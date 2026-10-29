"""Structured, validated workflow definitions.

Node types:
  TRIGGER        entry point, exactly one per graph
  ACTION         a named side effect from activities.ACTIONS
  CONDITION      two-way branch on a named check; edges labelled true/false
                 (this is also "IF" — one boolean-branch primitive, not two)
  SWITCH         n-way branch on a context value; edges labelled per case
  DELAY          durable sleep
  SET            writes computed values into the run context
  HTTP_REQUEST   calls an external API and saves the response into context
  HUMAN_APPROVAL pauses until someone approves/rejects; edges labelled
                 approved/rejected
  WEBHOOK        pauses until an external system posts a payload back
  LOOP           iterates a context list; edges labelled body/done
  SUB_WORKFLOW   runs another workflow as a single step
  AI_AGENT       an LLM turn — either a built-in preset (config.agent_type) or
                 a fully inline agent (name/system_prompt/tools/model/...);
                 see _validate_ai_agent_config. Writes its result to
                 context.nodes.<node_id>.output and does NOT send anything —
                 that's SEND_MESSAGE's job.
  TOOL           calls one named tool from the shared registry directly, no
                 LLM involved
  SEND_MESSAGE   sends templated text out over the conversation's channel
  WAIT_FOR_REPLY pauses until the customer sends the next message
  HUMAN_HANDOFF  pauses until a human resolves the conversation
  END            terminates the run

Deliberately NOT included, and not silently half-supported: PARALLEL (needs a
concurrency-safe context-merge/join design of its own) and DATABASE (a
query-injection surface that deserves its own review). Both are rejected at
validation, not accepted and left to fail at runtime.

AI-generated or user-submitted workflow JSON must always pass through
WorkflowDefinition before it is stored or executed — never run unvalidated
graph JSON.
"""

from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

NodeType = Literal[
    "TRIGGER",
    "ACTION",
    "CONDITION",
    "SWITCH",
    "DELAY",
    "SET",
    "HTTP_REQUEST",
    "HUMAN_APPROVAL",
    "WEBHOOK",
    "LOOP",
    "SUB_WORKFLOW",
    "AI_AGENT",
    "TOOL",
    "SEND_MESSAGE",
    "WAIT_FOR_REPLY",
    "HUMAN_HANDOFF",
    "END",
]

# Kept as a plain tuple (not imported from app.agents.runtime) to avoid a
# circular import: agents.runtime -> agents.tool_executor ->
# services.workflow_service -> this module.
AGENT_TYPES = ("SALES", "SUPPORT", "REORDERING", "FOLLOWUP", "INVENTORY", "COLLECTIONS", "COMPANY")

# AI_AGENT config.memory.scope. "conversation": full (optionally windowed)
# message history, same as every legacy preset agent uses today.
# "workflow_execution": no prior chat history — only this run's context is
# visible, as a synthetic turn. "none": no history and no context, just the
# system prompt. Kept out of legacy behavior entirely: a preset AI_AGENT node
# (config.agent_type) always uses "conversation", unaffected by this.
MEMORY_SCOPES = ("conversation", "workflow_execution", "none")
OUTPUT_MODES = ("text", "structured")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# A LOOP that iterates an attacker- or agent-supplied list needs a ceiling that
# does not depend on the list being sane. MAX_STEPS in graph_workflow.py is the
# backstop for the whole run; this bounds a single loop node.
MAX_LOOP_ITERATIONS = 1000

# SUB_WORKFLOW nesting depth. Guards against a workflow that (directly or via a
# cycle of sub-workflows) calls itself forever.
MAX_SUBWORKFLOW_DEPTH = 3

# Node types that suspend the run until something external resumes it.
WAITING_NODE_TYPES = ("HUMAN_APPROVAL", "WEBHOOK", "WAIT_FOR_REPLY", "HUMAN_HANDOFF")

# The branch labels each branching node type may put on its outgoing edges.
BRANCH_LABELS: dict[str, tuple[str, ...]] = {
    "CONDITION": ("true", "false"),
    "HUMAN_APPROVAL": ("approved", "rejected"),
    "LOOP": ("body", "done"),
}


class WorkflowNode(BaseModel):
    id: str
    type: NodeType
    config: dict = {}


class WorkflowEdge(BaseModel):
    source: str
    target: str
    # Branch label. CONDITION: "true"/"false". HUMAN_APPROVAL:
    # "approved"/"rejected". LOOP: "body"/"done". SWITCH: one of its case
    # values, or "default". Omitted for unconditional edges.
    condition: str | None = None


