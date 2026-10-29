"""Workflow editing, the dry-run endpoint, and the builder chat.

The builder agent runs through the shared runtime, so these stub the model
client the same way tests/test_agent.py does rather than calling a provider.
"""

from types import SimpleNamespace

import pytest

from app.services import workflow_builder_service


async def _headers(client, email="owner@builder.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={
            "full_name": "Owner", "email": email, "password": "supersecret123",
            "organization_name": "Builder Co",
        },
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


SIMPLE = {
    "nodes": [
        {"id": "t", "type": "TRIGGER", "config": {}},
        {"id": "e", "type": "END", "config": {}},
    ],
    "edges": [{"source": "t", "target": "e"}],
}


async def _create(client, headers, definition=None):
    resp = await client.post(
        "/api/v1/workflows",
        json={"name": "Test flow", "trigger_event": "quote.sent", "definition": definition or SIMPLE},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- update / versions -----------------------------------------------------


async def test_update_creates_a_new_version_and_keeps_the_old_one(client):
    headers = await _headers(client)
    workflow = await _create(client, headers)

    updated = {
        "nodes": SIMPLE["nodes"] + [{"id": "d", "type": "DELAY", "config": {"duration_seconds": 60}}],
        "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
    }
    resp = await client.put(
        f"/api/v1/workflows/{workflow['id']}", json={"definition": updated, "name": "Renamed"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    assert len(resp.json()["definition"]["nodes"]) == 3

    versions = (await client.get(f"/api/v1/workflows/{workflow['id']}/versions", headers=headers)).json()
    # The v1 graph must survive: an execution parked at a DELAY node still
    # points at it and has to keep resolving to the graph it started on.
    assert [v["version"] for v in versions] == [2, 1]
    assert len(versions[-1]["definition"]["nodes"]) == 2


async def test_update_rejects_an_invalid_definition(client):
    headers = await _headers(client, "owner2@builder.example")
    workflow = await _create(client, headers)
    resp = await client.put(
        f"/api/v1/workflows/{workflow['id']}",
        json={"definition": {"nodes": [{"id": "a", "type": "ACTION", "config": {}}], "edges": []}},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Invalid workflow definition" in resp.json()["detail"]


async def test_update_is_scoped_to_the_organization(client):
    headers = await _headers(client, "owner3@builder.example")
    workflow = await _create(client, headers)
    other = await _headers(client, "intruder@builder.example")

    resp = await client.put(f"/api/v1/workflows/{workflow['id']}", json={"name": "Hijacked"}, headers=other)
    assert resp.status_code == 404


# --- capabilities / dry run ------------------------------------------------


async def test_capabilities_lists_only_registered_actions(client):
    headers = await _headers(client, "owner4@builder.example")
    caps = (await client.get("/api/v1/workflows/capabilities", headers=headers)).json()
    assert "quote_responded" in caps["conditions"]
    assert "send_quote_followup" in caps["actions"]
    assert "HTTP_REQUEST" in caps["node_types"]
    assert caps["branch_labels"]["LOOP"] == ["body", "done"]


async def test_dry_run_endpoint_returns_a_trace(client):
    headers = await _headers(client, "owner5@builder.example")
    definition = {
        "nodes": [
            {"id": "t", "type": "TRIGGER", "config": {}},
            {"id": "d", "type": "DELAY", "config": {"duration_seconds": 3600}},
            {"id": "e", "type": "END", "config": {}},
        ],
        "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
    }
    resp = await client.post("/api/v1/workflows/dry-run", json={"definition": definition}, headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "COMPLETED"
    assert body["simulated_seconds"] == 3600
    assert [s["node_type"] for s in body["steps"]] == ["TRIGGER", "DELAY", "END"]


async def test_dry_run_works_on_an_unsaved_definition(client):
    """Drafting is the main use — the graph doesn't exist server-side yet."""
    headers = await _headers(client, "owner6@builder.example")
    resp = await client.post("/api/v1/workflows/dry-run", json={"definition": SIMPLE}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


# --- builder chat ----------------------------------------------------------


class _StubResponse:
    def __init__(self, content):
        self.content = content
        self.usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        self.stop_reason = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(name, input, id="tu-1"):
    return SimpleNamespace(type="tool_use", name=name, input=input, id=id)


def _stub_client(monkeypatch, scripted):
    """Replays `scripted` turns in order, so a test can drive the tool loop."""
    turns = iter(scripted)

    class _Messages:
        async def create(self, **kwargs):
            return _StubResponse(next(turns))

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("app.agents.runtime._client_and_model", lambda: (_Client(), "stub-model"))


async def test_builder_chat_stages_a_proposal_without_saving(client, monkeypatch):
    headers = await _headers(client, "owner7@builder.example")
    workflow = await _create(client, headers)

    proposed = {
        "nodes": [
            {"id": "t", "type": "TRIGGER", "config": {}},
            {"id": "d", "type": "DELAY", "config": {"duration_seconds": 86400}},
            {"id": "e", "type": "END", "config": {}},
        ],
        "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
    }
    _stub_client(
        monkeypatch,
        [
            [_tool_use("propose_workflow_definition", {"definition": proposed, "summary": "Wait a day first"})],
            [_text("I've staged a one-day wait before the end. Apply it when you're happy.")],
        ],
    )

    resp = await client.post(
        f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "wait a day first"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["proposal"]["nodes"][1]["type"] == "DELAY"
    assert body["proposal_summary"] == "Wait a day first"

    # Staged only — the stored workflow is untouched until it's applied.
    stored = (await client.get(f"/api/v1/workflows/{workflow['id']}", headers=headers)).json()
    assert len(stored["definition"]["nodes"]) == 2


async def test_applying_a_proposal_writes_a_new_version(client, monkeypatch):
    headers = await _headers(client, "owner8@builder.example")
    workflow = await _create(client, headers)

    proposed = {
        "nodes": [
            {"id": "t", "type": "TRIGGER", "config": {}},
            {"id": "d", "type": "DELAY", "config": {"duration_seconds": 60}},
            {"id": "e", "type": "END", "config": {}},
        ],
        "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
    }
    _stub_client(
        monkeypatch,
        [[_tool_use("propose_workflow_definition", {"definition": proposed})], [_text("Staged.")]],
    )
    await client.post(f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "add a delay"}, headers=headers)

    resp = await client.post(f"/api/v1/workflows/{workflow['id']}/chat/apply", headers=headers)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["definition"]["nodes"]) == 3

    versions = (await client.get(f"/api/v1/workflows/{workflow['id']}/versions", headers=headers)).json()
    assert [v["version"] for v in versions] == [2, 1]

    # The proposal is consumed, so a second apply has nothing to do.
    assert (await client.post(f"/api/v1/workflows/{workflow['id']}/chat/apply", headers=headers)).status_code == 409


async def test_invalid_proposal_is_reported_to_the_agent_not_staged(client, monkeypatch):
    """The tool returns the validation error so the model can self-correct;
    a broken graph must never reach the canvas as a proposal."""
    headers = await _headers(client, "owner9@builder.example")
    workflow = await _create(client, headers)

    _stub_client(
        monkeypatch,
        [
            [_tool_use("propose_workflow_definition", {"definition": {"nodes": [], "edges": []}})],
            [_text("That didn't validate — an empty graph has no trigger.")],
        ],
    )
    resp = await client.post(
        f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "empty it"}, headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["proposal"] is None

    assert (await client.post(f"/api/v1/workflows/{workflow['id']}/chat/apply", headers=headers)).status_code == 409


async def test_discard_clears_a_staged_proposal(client, monkeypatch):
    headers = await _headers(client, "owner10@builder.example")
    workflow = await _create(client, headers)

    proposed = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    _stub_client(monkeypatch, [[_tool_use("propose_workflow_definition", {"definition": proposed})], [_text("ok")]])
    await client.post(f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "hi"}, headers=headers)

    resp = await client.post(f"/api/v1/workflows/{workflow['id']}/chat/discard", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["proposal"] is None


async def test_builder_chat_is_scoped_per_workflow(client, monkeypatch):
    """Two workflows must not share one conversation — the builder's tools read
    the workflow id off the conversation's agent_state."""
    headers = await _headers(client, "owner11@builder.example")
    first = await _create(client, headers)
    second = await _create(client, headers)

    _stub_client(monkeypatch, [[_text("first")], [_text("second")]])
    await client.post(f"/api/v1/workflows/{first['id']}/chat", json={"message": "one"}, headers=headers)
    await client.post(f"/api/v1/workflows/{second['id']}/chat", json={"message": "two"}, headers=headers)

    a = (await client.get(f"/api/v1/workflows/{first['id']}/chat", headers=headers)).json()
    b = (await client.get(f"/api/v1/workflows/{second['id']}/chat", headers=headers)).json()
    assert [m["content"] for m in a["messages"] if m["direction"] == "INBOUND"] == ["one"]
    assert [m["content"] for m in b["messages"] if m["direction"] == "INBOUND"] == ["two"]


async def test_builder_chat_404s_for_another_organizations_workflow(client):
    headers = await _headers(client, "owner12@builder.example")
    workflow = await _create(client, headers)
    intruder = await _headers(client, "intruder2@builder.example")

    resp = await client.get(f"/api/v1/workflows/{workflow['id']}/chat", headers=intruder)
    assert resp.status_code == 404


async def test_builder_agent_is_not_offered_in_the_customer_sandbox(client):
    """Its tools need a workflow-pinned conversation; running it against a
    customer sandbox would fail deep inside them."""
    headers = await _headers(client, "owner13@builder.example")

    agents = (await client.get("/api/v1/agents", headers=headers)).json()
    assert "WORKFLOW_BUILDER" not in {a["agent_type"] for a in agents}

    resp = await client.post(
        "/api/v1/agents/sandbox/messages",
        json={"agent_type": "WORKFLOW_BUILDER", "content": "hi"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "not a customer-facing agent" in resp.json()["detail"]


async def test_reset_keeps_the_session_attached_to_its_workflow(client, monkeypatch):
    headers = await _headers(client, "owner14@builder.example")
    workflow = await _create(client, headers)

    _stub_client(monkeypatch, [[_text("hello")], [_text("hello again")]])
    await client.post(f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "hi"}, headers=headers)

    resp = await client.post(f"/api/v1/workflows/{workflow['id']}/chat/reset", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["messages"] == []

    # Still pinned: the next turn's tools must resolve the workflow.
    after = await client.post(
        f"/api/v1/workflows/{workflow['id']}/chat", json={"message": "again"}, headers=headers
    )
    assert after.status_code == 200
