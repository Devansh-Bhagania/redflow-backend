"""Schema validation and sandbox behavior for the generic node system:
inline AI_AGENT (agent-as-data, not AGENT_TYPES), TOOL, SEND_MESSAGE,
WAIT_FOR_REPLY, HUMAN_HANDOFF, and the context.nodes.<id>.output convention.
"""

import pytest

from app.workflows.sandbox import dry_run
from app.workflows.schema import WorkflowDefinition

TRIGGER = {"id": "t", "type": "TRIGGER", "config": {}}
END = {"id": "e", "type": "END", "config": {}}


def _wf(nodes, edges):
    return {"nodes": nodes, "edges": edges}


INLINE_AGENT = {
    "id": "discovery",
    "type": "AI_AGENT",
    "config": {
        "name": "Product Discovery",
        "system_prompt": "Help the customer find a product.",
        "tools": ["search_products", "check_inventory"],
        "memory": {"scope": "conversation", "window": 10},
        "output_mode": "structured",
        "output_schema": {"selected_product_id": "string|null"},
    },
}


# --- schema: inline AI_AGENT -------------------------------------------------


def test_inline_agent_is_valid():
    WorkflowDefinition.model_validate(_wf([TRIGGER, INLINE_AGENT, END], [{"source": "t", "target": "discovery"}, {"source": "discovery", "target": "e"}]))


def test_inline_agent_needs_system_prompt():
    node = {"id": "a", "type": "AI_AGENT", "config": {"name": "X", "tools": []}}
    with pytest.raises(ValueError, match="config.system_prompt"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_inline_agent_rejects_unknown_tool():
    node = {"id": "a", "type": "AI_AGENT", "config": {"name": "X", "system_prompt": "p", "tools": ["not_a_real_tool"]}}
    with pytest.raises(ValueError, match="unknown tool"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_inline_agent_cannot_reach_workflow_builder_tools():
    """propose_workflow_definition et al. require a workflow-pinned builder
    conversation — a conversational node must never be able to list them."""
    node = {"id": "a", "type": "AI_AGENT", "config": {"name": "X", "system_prompt": "p", "tools": ["propose_workflow_definition"]}}
    with pytest.raises(ValueError, match="unknown tool"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_inline_agent_and_preset_cannot_be_mixed():
    node = {"id": "a", "type": "AI_AGENT", "config": {"agent_type": "SALES", "system_prompt": "override"}}
    with pytest.raises(ValueError, match="cannot be combined with"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_preset_agent_type_still_works_unchanged():
    node = {"id": "a", "type": "AI_AGENT", "config": {"agent_type": "FOLLOWUP"}}
    WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_structured_output_needs_a_schema():
    node = {"id": "a", "type": "AI_AGENT", "config": {"name": "X", "system_prompt": "p", "output_mode": "structured"}}
    with pytest.raises(ValueError, match="output_schema"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


def test_bad_memory_scope_is_rejected():
    node = {"id": "a", "type": "AI_AGENT", "config": {"name": "X", "system_prompt": "p", "memory": {"scope": "eternal"}}}
    with pytest.raises(ValueError, match="memory.scope"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}]))


# --- schema: TOOL / SEND_MESSAGE / WAIT_FOR_REPLY / HUMAN_HANDOFF ----------


def test_tool_node_rejects_an_unregistered_tool():
    node = {"id": "s", "type": "TOOL", "config": {"tool": "delete_the_database"}}
    with pytest.raises(ValueError, match="not a registered tool"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "s"}, {"source": "s", "target": "e"}]))


def test_send_message_needs_text():
    node = {"id": "m", "type": "SEND_MESSAGE", "config": {}}
    with pytest.raises(ValueError, match="config.text"):
        WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "m"}, {"source": "m", "target": "e"}]))


def test_wait_for_reply_needs_no_config():
    node = {"id": "w", "type": "WAIT_FOR_REPLY", "config": {}}
    WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "w"}, {"source": "w", "target": "e"}]))