class WorkflowDefinition(BaseModel):
    nodes: list[WorkflowNode]
    edges: list[WorkflowEdge]

    @field_validator("nodes")
    @classmethod
    def _at_least_one_node(cls, nodes: list[WorkflowNode]) -> list[WorkflowNode]:
        if not nodes:
            raise ValueError("A workflow needs at least one node")
        return nodes

    @model_validator(mode="after")
    def _validate_graph(self) -> "WorkflowDefinition":
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Node ids must be unique")

        triggers = [n for n in self.nodes if n.type == "TRIGGER"]
        if len(triggers) != 1:
            raise ValueError("A workflow must have exactly one TRIGGER node")

        for edge in self.edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError(f"Edge references an unknown node: {edge.source} -> {edge.target}")

        by_id = {n.id: n for n in self.nodes}
        for node in self.nodes:
            _validate_node_config(node)

        # A branch label only means something on the node type that defines it;
        # a "true" edge leaving a DELAY silently never matches, which reads as a
        # workflow that mysteriously stops.
        for edge in self.edges:
            if edge.condition is None:
                continue
            source = by_id[edge.source]
            allowed = BRANCH_LABELS.get(source.type)
            if allowed is not None and edge.condition not in allowed:
                raise ValueError(
                    f"Edge {edge.source} -> {edge.target} is labelled {edge.condition!r}, "
                    f"but a {source.type} node only branches on {allowed}"
                )
            if source.type == "SWITCH":
                cases = [str(c) for c in source.config.get("cases", [])]
                if edge.condition not in cases and edge.condition != "default":
                    raise ValueError(
                        f"Edge {edge.source} -> {edge.target} is labelled {edge.condition!r}, "
                        f"which is not one of the SWITCH node's cases {cases} (or 'default')"
                    )
            if allowed is None and source.type != "SWITCH":
                raise ValueError(f"A {source.type} node's outgoing edges cannot be labelled")

        # A LOOP with no body edge spins on itself; catching it here beats
        # discovering it as a run that burns the whole step budget.
        for node in self.nodes:
            if node.type != "LOOP":
                continue
            labels = {e.condition for e in self.edges if e.source == node.id}
            if "body" not in labels:
                raise ValueError(f"LOOP node {node.id} needs an outgoing edge labelled 'body'")

        return self


def _validate_node_config(node: WorkflowNode) -> None:
    config = node.config
    node_type = node.type

    if node_type == "DELAY":
        duration = config.get("duration_seconds")
        if not isinstance(duration, int) or isinstance(duration, bool) or duration < 0:
            raise ValueError(f"DELAY node {node.id} needs config.duration_seconds as a non-negative integer")

    elif node_type == "ACTION":
        if "action" not in config:
            raise ValueError(f"ACTION node {node.id} needs config.action")

    elif node_type == "CONDITION":
        if "check" not in config:
            raise ValueError(f"CONDITION node {node.id} needs config.check")

    elif node_type == "AI_AGENT":
        _validate_ai_agent_config(node, config)

    elif node_type == "TOOL":
        tool_name = config.get("tool")
        if not tool_name:
            raise ValueError(f"TOOL node {node.id} needs config.tool")
        if tool_name not in _known_tool_names():
            raise ValueError(f"TOOL node {node.id}: {tool_name!r} is not a registered tool")
        if "input" in config and not isinstance(config["input"], dict):
            raise ValueError(f"TOOL node {node.id}: config.input must be an object")

    elif node_type == "SEND_MESSAGE":
        if not config.get("text"):
            raise ValueError(f"SEND_MESSAGE node {node.id} needs config.text")

    elif node_type == "WAIT_FOR_REPLY":
        _validate_optional_timeout(node, config)

    elif node_type == "HUMAN_HANDOFF":
        if "message" in config and not isinstance(config["message"], str):
            raise ValueError(f"HUMAN_HANDOFF node {node.id}: config.message must be a string")
        _validate_optional_timeout(node, config)

    elif node_type == "SWITCH":
        if not config.get("on"):
            raise ValueError(f"SWITCH node {node.id} needs config.on (a context path to switch on)")
        cases = config.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError(f"SWITCH node {node.id} needs a non-empty config.cases list")
        if len(set(map(str, cases))) != len(cases):
            raise ValueError(f"SWITCH node {node.id} has duplicate values in config.cases")

    elif node_type == "SET":
        values = config.get("values")
        if not isinstance(values, dict) or not values:
            raise ValueError(f"SET node {node.id} needs a non-empty config.values object")
        if any(not isinstance(k, str) or not k for k in values):
            raise ValueError(f"SET node {node.id} has a non-string key in config.values")

    elif node_type == "HTTP_REQUEST":
        method = str(config.get("method", "GET")).upper()
        if method not in HTTP_METHODS:
            raise ValueError(f"HTTP_REQUEST node {node.id} needs config.method to be one of {HTTP_METHODS}")
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"HTTP_REQUEST node {node.id} needs config.url")
        # The scheme is checked again at call time against the fully rendered
        # url; this catches the obvious mistake at save time instead of at 3am.
        if not url.startswith(("http://", "https://", "{{")):
            raise ValueError(f"HTTP_REQUEST node {node.id}: config.url must be an http(s) URL")
        headers = config.get("headers", {})
        if not isinstance(headers, dict):
            raise ValueError(f"HTTP_REQUEST node {node.id}: config.headers must be an object")

    elif node_type == "HUMAN_APPROVAL":
        if not config.get("prompt"):
            raise ValueError(f"HUMAN_APPROVAL node {node.id} needs config.prompt (what the approver is deciding)")
        _validate_optional_timeout(node, config)

    elif node_type == "WEBHOOK":
        if not config.get("save_as"):
            raise ValueError(f"WEBHOOK node {node.id} needs config.save_as (context key for the payload)")
        _validate_optional_timeout(node, config)

    elif node_type == "LOOP":
        if not config.get("over"):
            raise ValueError(f"LOOP node {node.id} needs config.over (a context path to a list)")
        if not config.get("as"):
            raise ValueError(f"LOOP node {node.id} needs config.as (context key for the current item)")
        limit = config.get("max_iterations", MAX_LOOP_ITERATIONS)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LOOP_ITERATIONS:
            raise ValueError(
                f"LOOP node {node.id}: config.max_iterations must be an integer between 1 and {MAX_LOOP_ITERATIONS}"
            )

    elif node_type == "SUB_WORKFLOW":
        if not config.get("workflow_id"):
            raise ValueError(f"SUB_WORKFLOW node {node.id} needs config.workflow_id")


