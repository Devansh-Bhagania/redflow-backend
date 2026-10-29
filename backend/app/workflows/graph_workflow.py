from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.workflows.activities import (
        evaluate_condition,
        execute_action,
        http_request,
        load_sub_workflow,
        mark_execution_complete,
        record_step,
        request_human_approval,
        request_human_handoff,
        run_ai_agent_node,
        run_inline_ai_agent_node,
        run_tool_node,
        send_message_node,
    )
    from app.workflows.context import render_template, resolve_path
    from app.workflows.schema import MAX_LOOP_ITERATIONS, MAX_SUBWORKFLOW_DEPTH

ACTIVITY_TIMEOUT = timedelta(seconds=30)
AI_AGENT_TIMEOUT = timedelta(seconds=90)  # the agent may make several tool-calling round trips to the model
HTTP_TIMEOUT = timedelta(seconds=60)  # activity-level; the request itself has its own shorter timeout
MAX_STEPS = 200  # guards against a malformed graph looping forever

# How long a HUMAN_APPROVAL / WEBHOOK node waits before giving up, when the
# node doesn't set config.timeout_seconds. Seven days: long enough to survive
# a weekend, short enough that a forgotten run doesn't sit in Temporal forever.
DEFAULT_WAIT_SECONDS = 7 * 24 * 3600

# Without an explicit policy Temporal retries an activity forever. That's the
# right behaviour for a transient outage (the DB or the model provider being
# briefly unreachable) but not for a deterministic bug — an unknown action
# name or a malformed context will fail identically on every attempt, so those
# are listed as non-retryable and surface as a FAILED execution instead of an
# invisible infinite retry loop.
NON_RETRYABLE = ["ValueError", "KeyError", "PermissionError", "TypeError"]
ACTIVITY_RETRY = RetryPolicy(maximum_attempts=5, non_retryable_error_types=NON_RETRYABLE)
# The agent already retries internally and a run costs real tokens, so give it
# fewer attempts than a cheap bookkeeping activity.
AI_AGENT_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=NON_RETRYABLE)
# An outbound call to somebody else's API fails for their reasons; retry a
# couple of times but don't hammer them.
HTTP_RETRY = RetryPolicy(maximum_attempts=3, non_retryable_error_types=NON_RETRYABLE)


def _write_node_output(context: dict, node_id: str, output: dict) -> None:
    """Every node whose result matters downstream writes here — one
    convention (`{{nodes.<id>.output.<field>}}`) for AI_AGENT, TOOL,
    SEND_MESSAGE, WAIT_FOR_REPLY and HUMAN_HANDOFF alike, resolved by the
    existing dotted-path template engine with no changes to it. SET and
    HTTP_REQUEST keep their own top-level config.save_as convention
    unchanged — this is additive, not a replacement."""
    context.setdefault("nodes", {})[node_id] = {"output": output}


