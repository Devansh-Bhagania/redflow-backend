import pytest

from app.workflows.schema import WorkflowDefinition


async def _owner_headers(client, email="owner@wf.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "WF Co"},
    )
    tokens = resp.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


SIMPLE_DEFINITION = {
    "nodes": [
        {"id": "t", "type": "TRIGGER", "config": {}},
        {"id": "d", "type": "DELAY", "config": {"duration_seconds": 1}},
        {"id": "e", "type": "END", "config": {}},
    ],
    "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
}


def test_schema_rejects_missing_trigger():
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {"nodes": [{"id": "a", "type": "ACTION", "config": {"action": "x"}}], "edges": []}
        )


def test_schema_rejects_dangling_edge():
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}],
                "edges": [{"source": "t", "target": "missing"}],
            }
        )


def test_schema_rejects_delay_without_duration():
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "d", "type": "DELAY", "config": {}}],
                "edges": [{"source": "t", "target": "d"}],
            }
        )


def test_schema_accepts_a_valid_graph():
    WorkflowDefinition.model_validate(SIMPLE_DEFINITION)


def test_schema_rejects_ai_agent_without_agent_type():
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "a", "type": "AI_AGENT", "config": {}}],
                "edges": [{"source": "t", "target": "a"}],
            }
        )


def test_schema_rejects_ai_agent_with_unknown_agent_type():
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(
            {
                "nodes": [
                    {"id": "t", "type": "TRIGGER", "config": {}},
                    {"id": "a", "type": "AI_AGENT", "config": {"agent_type": "NOT_A_REAL_AGENT"}},
                ],
                "edges": [{"source": "t", "target": "a"}],
            }
        )


def test_schema_accepts_a_valid_ai_agent_node():
    WorkflowDefinition.model_validate(
        {
            "nodes": [
                {"id": "t", "type": "TRIGGER", "config": {}},
                {"id": "a", "type": "AI_AGENT", "config": {"agent_type": "FOLLOWUP"}},
                {"id": "e", "type": "END", "config": {}},
            ],
            "edges": [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}],
        }
    )


@pytest.mark.asyncio
async def test_create_workflow_rejects_invalid_definition(client):
    headers = await _owner_headers(client)
    resp = await client.post(
        "/api/v1/workflows",
        json={"name": "Bad", "trigger_event": "quote.sent", "definition": {"nodes": [], "edges": []}},
        headers=headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_list_get_publish_workflow(client):
    headers = await _owner_headers(client, "owner2@wf.example")

    created = await client.post(
        "/api/v1/workflows",
        json={"name": "Follow-up", "trigger_event": "quote.sent", "definition": SIMPLE_DEFINITION},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "DRAFT"

    listed = await client.get("/api/v1/workflows", headers=headers)
    assert len(listed.json()) == 1

    fetched = await client.get(f"/api/v1/workflows/{body['id']}", headers=headers)
    assert fetched.status_code == 200

    published = await client.post(f"/api/v1/workflows/{body['id']}/publish", headers=headers)
    assert published.status_code == 200
    assert published.json()["status"] == "PUBLISHED"


@pytest.mark.asyncio
async def test_create_workflow_with_ai_agent_node(client):
    headers = await _owner_headers(client, "owner3@wf.example")
    definition = {
        "nodes": [
            {"id": "t", "type": "TRIGGER", "config": {}},
            {"id": "a", "type": "AI_AGENT", "config": {"agent_type": "COLLECTIONS"}},
            {"id": "e", "type": "END", "config": {}},
        ],
        "edges": [{"source": "t", "target": "a"}, {"source": "a", "target": "e"}],
    }
    created = await client.post(
        "/api/v1/workflows",
        json={"name": "Collections nudge", "trigger_event": "quote.sent", "definition": definition},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["definition"]["nodes"][1]["config"]["agent_type"] == "COLLECTIONS"


@pytest.mark.asyncio
async def test_list_default_workflows_includes_quote_followup(client):
    headers = await _owner_headers(client, "owner4@wf.example")
    resp = await client.get("/api/v1/workflows/defaults", headers=headers)
    assert resp.status_code == 200
    defaults = resp.json()
    assert any(d["trigger_event"] == "quote.sent" for d in defaults)
    quote_default = next(d for d in defaults if d["trigger_event"] == "quote.sent")
    assert quote_default["definition"]["nodes"][0]["type"] == "TRIGGER"


@pytest.mark.asyncio
async def test_workflows_require_permission(client):
    resp = await client.get("/api/v1/workflows")
    assert resp.status_code == 401
