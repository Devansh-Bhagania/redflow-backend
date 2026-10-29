"""In-process dry run of a workflow definition.

Deliberately NOT Temporal. A sandbox run must be instant and must not touch
the outside world, and the real interpreter is neither: a DELAY node is a
genuine 24-hour sleep, an ACTION really sends WhatsApp messages to real
customers, and an AI_AGENT node costs tokens. This walker mirrors
graph_workflow._walk's control flow with every side effect replaced by a
recorded, faked step.

What it does test: reachability, branch wiring, loop termination, context
plumbing through SET/HTTP_REQUEST/LOOP, and whether the graph ends up
somewhere sensible. What it cannot test: whether an action actually works.

Branch decisions come from the caller (`decisions`, keyed by node id) so a
user can walk a specific path; anything unspecified takes the documented
default below.
"""

import copy

from app.workflows.context import render_template, resolve_path
from app.workflows.schema import MAX_LOOP_ITERATIONS, MAX_SUBWORKFLOW_DEPTH, WorkflowDefinition

# Mirrors graph_workflow.MAX_STEPS: a cyclic graph must stop, not hang the
# request thread.
MAX_STEPS = 200

# Unspecified branch defaults. Chosen so a bare "run it" exercises the
# interesting path rather than falling straight out of the graph: an
# unanswered quote (false) is why the follow-up workflow exists, and an
# approval that auto-rejects would leave most graphs untested.
DEFAULT_DECISIONS = {"CONDITION": "false", "HUMAN_APPROVAL": "approved"}

# A conversational workflow that loops "reply -> wait -> reply" back on itself
# with no END node is not a bug — it's meant to run until the conversation
# does, which for a dry run is unbounded. Treating that the same as a
# pathological cycle (MAX_STEPS -> FAILED) would flag every correctly-built
# chat loop as broken. Stopping cleanly after this many simulated customer
# turns instead reports COMPLETED with a note, which is what a bare "run it"
# on this shape of graph actually wants to see.
MAX_SANDBOX_WAIT_TURNS = 5


class SandboxRun:
    """Accumulates the trace. One instance per dry run, including nested
    sub-workflows so their steps appear inline in one readable timeline."""

    def __init__(self, decisions: dict[str, str], sub_definitions: dict[str, dict]):
        self.decisions = decisions
        self.sub_definitions = sub_definitions
        self.steps: list[dict] = []
        self.simulated_seconds = 0
        self.budget = MAX_STEPS
        self.wait_turns = 0

    def record(self, node_id: str, node_type: str, detail: str, *, branch: str | None = None, depth: int = 0) -> None:
        self.steps.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "detail": detail,
                "branch": branch,
                "depth": depth,
            }
        )


def dry_run(
    definition: dict,
    context: dict | None = None,
    decisions: dict[str, str] | None = None,
    sub_definitions: dict[str, dict] | None = None,
) -> dict:
    """Walks `definition` without side effects.

    Returns {status, steps, context, simulated_seconds, error}. `status` is
    COMPLETED or FAILED — a dry run never blocks, so the nodes that suspend a
    real run (HUMAN_APPROVAL, WEBHOOK) resolve from `decisions` or their
    default instead of reporting WAITING.
    """
    # Validate first: the sandbox is the natural place to catch a malformed
    # graph, and every walker below assumes the invariants the schema enforces.
    try:
        WorkflowDefinition.model_validate(definition)
    except Exception as exc:
        return {
            "status": "FAILED",
            "steps": [],
            "context": context or {},
            "simulated_seconds": 0,
            "error": f"Invalid workflow definition: {exc}",
        }

    run = SandboxRun(decisions or {}, sub_definitions or {})
    # Deep-copied: SET and HTTP_REQUEST nodes write into it, and a dry run must
    # not mutate the caller's dict.
    working_context = copy.deepcopy(context or {})

    try:
        status = _walk(run, definition, working_context, depth=0)
        error = None
    except _SandboxError as exc:
        status, error = "FAILED", str(exc)

    return {
        "status": status,
        "steps": run.steps,
        "context": working_context,
        "simulated_seconds": run.simulated_seconds,
        "error": error,
    }


class _SandboxError(Exception):
    pass


def _write_node_output(context: dict, node_id: str, output: dict) -> None:
    """Mirrors graph_workflow._write_node_output — same
    `{{nodes.<id>.output...}}` convention, so a dry-run trace exercises
    exactly the paths a real run would."""
    context.setdefault("nodes", {})[node_id] = {"output": output}