@workflow.defn
class GraphWorkflow:
    """Interprets a validated node graph (see schema.py) durably.

    DELAY nodes can be days long, and HUMAN_APPROVAL/WEBHOOK nodes park the
    run until someone signals it — that's the entire point of running this on
    Temporal rather than as a plain async function: the process can restart
    mid-workflow and the wait resumes exactly where it left off.
    """

    def __init__(self) -> None:
        # node_id -> the value that arrived. Keyed by node so two approval
        # nodes in one graph can't be satisfied by a single signal.
        self._resumes: dict[str, dict] = {}
        # Initialised here, not in run(): a query can arrive before the
        # workflow method has started executing.
        self._waiting: list[str] = []

    @workflow.signal
    def resume(self, node_id: str, payload: dict) -> None:
        """Unparks a HUMAN_APPROVAL or WEBHOOK node.

        For approvals the payload carries {"decision": "approved"|"rejected"};
        for webhooks it's the posted body.
        """
        self._resumes[node_id] = payload

    @workflow.query
    def waiting_for(self) -> list[str]:
        """Node ids this run is currently parked on. Lets the API show what a
        run is blocked on without reading Temporal history."""
        return list(self._waiting)

    @workflow.run
    async def run(self, run_input: dict) -> dict:
        execution_id = run_input["execution_id"]
        definition = run_input["definition"]
        context = run_input["context"]
        depth = run_input.get("depth", 0)

        try:
            status = await self._walk(execution_id, definition, context, depth)
        except Exception:
            # Without this the WorkflowExecution row stays RUNNING forever
            # whenever a node fails — the run looks in-flight in the UI long
            # after Temporal has given up on it.
            await workflow.execute_activity(
                mark_execution_complete, args=[execution_id, "FAILED"],
                start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
            )
            raise

        await workflow.execute_activity(
            mark_execution_complete, args=[execution_id, status],
            start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
        )
        return {"status": status, "context": context}

    async def _walk(self, execution_id: str, definition: dict, context: dict, depth: int) -> str:
        nodes = {n["id"]: n for n in definition["nodes"]}
        edges = definition["edges"]

        current_id = next((n["id"] for n in definition["nodes"] if n["type"] == "TRIGGER"), None)
        if current_id is None:
            # A bare `next(...)` raises StopIteration here, which inside a
            # workflow surfaces as a confusing task failure that retries
            # forever rather than a failed run with a readable reason.
            raise ApplicationError("Workflow definition has no TRIGGER node", non_retryable=True)

        # node_id -> {"items": [...], "index": int}. A LOOP node is re-entered
        # once per iteration, which keeps the walker a single cursor instead of
        # a recursive sub-graph evaluator.
        loop_frames: dict[str, dict] = {}

        for _ in range(MAX_STEPS):
            node = nodes.get(current_id)
            if node is None:
                raise ApplicationError(f"Edge points at an unknown node: {current_id}", non_retryable=True)
            node_type = node["type"]
            config = node.get("config", {})
            branch_key = None

            if node_type == "DELAY":
                # Same resume signal HUMAN_APPROVAL/WEBHOOK use, so the
                # existing POST /executions/{id}/resume endpoint doubles as a
                # "skip this wait" control — needed in practice for testing a
                # workflow with a 24h+ delay without actually waiting 24h.
                await self._await_resume(node["id"], config | {"timeout_seconds": config["duration_seconds"]})

            elif node_type == "ACTION":
                await workflow.execute_activity(
                    execute_action, args=[config["action"], context],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )

            elif node_type == "CONDITION":
                result = await workflow.execute_activity(
                    evaluate_condition, args=[config["check"], context],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                branch_key = "true" if result else "false"

            elif node_type == "SWITCH":
                resolved = resolve_path(context, config["on"])
                cases = [str(c) for c in config["cases"]]
                branch_key = str(resolved) if str(resolved) in cases else "default"

            elif node_type == "SET":
                # Pure, deterministic, no IO — safe to run in workflow code
                # rather than paying for an activity round trip.
                context.update(render_template(config["values"], context))

            elif node_type == "HTTP_REQUEST":
                response = await workflow.execute_activity(
                    http_request, args=[config, context],
                    start_to_close_timeout=HTTP_TIMEOUT, retry_policy=HTTP_RETRY,
                )
                if config.get("save_as"):
                    context[config["save_as"]] = response

            elif node_type == "AI_AGENT":
                if "agent_type" in config:
                    # Legacy preset — unchanged behavior: sends its reply
                    # directly and returns nothing to the graph.
                    await workflow.execute_activity(
                        run_ai_agent_node, args=[config["agent_type"], context],
                        start_to_close_timeout=AI_AGENT_TIMEOUT, retry_policy=AI_AGENT_RETRY,
                    )
                else:
                    result = await workflow.execute_activity(
                        run_inline_ai_agent_node, args=[node["id"], config, context],
                        start_to_close_timeout=AI_AGENT_TIMEOUT, retry_policy=AI_AGENT_RETRY,
                    )
                    _write_node_output(context, node["id"], result)

            elif node_type == "TOOL":
                result = await workflow.execute_activity(
                    run_tool_node, args=[node["id"], config["tool"], config.get("input", {}), context],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                _write_node_output(context, node["id"], result)

            elif node_type == "SEND_MESSAGE":
                result = await workflow.execute_activity(
                    send_message_node, args=[config["text"], context],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                _write_node_output(context, node["id"], result)

            elif node_type == "WAIT_FOR_REPLY":
                payload = await self._await_resume(node["id"], config)
                _write_node_output(context, node["id"], payload or {"reply": None, "timed_out": True})

            elif node_type == "HUMAN_HANDOFF":
                await workflow.execute_activity(
                    request_human_handoff, args=[execution_id, node["id"], context, config.get("message")],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                payload = await self._await_resume(node["id"], config)
                _write_node_output(context, node["id"], payload or {"resumed": False, "timed_out": True})

            elif node_type == "HUMAN_APPROVAL":
                await workflow.execute_activity(
                    request_human_approval, args=[execution_id, node["id"], config["prompt"]],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                payload = await self._await_resume(node["id"], config)
                # A timed-out approval is a rejection, not a crash: the whole
                # point of the node is that nobody said yes.
                branch_key = "approved" if (payload or {}).get("decision") == "approved" else "rejected"

            elif node_type == "WEBHOOK":
                payload = await self._await_resume(node["id"], config)
                context[config["save_as"]] = payload or {}

            elif node_type == "LOOP":
                branch_key = self._step_loop(node, config, context, loop_frames)

            elif node_type == "SUB_WORKFLOW":
                await self._run_sub_workflow(execution_id, node, config, context, depth)

            elif node_type == "END":
                await workflow.execute_activity(
                    record_step, args=[execution_id, node["id"], node_type, "COMPLETED"],
                    start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
                )
                return "COMPLETED"

            await workflow.execute_activity(
                record_step, args=[execution_id, node["id"], node_type, "COMPLETED"],
                start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
            )

            # A branching node's edges are labelled; an unlabelled edge out of
            # any other node type matches unconditionally.
            next_edges = [
                e for e in edges
                if e["source"] == current_id and (branch_key is None or e.get("condition") in (None, branch_key))
            ]
            if not next_edges:
                return "COMPLETED"
            current_id = next_edges[0]["target"]

        # Ran out of step budget: a cyclic or pathological graph. Reporting
        # COMPLETED here (the old behaviour) hid genuinely broken workflows.
        workflow.logger.warning("Workflow execution %s hit the %d-step limit", execution_id, MAX_STEPS)
        return "FAILED"

    async def _await_resume(self, node_id: str, config: dict) -> dict | None:
        """Parks until `resume` arrives for this node, or the timeout expires."""
        timeout = timedelta(seconds=config.get("timeout_seconds", DEFAULT_WAIT_SECONDS))
        self._waiting.append(node_id)
        try:
            await workflow.wait_condition(lambda: node_id in self._resumes, timeout=timeout)
        except TimeoutError:
            return None
        finally:
            self._waiting.remove(node_id)
        # Popped so a second visit to the same node (inside a LOOP) waits for
        # its own signal instead of reusing the previous one.
        return self._resumes.pop(node_id)

    def _step_loop(self, node: dict, config: dict, context: dict, frames: dict[str, dict]) -> str:
        frame = frames.get(node["id"])
        if frame is None:
            items = resolve_path(context, config["over"]) or []
            if not isinstance(items, list):
                raise ApplicationError(
                    f"LOOP node {node['id']}: config.over ({config['over']!r}) is not a list", non_retryable=True
                )
            limit = config.get("max_iterations", MAX_LOOP_ITERATIONS)
            frame = {"items": items[:limit], "index": 0}
            frames[node["id"]] = frame

        if frame["index"] >= len(frame["items"]):
            frames.pop(node["id"], None)
            context.pop(config["as"], None)
            return "done"

        context[config["as"]] = frame["items"][frame["index"]]
        frame["index"] += 1
        return "body"

    async def _run_sub_workflow(self, execution_id: str, node: dict, config: dict, context: dict, depth: int) -> None:
        if depth >= MAX_SUBWORKFLOW_DEPTH:
            raise ApplicationError(
                f"SUB_WORKFLOW node {node['id']}: nesting deeper than {MAX_SUBWORKFLOW_DEPTH} levels",
                non_retryable=True,
            )

        sub_definition = await workflow.execute_activity(
            load_sub_workflow, args=[context["organization_id"], str(config["workflow_id"])],
            start_to_close_timeout=ACTIVITY_TIMEOUT, retry_policy=ACTIVITY_RETRY,
        )
        if sub_definition is None:
            raise ApplicationError(
                f"SUB_WORKFLOW node {node['id']}: workflow {config['workflow_id']} not found in this organization",
                non_retryable=True,
            )

        result = await workflow.execute_child_workflow(
            GraphWorkflow.run,
            {
                "execution_id": execution_id,
                "definition": sub_definition,
                "context": context,
                "depth": depth + 1,
            },
            id=f"{workflow.info().workflow_id}-sub-{node['id']}",
        )
        # The child gets a copy of the context; merging its result back is what
        # makes a sub-workflow useful as a step rather than a dead end.
        if config.get("save_as"):
            context[config["save_as"]] = result.get("context", {})
        else:
            context.update(result.get("context", {}))
