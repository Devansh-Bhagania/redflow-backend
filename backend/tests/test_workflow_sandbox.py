"""Sandbox runner and the node types added alongside it.

The sandbox is a second interpreter mirroring graph_workflow._walk, so these
tests double as the spec for the control flow both walkers must agree on.
"""

import pytest

from app.workflows.context import render_template, resolve_path
from app.workflows.sandbox import MAX_STEPS, dry_run
from app.workflows.schema import WorkflowDefinition


def _wf(nodes, edges):
    return {"nodes": nodes, "edges": edges}


TRIGGER = {"id": "t", "type": "TRIGGER", "config": {}}
END = {"id": "e", "type": "END", "config": {}}


# --- context helpers -------------------------------------------------------


def test_resolve_path_walks_dicts_and_lists():
    context = {"order": {"items": [{"sku": "A1"}, {"sku": "B2"}]}}
    assert resolve_path(context, "order.items[1].sku") == "B2"
    assert resolve_path(context, "order.missing") is None
    assert resolve_path(context, "order.items[9].sku", default="?") == "?"


def test_render_template_preserves_type_for_a_whole_placeholder():
    context = {"count": 3, "flag": True, "name": "Ada"}
    # A lone placeholder keeps the original type so JSON bodies send numbers.
    assert render_template("{{count}}", context) == 3
    # Mixed text always renders as a string, and booleans render lowercase for
    # the JSON/URL consumers on the other end.
    assert render_template("n={{count}} ok={{flag}}", context) == "n=3 ok=true"
    assert render_template({"greet": "hi {{name}}"}, context) == {"greet": "hi Ada"}


def test_render_template_leaves_unknown_paths_empty_rather_than_raising():
    assert render_template("v={{nope.here}}", {}) == "v="


# --- schema ----------------------------------------------------------------


def test_switch_edge_must_name_a_declared_case():
    with pytest.raises(ValueError, match="not one of the SWITCH node's cases"):
        WorkflowDefinition.model_validate(
            _wf(
                [TRIGGER, {"id": "s", "type": "SWITCH", "config": {"on": "tier", "cases": ["gold"]}}, END],
                [{"source": "t", "target": "s"}, {"source": "s", "target": "e", "condition": "bronze"}],
            )
        )


def test_condition_edge_cannot_use_an_approval_label():
    with pytest.raises(ValueError, match="only branches on"):
        WorkflowDefinition.model_validate(
            _wf(
                [TRIGGER, {"id": "c", "type": "CONDITION", "config": {"check": "quote_responded"}}, END],
                [{"source": "t", "target": "c"}, {"source": "c", "target": "e", "condition": "approved"}],
            )
        )


def test_loop_without_a_body_edge_is_rejected():
    with pytest.raises(ValueError, match="needs an outgoing edge labelled 'body'"):
        WorkflowDefinition.model_validate(
            _wf(
                [TRIGGER, {"id": "l", "type": "LOOP", "config": {"over": "items", "as": "i"}}, END],
                [{"source": "t", "target": "l"}, {"source": "l", "target": "e", "condition": "done"}],
            )
        )


def test_http_request_rejects_a_non_http_scheme():
    with pytest.raises(ValueError, match="must be an http"):
        WorkflowDefinition.model_validate(
            _wf(
                [TRIGGER, {"id": "h", "type": "HTTP_REQUEST", "config": {"url": "file:///etc/passwd"}}, END],
                [{"source": "t", "target": "h"}, {"source": "h", "target": "e"}],
            )
        )


