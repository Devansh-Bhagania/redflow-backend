"""CRUD for company-configured Agent records and their workflows
(/api/v1/agent-configs) — org scoping, status validation, delete cascade."""

import pytest


async def _headers(client, email="owner@agentconfig.example"):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"full_name": "Owner", "email": email, "password": "supersecret123", "organization_name": "AC Co"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_create_list_get_agent(client):
    headers = await _headers(client)
    created = (await client.post(
        "/api/v1/agent-configs",
        json={"name": "Fashion Sales Assistant", "channels": ["WHATSAPP"], "system_prompt": "Be helpful."},
        headers=headers,
    )).json()
    assert created["status"] == "DRAFT"
    assert created["channels"] == ["WHATSAPP"]

    listed = (await client.get("/api/v1/agent-configs", headers=headers)).json()
    assert [a["id"] for a in listed] == [created["id"]]

    fetched = (await client.get(f"/api/v1/agent-configs/{created['id']}", headers=headers)).json()
    assert fetched["name"] == "Fashion Sales Assistant"


async def test_update_status_and_reject_invalid_status(client):
    headers = await _headers(client, "owner2@agentconfig.example")
    created = (await client.post("/api/v1/agent-configs", json={"name": "A"}, headers=headers)).json()

    ok = await client.patch(f"/api/v1/agent-configs/{created['id']}", json={"status": "ACTIVE"}, headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "ACTIVE"

    bad = await client.patch(f"/api/v1/agent-configs/{created['id']}", json={"status": "ENABLED"}, headers=headers)
    assert bad.status_code == 400


async def test_agent_configs_are_org_scoped(client):
    headers = await _headers(client, "owner3@agentconfig.example")
    other = await _headers(client, "intruder@agentconfig.example")
    created = (await client.post("/api/v1/agent-configs", json={"name": "Secret"}, headers=headers)).json()

    assert (await client.get(f"/api/v1/agent-configs/{created['id']}", headers=other)).status_code == 404
    assert (await client.patch(f"/api/v1/agent-configs/{created['id']}", json={"name": "x"}, headers=other)).status_code == 404
    assert (await client.get("/api/v1/agent-configs", headers=other)).json() == []


async def test_agent_workflows_are_created_under_the_agent(client):
    headers = await _headers(client, "owner4@agentconfig.example")
    agent = (await client.post("/api/v1/agent-configs", json={"name": "Sales"}, headers=headers)).json()

    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    created = await client.post(
        f"/api/v1/agent-configs/{agent['id']}/workflows",
        json={"name": "Main flow", "trigger_event": "message.received", "definition": definition},
        headers=headers,
    )
    assert created.status_code == 201, created.text

    listed = (await client.get(f"/api/v1/agent-configs/{agent['id']}/workflows", headers=headers)).json()
    assert len(listed) == 1
    assert listed[0]["name"] == "Main flow"

    # A workflow created under another agent must not show up here.
    other_agent = (await client.post("/api/v1/agent-configs", json={"name": "Support"}, headers=headers)).json()
    empty = (await client.get(f"/api/v1/agent-configs/{other_agent['id']}/workflows", headers=headers)).json()
    assert empty == []


async def test_cannot_create_a_workflow_under_another_orgs_agent(client):
    headers = await _headers(client, "owner5@agentconfig.example")
    other = await _headers(client, "intruder2@agentconfig.example")
    agent = (await client.post("/api/v1/agent-configs", json={"name": "A"}, headers=headers)).json()

    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    resp = await client.post(
        f"/api/v1/agent-configs/{agent['id']}/workflows",
        json={"name": "x", "trigger_event": "y", "definition": definition},
        headers=other,
    )
    assert resp.status_code == 404


async def test_delete_agent_cascades_its_workflows(client):
    headers = await _headers(client, "owner6@agentconfig.example")
    agent = (await client.post("/api/v1/agent-configs", json={"name": "A"}, headers=headers)).json()
    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    workflow = (await client.post(
        f"/api/v1/agent-configs/{agent['id']}/workflows",
        json={"name": "x", "trigger_event": "y", "definition": definition},
        headers=headers,
    )).json()

    assert (await client.delete(f"/api/v1/agent-configs/{agent['id']}", headers=headers)).status_code == 204
    assert (await client.get(f"/api/v1/workflows/{workflow['id']}", headers=headers)).status_code == 404


async def test_attach_links_a_standalone_workflow_to_an_agent(client):
    headers = await _headers(client, "owner7@agentconfig.example")
    agent = (await client.post("/api/v1/agent-configs", json={"name": "A"}, headers=headers)).json()
    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    standalone = (await client.post(
        "/api/v1/workflows", json={"name": "Orphan", "trigger_event": "quote.sent", "definition": definition},
        headers=headers,
    )).json()

    resp = await client.post(f"/api/v1/agent-configs/{agent['id']}/workflows/{standalone['id']}/attach", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_id"] == agent["id"]

    listed = (await client.get(f"/api/v1/agent-configs/{agent['id']}/workflows", headers=headers)).json()
    assert [w["id"] for w in listed] == [standalone["id"]]


async def test_attach_refuses_a_workflow_owned_by_another_agent(client):
    headers = await _headers(client, "owner8@agentconfig.example")
    agent_a = (await client.post("/api/v1/agent-configs", json={"name": "A"}, headers=headers)).json()
    agent_b = (await client.post("/api/v1/agent-configs", json={"name": "B"}, headers=headers)).json()
    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    workflow = (await client.post(
        f"/api/v1/agent-configs/{agent_a['id']}/workflows",
        json={"name": "x", "trigger_event": "y", "definition": definition},
        headers=headers,
    )).json()

    resp = await client.post(f"/api/v1/agent-configs/{agent_b['id']}/workflows/{workflow['id']}/attach", headers=headers)
    assert resp.status_code == 409


async def test_copy_duplicates_the_definition_into_a_new_workflow_owned_by_the_agent(client):
    headers = await _headers(client, "owner9@agentconfig.example")
    source_agent = (await client.post("/api/v1/agent-configs", json={"name": "Source"}, headers=headers)).json()
    target_agent = (await client.post("/api/v1/agent-configs", json={"name": "Target"}, headers=headers)).json()
    definition = {
        "nodes": [{"id": "t", "type": "TRIGGER", "config": {}}, {"id": "e", "type": "END", "config": {}}],
        "edges": [{"source": "t", "target": "e"}],
    }
    source = (await client.post(
        f"/api/v1/agent-configs/{source_agent['id']}/workflows",
        json={"name": "Original", "trigger_event": "message.received", "definition": definition},
        headers=headers,
    )).json()

    resp = await client.post(f"/api/v1/agent-configs/{target_agent['id']}/workflows/{source['id']}/copy", headers=headers)
    assert resp.status_code == 201, resp.text
    copy = resp.json()
    assert copy["id"] != source["id"]
    assert copy["agent_id"] == target_agent["id"]
    assert copy["definition"] == definition

    # The source is untouched — still owned by its original agent.
    assert (await client.get(f"/api/v1/workflows/{source['id']}", headers=headers)).json()["agent_id"] == source_agent["id"]
