"""CRUD for company-configured Agent records — see app/models/agent.py for
why this exists alongside the legacy AGENT_TYPES constants."""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import AGENT_STATUSES, Agent
from app.schemas.agent_config import AgentConfigCreate, AgentConfigUpdate


async def create_agent(db: AsyncSession, organization_id: uuid.UUID, payload: AgentConfigCreate) -> Agent:
    agent = Agent(organization_id=organization_id, **payload.model_dump())
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def list_agents(db: AsyncSession, organization_id: uuid.UUID) -> list[Agent]:
    result = await db.execute(select(Agent).where(Agent.organization_id == organization_id))
    return list(result.scalars().all())


async def get_agent(db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID) -> Agent:
    result = await db.execute(select(Agent).where(Agent.organization_id == organization_id, Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agent not found")
    return agent


async def update_agent(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, payload: AgentConfigUpdate
) -> Agent:
    agent = await get_agent(db, organization_id, agent_id)
    updates = payload.model_dump(exclude_unset=True)
    if "status" in updates and updates["status"] not in AGENT_STATUSES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"status must be one of {AGENT_STATUSES}")
    for field, value in updates.items():
        setattr(agent, field, value)
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return agent


async def delete_agent(db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID) -> None:
    agent = await get_agent(db, organization_id, agent_id)
    # Workflow.agent_id is ondelete="CASCADE", so this also removes the
    # agent's workflows and their versions. WorkflowExecution.workflow_version_id
    # has no cascade, so the database itself refuses this delete while any
    # execution still references one of those versions — a running
    # conversation blocks deleting the agent underneath it rather than being
    # silently orphaned.
    await db.delete(agent)
    await db.commit()