def test_negative_delay_is_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        WorkflowDefinition.model_validate(
            _wf(
                [TRIGGER, {"id": "d", "type": "DELAY", "config": {"duration_seconds": -1}}, END],
                [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
            )
        )


# --- sandbox ---------------------------------------------------------------


def test_dry_run_reports_an_invalid_definition_instead_of_raising():
    result = dry_run(_wf([{"id": "a", "type": "ACTION", "config": {"action": "x"}}], []))
    assert result["status"] == "FAILED"
    assert "Invalid workflow definition" in result["error"]


def test_delay_is_skipped_but_totalled():
    result = dry_run(
        _wf(
            [TRIGGER, {"id": "d", "type": "DELAY", "config": {"duration_seconds": 86400}}, END],
            [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
        )
    )
    assert result["status"] == "COMPLETED"
    assert result["simulated_seconds"] == 86400
    assert "1d" in next(s["detail"] for s in result["steps"] if s["node_type"] == "DELAY")


def test_loop_iterates_each_item_then_takes_the_done_edge():
    result = dry_run(
        _wf(
            [
                TRIGGER,
                {"id": "s", "type": "SET", "config": {"values": {"items": ["a", "b", "c"]}}},
                {"id": "l", "type": "LOOP", "config": {"over": "items", "as": "item"}},
                {"id": "act", "type": "ACTION", "config": {"action": "send_quote_followup"}},
                END,
            ],
            [
                {"source": "t", "target": "s"},
                {"source": "s", "target": "l"},
                {"source": "l", "target": "act", "condition": "body"},
                {"source": "act", "target": "l"},
                {"source": "l", "target": "e", "condition": "done"},
            ],
        )
    )
    assert result["status"] == "COMPLETED"
    assert sum(1 for s in result["steps"] if s["node_id"] == "act") == 3
    # The loop variable is cleared on the way out rather than leaking the last item.
    assert "item" not in result["context"]


def test_loop_respects_max_iterations():
    result = dry_run(
        _wf(
            [
                TRIGGER,
                {"id": "s", "type": "SET", "config": {"values": {"items": [1, 2, 3, 4, 5]}}},
                {"id": "l", "type": "LOOP", "config": {"over": "items", "as": "i", "max_iterations": 2}},
                {"id": "act", "type": "ACTION", "config": {"action": "send_quote_followup"}},
                END,
            ],
            [
                {"source": "t", "target": "s"},
                {"source": "s", "target": "l"},
                {"source": "l", "target": "act", "condition": "body"},
                {"source": "act", "target": "l"},
                {"source": "l", "target": "e", "condition": "done"},
            ],
        )
    )
    assert sum(1 for s in result["steps"] if s["node_id"] == "act") == 2
    assert any("exceeds max_iterations" in s["detail"] for s in result["steps"])


def test_switch_routes_on_context_and_falls_back_to_default():
    definition = _wf(
        [
            TRIGGER,
            {"id": "sw", "type": "SWITCH", "config": {"on": "tier", "cases": ["gold", "silver"]}},
            {"id": "g", "type": "ACTION", "config": {"action": "send_quote_followup"}},
            {"id": "d", "type": "ACTION", "config": {"action": "notify_team_quote_stalled"}},
            END,
        ],
        [
            {"source": "t", "target": "sw"},
            {"source": "sw", "target": "g", "condition": "gold"},
            {"source": "sw", "target": "d", "condition": "default"},
            {"source": "g", "target": "e"},
            {"source": "d", "target": "e"},
        ],
    )
    assert any(s["node_id"] == "g" for s in dry_run(definition, context={"tier": "gold"})["steps"])
    assert any(s["node_id"] == "d" for s in dry_run(definition, context={"tier": "bronze"})["steps"])


def test_decisions_override_a_condition_branch():
    definition = _wf(
        [
            TRIGGER,
            {"id": "c", "type": "CONDITION", "config": {"check": "quote_responded"}},
            {"id": "yes", "type": "ACTION", "config": {"action": "send_quote_followup"}},
            {"id": "no", "type": "ACTION", "config": {"action": "notify_team_quote_stalled"}},
            END,
        ],
        [
            {"source": "t", "target": "c"},
            {"source": "c", "target": "yes", "condition": "true"},
            {"source": "c", "target": "no", "condition": "false"},
            {"source": "yes", "target": "e"},
            {"source": "no", "target": "e"},
        ],
    )
    assert any(s["node_id"] == "yes" for s in dry_run(definition, decisions={"c": "true"})["steps"])
    # Documented default for an unspecified CONDITION is the "no reply yet" path.
    assert any(s["node_id"] == "no" for s in dry_run(definition)["steps"])


def test_dangling_branch_is_reported_rather_than_silently_ending():
    result = dry_run(
        _wf(
            [
                TRIGGER,
                {"id": "c", "type": "CONDITION", "config": {"check": "quote_responded"}},
                END,
            ],
            [{"source": "t", "target": "c"}, {"source": "c", "target": "e", "condition": "true"}],
        ),
        decisions={"c": "false"},
    )
    assert any("No outgoing edge for branch" in s["detail"] for s in result["steps"])


def test_cyclic_graph_hits_the_step_limit_instead_of_hanging():
    result = dry_run(
        _wf(
            [TRIGGER, {"id": "a", "type": "ACTION", "config": {"action": "send_quote_followup"}}, END],
            [{"source": "t", "target": "a"}, {"source": "a", "target": "t"}],
        )
    )
    assert result["status"] == "FAILED"
    assert str(MAX_STEPS) in result["error"]


def test_dry_run_does_not_mutate_the_callers_context():
    context = {"tier": "gold"}
    dry_run(
        _wf(
            [TRIGGER, {"id": "s", "type": "SET", "config": {"values": {"tier": "silver"}}}, END],
            [{"source": "t", "target": "s"}, {"source": "s", "target": "e"}],
        ),
        context=context,
    )
    assert context == {"tier": "gold"}


def test_sub_workflow_steps_appear_inline_and_unresolved_ids_are_flagged():
    sub = _wf(
        [TRIGGER, {"id": "inner", "type": "ACTION", "config": {"action": "send_quote_followup"}}, END],
        [{"source": "t", "target": "inner"}, {"source": "inner", "target": "e"}],
    )
    definition = _wf(
        [TRIGGER, {"id": "sw", "type": "SUB_WORKFLOW", "config": {"workflow_id": "abc"}}, END],
        [{"source": "t", "target": "sw"}, {"source": "sw", "target": "e"}],
    )

    resolved = dry_run(definition, sub_definitions={"abc": sub})
    assert any(s["node_id"] == "inner" and s["depth"] == 1 for s in resolved["steps"])

    unresolved = dry_run(definition)
    assert unresolved["status"] == "COMPLETED"
    assert any("could not be loaded" in s["detail"] for s in unresolved["steps"])
