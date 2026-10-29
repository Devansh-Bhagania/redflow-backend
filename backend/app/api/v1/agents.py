import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import AGENT_DESCRIPTIONS, CUSTOMER_FACING_AGENTS, INTERNAL_AGENT_TYPES
from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.agent import AgentOut, SandboxMessageRequest, SandboxResetRequest
from app.schemas.conversation import ConversationOut
from app.services import agent_sandbox_service

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
async def list_agents(_=Depends(require_permission("agents.read"))):
    return [
        AgentOut(agent_type=agent_type, description=AGENT_DESCRIPTIONS[agent_type], tools=[t["name"] for t in tools])
        for agent_type, (_prompt, tools) in CUSTOMER_FACING_AGENTS.items()
    ]


@router.get("/sandbox", response_model=ConversationOut)
async def get_sandbox(
    customer_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission("agents.read")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_sandbox_service.get_sandbox_conversation(db, ctx.organization_id, customer_id)


@router.post("/sandbox/messages", response_model=ConversationOut)
async def send_sandbox_message(
    payload: SandboxMessageRequest,
    ctx: TenantContext = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
):
    # Enforced here, not just by omitting it from the catalog above: the
    # agent_type is caller-supplied, and an internal agent run against a
    # customer sandbox conversation would fail deep inside its tools.
    if payload.agent_type in INTERNAL_AGENT_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{payload.agent_type} is not a customer-facing agent and cannot run in the customer sandbox",
        )
    return await agent_sandbox_service.send_sandbox_message(
        db, ctx.organization_id, payload.agent_type, payload.content, payload.customer_id
    )


@router.post("/sandbox/reset", response_model=ConversationOut)
async def reset_sandbox(
    payload: SandboxResetRequest = Body(default_factory=SandboxResetRequest),
    ctx: TenantContext = Depends(require_permission("agents.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await agent_sandbox_service.reset_sandbox(db, ctx.organization_id, payload.customer_id)