def _known_tool_names() -> set[str]:
    # Deferred import: app.agents.tool_executor imports app.services.workflow_service
    # (for the propose_workflow_definition tool), which imports this module —
    # importing DISPATCH at module load time here would close that cycle.
    from app.agents.tool_executor import DISPATCH
    from app.agents.tools import WORKFLOW_BUILDER_ONLY_TOOL_NAMES

    return set(DISPATCH) - WORKFLOW_BUILDER_ONLY_TOOL_NAMES


def _validate_ai_agent_config(node: WorkflowNode, config: dict) -> None:
    if "agent_type" in config:
        # Legacy preset: config.agent_type is the only field, unchanged from
        # before inline agents existed. A node can't mix the two forms — that
        # would leave it ambiguous which prompt/tools/model actually run.
        if config["agent_type"] not in AGENT_TYPES:
            raise ValueError(f"AI_AGENT node {node.id} needs config.agent_type to be one of {AGENT_TYPES}")
        extra = set(config) - {"agent_type"}
        if extra:
            raise ValueError(
                f"AI_AGENT node {node.id}: config.agent_type is a preset and cannot be combined with {sorted(extra)}"
            )
        return

    # Inline agent: the node configuration is authoritative, no Python lookup.
    if not config.get("name"):
        raise ValueError(f"AI_AGENT node {node.id} needs config.name (or config.agent_type for a preset)")
    if not config.get("system_prompt"):
        raise ValueError(f"AI_AGENT node {node.id} needs config.system_prompt")

    for field in ("provider", "model"):
        if field in config and not isinstance(config[field], str):
            raise ValueError(f"AI_AGENT node {node.id}: config.{field} must be a string")

    tools = config.get("tools", [])
    if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools):
        raise ValueError(f"AI_AGENT node {node.id}: config.tools must be a list of tool names")
    unknown = set(tools) - _known_tool_names()
    if unknown:
        raise ValueError(f"AI_AGENT node {node.id}: unknown tool(s) {sorted(unknown)}")

    memory = config.get("memory", {"scope": "conversation"})
    if not isinstance(memory, dict) or memory.get("scope", "conversation") not in MEMORY_SCOPES:
        raise ValueError(f"AI_AGENT node {node.id}: config.memory.scope must be one of {MEMORY_SCOPES}")
    window = memory.get("window")
    if window is not None and (not isinstance(window, int) or isinstance(window, bool) or window < 1):
        raise ValueError(f"AI_AGENT node {node.id}: config.memory.window must be a positive integer")

    output_mode = config.get("output_mode", "text")
    if output_mode not in OUTPUT_MODES:
        raise ValueError(f"AI_AGENT node {node.id}: config.output_mode must be one of {OUTPUT_MODES}")
    if output_mode == "structured":
        schema = config.get("output_schema")
        if not isinstance(schema, dict) or not schema:
            raise ValueError(f"AI_AGENT node {node.id}: output_mode 'structured' needs a non-empty config.output_schema")


def _validate_optional_timeout(node: WorkflowNode, config: dict) -> None:
    if "timeout_seconds" not in config:
        return
    timeout = config["timeout_seconds"]
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError(f"{node.type} node {node.id}: config.timeout_seconds must be a positive integer")