def test_parallel_and_database_are_not_accepted_node_types():
    """Deliberately unsupported — see schema.py's module docstring. Rejected
    at validation, not accepted and left to fail at runtime."""
    for bad_type in ("PARALLEL", "DATABASE", "IF"):
        node = {"id": "x", "type": bad_type, "config": {}}
        with pytest.raises(ValueError):
            WorkflowDefinition.model_validate(_wf([TRIGGER, node, END], [{"source": "t", "target": "x"}, {"source": "x", "target": "e"}]))


# --- sandbox: full conversational graph -------------------------------------


def test_conversational_flow_dry_runs_end_to_end():
    """Mirrors the target example: discovery agent -> tool -> send -> wait ->
    handoff -> end. Verifies the context.nodes convention threads through
    every new node type via the existing template engine, unmodified."""
    definition = _wf(
        [
            TRIGGER,
            INLINE_AGENT,
            {"id": "search", "type": "TOOL", "config": {"tool": "search_products", "input": {"query": "socks"}}},
            {"id": "msg", "type": "SEND_MESSAGE", "config": {"text": "Found: {{nodes.search.output}}"}},
            {"id": "wait", "type": "WAIT_FOR_REPLY", "config": {"timeout_seconds": 3600}},
            {"id": "handoff", "type": "HUMAN_HANDOFF", "config": {"message": "customer wants a human"}},
            END,
        ],
        [
            {"source": "t", "target": "discovery"},
            {"source": "discovery", "target": "search"},
            {"source": "search", "target": "msg"},
            {"source": "msg", "target": "wait"},
            {"source": "wait", "target": "handoff"},
            {"source": "handoff", "target": "e"},
        ],
    )
    result = dry_run(definition, decisions={"wait": "size L, quantity 200"})
    assert result["status"] == "COMPLETED", result["error"]
    node_types = [s["node_type"] for s in result["steps"]]
    assert node_types == ["TRIGGER", "AI_AGENT", "TOOL", "SEND_MESSAGE", "WAIT_FOR_REPLY", "HUMAN_HANDOFF", "END"]
    assert result["context"]["nodes"]["wait"]["output"] == {"reply": "size L, quantity 200"}


def test_conversational_loop_with_no_end_node_completes_instead_of_failing():
    """AI_AGENT -> SEND_MESSAGE -> WAIT_FOR_REPLY -> back to AI_AGENT, forever
    — the correct shape for a real chat flow, and previously misreported as
    FAILED (hit the step limit) because the sandbox had no notion of 'stop
    after a few simulated turns'."""
    definition = _wf(
        [
            TRIGGER,
            {"id": "agent", "type": "AI_AGENT", "config": {"name": "Bot", "system_prompt": "chat", "tools": []}},
            {"id": "reply", "type": "SEND_MESSAGE", "config": {"text": "{{nodes.agent.output.text}}"}},
            {"id": "wait", "type": "WAIT_FOR_REPLY", "config": {}},
        ],
        [
            {"source": "t", "target": "agent"},
            {"source": "agent", "target": "reply"},
            {"source": "reply", "target": "wait"},
            {"source": "wait", "target": "agent"},
        ],
    )
    result = dry_run(definition)
    assert result["status"] == "COMPLETED", result["error"]
    assert any("turn limit" in s["detail"] for s in result["steps"])


def test_send_message_renders_the_template_in_the_trace():
    definition = _wf(
        [TRIGGER, {"id": "m", "type": "SEND_MESSAGE", "config": {"text": "Hi {{customer_name}}"}}, END],
        [{"source": "t", "target": "m"}, {"source": "m", "target": "e"}],
    )
    result = dry_run(definition, context={"customer_name": "Sharma"})
    assert any("Hi Sharma" in s["detail"] for s in result["steps"])


def test_capabilities_expose_the_new_registries():
    from app.services.workflow_service import list_capabilities

    caps = list_capabilities()
    assert {"AI_AGENT", "TOOL", "SEND_MESSAGE", "WAIT_FOR_REPLY", "HUMAN_HANDOFF"} <= set(caps["node_types"])
    assert "search_products" in caps["tools"]
    assert "propose_workflow_definition" not in caps["tools"]
    assert caps["memory_scopes"] == ["conversation", "workflow_execution", "none"]
    assert caps["output_modes"] == ["text", "structured"]