def _walk(run: SandboxRun, definition: dict, context: dict, depth: int) -> str:
    nodes = {n["id"]: n for n in definition["nodes"]}
    edges = definition["edges"]

    current_id = next((n["id"] for n in definition["nodes"] if n["type"] == "TRIGGER"), None)
    if current_id is None:
        raise _SandboxError("Workflow definition has no TRIGGER node")

    # node_id -> {"items": [...], "index": int}. Held per walk, so a
    # sub-workflow gets its own loop state rather than colliding with its
    # parent's.
    loop_frames: dict[str, dict] = {}

    while run.budget > 0:
        run.budget -= 1
        node = nodes.get(current_id)
        if node is None:
            raise _SandboxError(f"Edge points at an unknown node: {current_id}")

        node_type = node["type"]
        config = node.get("config", {})
        branch_key: str | None = None

        if node_type == "TRIGGER":
            run.record(node["id"], node_type, "Workflow triggered", depth=depth)

        elif node_type == "DELAY":
            seconds = config["duration_seconds"]
            run.simulated_seconds += seconds
            run.record(node["id"], node_type, f"Waited {_humanize(seconds)} (skipped)", depth=depth)

        elif node_type == "ACTION":
            run.record(node["id"], node_type, f"Would run action {config['action']!r}", depth=depth)

        elif node_type == "AI_AGENT":
            if "agent_type" in config:
                run.record(node["id"], node_type, f"Would run the {config['agent_type']} agent (sends its own reply)", depth=depth)
            else:
                output_mode = config.get("output_mode", "text")
                if output_mode == "structured":
                    # Every declared field, fabricated as None — realistic
                    # enough that a SWITCH one edge downstream lands on a
                    # deterministic "default" branch rather than crashing the
                    # dry run on a missing key.
                    simulated = {k: None for k in config.get("output_schema", {})}
                else:
                    simulated = None
                _write_node_output(context, node["id"], {"sandbox": True, "text": "(simulated reply)", "structured_output": simulated})
                run.record(node["id"], node_type, f"Would run inline agent {config['name']!r} with tools {config.get('tools', [])}", depth=depth)

        elif node_type == "TOOL":
            _write_node_output(context, node["id"], {"sandbox": True})
            run.record(node["id"], node_type, f"Would call tool {config['tool']!r}", depth=depth)

        elif node_type == "SEND_MESSAGE":
            text = render_template(config["text"], context)
            _write_node_output(context, node["id"], {"sandbox": True, "sent": False, "text": text})
            run.record(node["id"], node_type, f"Would send: {text!r}", depth=depth)

        elif node_type == "WAIT_FOR_REPLY":
            run.wait_turns += 1
            if run.wait_turns > MAX_SANDBOX_WAIT_TURNS:
                run.record(
                    node["id"], node_type,
                    f"Reached the sandbox's {MAX_SANDBOX_WAIT_TURNS}-turn limit for simulated replies — stopping "
                    "here. A real conversation keeps waiting for the customer indefinitely; this isn't an error.",
                    depth=depth,
                )
                return "COMPLETED"
            simulated_reply = run.decisions.get(node["id"], "(simulated customer reply)")
            _write_node_output(context, node["id"], {"reply": simulated_reply})
            run.record(node["id"], node_type, f"Would wait for a reply -> {simulated_reply!r} (simulated)", depth=depth)

        elif node_type == "HUMAN_HANDOFF":
            _write_node_output(context, node["id"], {"resumed": True, "sandbox": True})
            run.record(node["id"], node_type, "Would hand off to a human (simulated as resumed immediately)", depth=depth)

        elif node_type == "SET":
            rendered = render_template(config["values"], context)
            context.update(rendered)
            run.record(node["id"], node_type, f"Set {', '.join(rendered)}", depth=depth)

        elif node_type == "HTTP_REQUEST":
            method = str(config.get("method", "GET")).upper()
            url = render_template(config["url"], context)
            save_as = config.get("save_as")
            if save_as:
                # A faked response, clearly labelled. Downstream nodes get
                # *something* to reference so the rest of the graph still
                # walks, but nothing here should be mistaken for real data.
                context[save_as] = {"sandbox": True, "status_code": 200, "body": {}}
            run.record(
                node["id"], node_type,
                f"Would {method} {url}" + (f", saving to {save_as!r}" if save_as else ""),
                depth=depth,
            )

        elif node_type == "CONDITION":
            branch_key = run.decisions.get(node["id"], DEFAULT_DECISIONS["CONDITION"])
            run.record(
                node["id"], node_type,
                f"Check {config['check']!r} -> {branch_key}",
                branch=branch_key, depth=depth,
            )

        elif node_type == "SWITCH":
            value = run.decisions.get(node["id"])
            if value is None:
                resolved = resolve_path(context, config["on"])
                cases = [str(c) for c in config["cases"]]
                value = str(resolved) if str(resolved) in cases else "default"
            branch_key = value
            run.record(
                node["id"], node_type,
                f"Switch on {config['on']} -> {branch_key}",
                branch=branch_key, depth=depth,
            )

        elif node_type == "HUMAN_APPROVAL":
            branch_key = run.decisions.get(node["id"], DEFAULT_DECISIONS["HUMAN_APPROVAL"])
            run.record(
                node["id"], node_type,
                f"{config['prompt']} -> {branch_key} (simulated)",
                branch=branch_key, depth=depth,
            )

        elif node_type == "WEBHOOK":
            # No decision to make, but the real run genuinely blocks here.
            # Saying so is more useful than silently continuing.
            context[config["save_as"]] = {"sandbox": True, "payload": {}}
            run.record(
                node["id"], node_type,
                f"Would wait for an inbound callback, saving to {config['save_as']!r} (skipped)",
                depth=depth,
            )

        elif node_type == "LOOP":
            branch_key = _step_loop(run, node, config, context, loop_frames, depth)

        elif node_type == "SUB_WORKFLOW":
            _run_sub_workflow(run, node, config, context, depth)

        elif node_type == "END":
            run.record(node["id"], node_type, "Workflow ended", depth=depth)
            return "COMPLETED"

        next_edges = [
            e for e in edges
            if e["source"] == current_id and (branch_key is None or e.get("condition") in (None, branch_key))
        ]
        if not next_edges:
            if branch_key is not None:
                # A branch with nowhere to go is almost always a wiring
                # mistake, and it is exactly what a dry run exists to catch.
                run.record(
                    node["id"], node_type,
                    f"No outgoing edge for branch {branch_key!r} — run stops here",
                    branch=branch_key, depth=depth,
                )
            return "COMPLETED"
        current_id = next_edges[0]["target"]

    raise _SandboxError(f"Hit the {MAX_STEPS}-step limit — the graph is probably cyclic")


