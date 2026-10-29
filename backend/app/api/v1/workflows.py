import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import TenantContext, require_permission
from app.schemas.workflow import (
    DefaultWorkflowOut,
    DryRunOut,
    DryRunRequest,
    ResumeRequest,
    WorkflowChatRequest,
    WorkflowCreate,
    WorkflowExecutionOut,
    WorkflowOut,
    WorkflowUpdate,
    WorkflowVersionOut,
)
from app.services import workflow_builder_service, workflow_service

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowOut])
async def list_workflows(
    ctx: TenantContext = Depends(require_permission("workflows.read")), db: AsyncSession = Depends(get_db)
):
    return await workflow_service.list_workflows(db, ctx.organization_id)


@router.post("", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    payload: WorkflowCreate,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_service.create_workflow(db, ctx.organization_id, payload)


# Registered before /{workflow_id} — otherwise "executions"/"defaults" would be parsed as a workflow_id.
@router.get("/executions", response_model=list[WorkflowExecutionOut])
async def list_executions(
    ctx: TenantContext = Depends(require_permission("workflows.read")), db: AsyncSession = Depends(get_db)
):
    return await workflow_service.list_executions(db, ctx.organization_id)


@router.get("/defaults", response_model=list[DefaultWorkflowOut])
async def list_default_workflows(_ctx: TenantContext = Depends(require_permission("workflows.read"))):
    return workflow_service.list_default_workflows()


@router.get("/capabilities")
async def list_capabilities(_ctx: TenantContext = Depends(require_permission("workflows.read"))):
    """Every node type, action, condition and agent a definition may reference.
    Drives the canvas dropdowns so the UI can't offer something the validator
    will reject."""
    return workflow_service.list_capabilities()


@router.post("/dry-run", response_model=DryRunOut)
async def dry_run(
    payload: DryRunRequest,
    ctx: TenantContext = Depends(require_permission("workflows.read")),
    db: AsyncSession = Depends(get_db),
):
    """Sandbox execution of an arbitrary definition — including one that has
    never been saved, which is what makes it useful while drafting."""
    return await workflow_service.dry_run_definition(
        db, ctx.organization_id, payload.definition, payload.context, payload.decisions
    )


@router.post("/executions/{execution_id}/resume", response_model=WorkflowExecutionOut)
async def resume_execution(
    execution_id: uuid.UUID,
    payload: ResumeRequest,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Unparks a run waiting on a HUMAN_APPROVAL or WEBHOOK node."""
    return await workflow_service.resume_execution(
        db, ctx.organization_id, execution_id, payload.node_id, payload.payload
    )


@router.get("/{workflow_id}", response_model=WorkflowOut)
async def get_workflow(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.read")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_service.get_workflow(db, ctx.organization_id, workflow_id)


@router.put("/{workflow_id}", response_model=WorkflowOut)
async def update_workflow(
    workflow_id: uuid.UUID,
    payload: WorkflowUpdate,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Saves a revision as a new version. Existing versions are never mutated —
    an in-flight run parked at a DELAY node must keep resolving to the graph it
    started on."""
    return await workflow_service.update_workflow(db, ctx.organization_id, workflow_id, payload)


@router.get("/{workflow_id}/versions", response_model=list[WorkflowVersionOut])
async def list_versions(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.read")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_service.list_versions(db, ctx.organization_id, workflow_id)


@router.post("/{workflow_id}/publish", response_model=WorkflowOut)
async def publish_workflow(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_service.publish_workflow(db, ctx.organization_id, workflow_id)


# --- builder chat ----------------------------------------------------------


@router.get("/{workflow_id}/chat")
async def get_builder_chat(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_builder_service.get_session(db, ctx.organization_id, workflow_id)


@router.post("/{workflow_id}/chat")
async def send_builder_message(
    workflow_id: uuid.UUID,
    payload: WorkflowChatRequest,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_builder_service.send_message(db, ctx.organization_id, workflow_id, payload.message)


@router.post("/{workflow_id}/chat/reset")
async def reset_builder_chat(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_builder_service.reset_session(db, ctx.organization_id, workflow_id)


@router.post("/{workflow_id}/chat/apply", response_model=WorkflowOut)
async def apply_builder_proposal(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    """Commits the agent's staged proposal as a new version. This is the only
    path from a proposal to the stored workflow — the agent has no other."""
    await workflow_builder_service.apply_proposal(db, ctx.organization_id, workflow_id)
    return await workflow_service.get_workflow(db, ctx.organization_id, workflow_id)


@router.post("/{workflow_id}/chat/discard")
async def discard_builder_proposal(
    workflow_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission("workflows.manage")),
    db: AsyncSession = Depends(get_db),
):
    return await workflow_builder_service.discard_proposal(db, ctx.organization_id, workflow_id)
