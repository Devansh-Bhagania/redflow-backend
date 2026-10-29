"""CRUD for company-configured Agent records (app/models/agent.py) and their
workflows. Deliberately mounted at /agent-configs rather than /agents: that
prefix is already the built-in agent catalog + sandbox (app/api/v1/agents.py)
and changing its meaning would break every existing caller. Reuses the
existing agents.read/agents.manage permissions — this is still "AI agents",
just a different resource shape.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.agent_config import AgentConfigCreate, AgentConfigOut, AgentConfigUpdate
from app.schemas.workflow import WorkflowCreate, WorkflowOut
from app.services import agent_config_service, workflow_service

router = APIRouter(prefix="/agent-configs", tags=["agent-configs"])


@router.post("", response_model=AgentConfigOut, status_code=201)
async def create_agent(
    payload: AgentConfigCreate,
    ctx: TenantContext = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_config_service.create_agent(db, ctx.organization_id, payload)


@router.get("", response_model=list[AgentConfigOut])
async def list_agents(
    ctx: TenantContext = Depends(require_permission("agents.read")), db: AsyncSession = Depends(get_db)
):
    return await agent_config_service.list_agents(db, ctx.organization_id)


@router.get("/{agent_id}", response_model=AgentConfigOut)
async def get_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_config_service.get_agent(db, ctx.organization_id, agent_id)


@router.patch("/{agent_id}", response_model=AgentConfigOut)
async def update_agent(
    agent_id: uuid.UUID,
    payload: AgentConfigUpdate,
    ctx: TenantContext = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_config_service.update_agent(db, ctx.organization_id, agent_id, payload)


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
):
    await agent_config_service.delete_agent(db, ctx.organization_id, agent_id)


# --- an agent's workflows ---------------------------------------------------
# Thin wrappers over the existing workflow_service — no parallel workflow
# implementation, just this agent_id attached and org-scoped through the same
# service functions every other workflow endpoint uses.


@router.post("/{agent_id}/workflows", response_model=WorkflowOut, status_code=201)
async def create_agent_workflow(
    agent_id: uuid.UUID,
    payload: WorkflowCreate,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    await agent_config_service.get_agent(db, ctx.organization_id, agent_id)  # 404s if not this org's
    return await workflow_service.create_workflow(db, ctx.organization_id, payload, agent_id=agent_id)


@router.get("/{agent_id}/workflows", response_model=list[WorkflowOut])
async def list_agent_workflows(
    agent_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.read")),
    db: AsyncSession = Depends(get_db),
):
    await agent_config_service.get_agent(db, ctx.organization_id, agent_id)
    return await workflow_service.list_workflows(db, ctx.organization_id, agent_id=agent_id)


@router.post("/{agent_id}/workflows/{workflow_id}/attach", response_model=WorkflowOut)
async def attach_agent_workflow(
    agent_id: uuid.UUID,
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Links an already-existing, unattached workflow to this agent instead
    of building a new one from scratch."""
    await agent_config_service.get_agent(db, ctx.organization_id, agent_id)
    return await workflow_service.attach_workflow_to_agent(db, ctx.organization_id, agent_id, workflow_id)


@router.post("/{agent_id}/workflows/{workflow_id}/copy", response_model=WorkflowOut, status_code=201)
async def copy_agent_workflow(
    agent_id: uuid.UUID,
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Duplicates any org workflow's latest definition into a new DRAFT
    workflow owned by this agent — a copy to edit, not a shared link."""
    await agent_config_service.get_agent(db, ctx.organization_id, agent_id)
    return await workflow_service.copy_workflow_to_agent(db, ctx.organization_id, agent_id, workflow_id)