def _step_loop(
    run: SandboxRun, node: dict, config: dict, context: dict, frames: dict[str, dict], depth: int
) -> str:
    """Advances (or opens) this LOOP's frame and returns 'body' or 'done'.

    Mirrors graph_workflow's loop handling: the node is re-entered once per
    iteration, so the walker stays a single cursor rather than needing a
    recursive sub-graph evaluator.
    """
    frame = frames.get(node["id"])
    if frame is None:
        items = resolve_path(context, config["over"])
        if items is None:
            items = []
        if not isinstance(items, list):
            raise _SandboxError(
                f"LOOP node {node['id']}: config.over ({config['over']!r}) is not a list in the run context"
            )
        limit = config.get("max_iterations", MAX_LOOP_ITERATIONS)
        frame = {"items": items[:limit], "index": 0, "truncated": len(items) > limit}
        frames[node["id"]] = frame
        if frame["truncated"]:
            run.record(
                node["id"], "LOOP",
                f"{len(items)} items exceeds max_iterations={limit}; only the first {limit} will run",
                depth=depth,
            )

    if frame["index"] >= len(frame["items"]):
        frames.pop(node["id"], None)
        context.pop(config["as"], None)
        run.record(node["id"], "LOOP", f"Finished after {len(frame['items'])} iteration(s)", branch="done", depth=depth)
        return "done"

    context[config["as"]] = frame["items"][frame["index"]]
    frame["index"] += 1
    run.record(
        node["id"], "LOOP",
        f"Iteration {frame['index']} of {len(frame['items'])}",
        branch="body", depth=depth,
    )
    return "body"


def _run_sub_workflow(run: SandboxRun, node: dict, config: dict, context: dict, depth: int) -> None:
    workflow_id = str(config["workflow_id"])
    if depth >= MAX_SUBWORKFLOW_DEPTH:
        raise _SandboxError(
            f"SUB_WORKFLOW node {node['id']}: nesting deeper than {MAX_SUBWORKFLOW_DEPTH} levels"
        )

    sub_definition = run.sub_definitions.get(workflow_id)
    if sub_definition is None:
        # The caller resolves sub-definitions up front (the sandbox does no
        # IO). An unresolved id means the referenced workflow is missing or
        # belongs to another org — worth saying, not worth failing the run.
        run.record(
            node["id"], "SUB_WORKFLOW",
            f"Sub-workflow {workflow_id} could not be loaded — skipped",
            depth=depth,
        )
        return

    run.record(node["id"], "SUB_WORKFLOW", f"Entering sub-workflow {workflow_id}", depth=depth)
    status = _walk(run, sub_definition, context, depth=depth + 1)
    run.record(node["id"], "SUB_WORKFLOW", f"Sub-workflow finished ({status})", depth=depth)


def _humanize(seconds: int) -> str:
    if seconds >= 86400 and seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def collect_subworkflow_ids(definition: dict) -> list[str]:
    """Ids a caller must resolve and pass in as `sub_definitions`."""
    return [
        str(n["config"]["workflow_id"])
        for n in definition.get("nodes", [])
        if n.get("type") == "SUB_WORKFLOW" and n.get("config", {}).get("workflow_id")
    ]
