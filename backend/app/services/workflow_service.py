import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from app.core.config import settings
from app.models.quotation import Quotation
from app.models.workflow import Workflow, WorkflowExecution, WorkflowVersion
from app.schemas.workflow import WorkflowCreate, WorkflowUpdate
from app.workflows.graph_workflow import GraphWorkflow
from app.workflows.sandbox import collect_subworkflow_ids, dry_run
from app.workflows.schema import (
    AGENT_TYPES,
    BRANCH_LABELS,
    HTTP_METHODS,
    MAX_LOOP_ITERATIONS,
    MAX_SUBWORKFLOW_DEPTH,
    NodeType,
    WorkflowDefinition,
)

logger = logging.getLogger(__name__)

TASK_QUEUE = "redflow-workflows"

# Used whenever an organization hasn't published its own "quote.sent" workflow.
# Mirrors the spec's example follow-up flow: 24h wait, nudge, 48h wait, escalate.
DEFAULT_QUOTE_FOLLOWUP_DEFINITION = {
    "nodes": [
        {"id": "trigger", "type": "TRIGGER", "config": {}},
        {"id": "delay1", "type": "DELAY", "config": {"duration_seconds": 86400}},
        {"id": "cond1", "type": "CONDITION", "config": {"check": "quote_responded"}},
        {"id": "end1", "type": "END", "config": {}},
        {"id": "followup", "type": "ACTION", "config": {"action": "send_quote_followup"}},
        {"id": "delay2", "type": "DELAY", "config": {"duration_seconds": 172800}},
        {"id": "cond2", "type": "CONDITION", "config": {"check": "quote_responded"}},
        {"id": "end2", "type": "END", "config": {}},
        {"id": "notify", "type": "ACTION", "config": {"action": "notify_team_quote_stalled"}},
        {"id": "end3", "type": "END", "config": {}},
    ],
    "edges": [
        {"source": "trigger", "target": "delay1"},
        {"source": "delay1", "target": "cond1"},
        {"source": "cond1", "target": "end1", "condition": "true"},
        {"source": "cond1", "target": "followup", "condition": "false"},
        {"source": "followup", "target": "delay2"},
        {"source": "delay2", "target": "cond2"},
        {"source": "cond2", "target": "end2", "condition": "true"},
        {"source": "cond2", "target": "notify", "condition": "false"},
        {"source": "notify", "target": "end3"},
    ],
}


# Every trigger event that falls back to a hardcoded default when an org
# hasn't published its own workflow for it — surfaced read-only in the UI so
# "the built-in quote follow-up runs by default" (see the workflows list empty
# state) is actually something a user can open and look at.
DEFAULT_WORKFLOWS = [
    {"trigger_event": "quote.sent", "name": "Built-in: quote follow-up", "definition": DEFAULT_QUOTE_FOLLOWUP_DEFINITION},
]


def list_default_workflows() -> list[dict]:
    return DEFAULT_WORKFLOWS


def list_capabilities() -> dict:
    """Everything a workflow definition is allowed to reference.

    Read by the builder agent before it proposes a graph, and by the canvas UI
    to populate its dropdowns. Derived from the registries rather than
    hand-listed, so a new action can't be added without showing up here — the
    agent inventing `action: "send_sms"` and failing validation on save was the
    failure mode this exists to prevent.
    """
    # Imported here, not at module scope: activities pulls in the integration
    # providers and the agent runtime, and workflow_service is imported by
    # agents.tool_executor — at module level that closes an import cycle.
    from app.workflows.activities import ACTIONS, CONDITIONS
    from app.workflows.schema import MEMORY_SCOPES, OUTPUT_MODES, _known_tool_names

    return {
        "node_types": list(NodeType.__args__),
        "actions": sorted(ACTIONS),
        "conditions": sorted(CONDITIONS),
        "agent_types": list(AGENT_TYPES),
        # Everything an AI_AGENT or TOOL node's config.tools may name.
        "tools": sorted(_known_tool_names()),
        "memory_scopes": list(MEMORY_SCOPES),
        "output_modes": list(OUTPUT_MODES),
        "http_methods": list(HTTP_METHODS),
        "branch_labels": {k: list(v) for k, v in BRANCH_LABELS.items()},
        "limits": {
            "max_loop_iterations": MAX_LOOP_ITERATIONS,
            "max_subworkflow_depth": MAX_SUBWORKFLOW_DEPTH,
        },
    }


async def create_workflow(
    db: AsyncSession, organization_id: uuid.UUID, payload: WorkflowCreate, agent_id: uuid.UUID | None = None
) -> Workflow:
    try:
        WorkflowDefinition.model_validate(payload.definition)
    except Exception as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid workflow definition: {exc}")

    workflow = Workflow(
        organization_id=organization_id, name=payload.name, trigger_event=payload.trigger_event,
        status="DRAFT", agent_id=agent_id,
    )
    db.add(workflow)
    await db.flush()

    db.add(WorkflowVersion(workflow_id=workflow.id, version=1, definition=payload.definition))
    await db.commit()
    await db.refresh(workflow)
    workflow.definition = payload.definition
    return workflow


async def attach_workflow_to_agent(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow:
    """Links an existing standalone workflow (agent_id is null — created via
    the generic /workflows endpoint, not "New workflow" on an agent page) to
    `agent_id`. Refuses to steal one that already belongs to a different
    agent: a workflow's agent_id is otherwise immutable once set (see
    update_workflow), and in-flight executions may still be resolving
    templates against the assumption of which agent owns it."""
    workflow = await get_workflow(db, organization_id, workflow_id)
    if workflow.agent_id is not None and workflow.agent_id != agent_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Workflow already belongs to a different agent")

    workflow.agent_id = agent_id
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    version = await _latest_version(db, workflow.id)
    workflow.definition = version.definition if version else {}
    return workflow


async def copy_workflow_to_agent(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID, workflow_id: uuid.UUID
) -> Workflow:
    """Duplicates another workflow's latest definition into a brand new DRAFT
    workflow owned by `agent_id` — a starting point to edit, not a live link:
    changes to the source afterward don't propagate."""
    source = await get_workflow(db, organization_id, workflow_id)

    workflow = Workflow(
        organization_id=organization_id, name=f"{source.name} (copy)",
        trigger_event=source.trigger_event, status="DRAFT", agent_id=agent_id,
    )
    db.add(workflow)
    await db.flush()
    db.add(WorkflowVersion(workflow_id=workflow.id, version=1, definition=source.definition))
    await db.commit()
    await db.refresh(workflow)
    workflow.definition = source.definition
    return workflow


async def update_workflow(
    db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID, payload: WorkflowUpdate
) -> Workflow:
    """Saves a revision. Never overwrites a WorkflowVersion in place — an
    in-flight execution holds a workflow_version_id and must keep resolving to
    the graph it actually started on, which can be days old at a DELAY node."""
    workflow = await get_workflow(db, organization_id, workflow_id)

    if payload.definition is not None:
        try:
            WorkflowDefinition.model_validate(payload.definition)
        except Exception as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid workflow definition: {exc}")
        latest = await _latest_version(db, workflow.id)
        db.add(
            WorkflowVersion(
                workflow_id=workflow.id,
                version=(latest.version + 1) if latest else 1,
                definition=payload.definition,
            )
        )

    if payload.name is not None:
        workflow.name = payload.name
    if payload.trigger_event is not None:
        workflow.trigger_event = payload.trigger_event

    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    version = await _latest_version(db, workflow.id)
    workflow.definition = version.definition if version else {}
    return workflow


async def list_versions(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> list[WorkflowVersion]:
    await get_workflow(db, organization_id, workflow_id)  # 404s if it isn't this org's
    result = await db.execute(
        select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id).order_by(WorkflowVersion.version.desc())
    )
    return list(result.scalars().all())


async def dry_run_definition(
    db: AsyncSession,
    organization_id: uuid.UUID,
    definition: dict,
    context: dict | None = None,
    decisions: dict[str, str] | None = None,
) -> dict:
    """Sandbox execution. Resolves any SUB_WORKFLOW references up front so the
    runner itself stays pure and does no IO."""
    sub_definitions: dict[str, dict] = {}
    for sub_id in collect_subworkflow_ids(definition):
        try:
            sub = await get_workflow(db, organization_id, uuid.UUID(sub_id))
        except (HTTPException, ValueError):
            # Left unresolved on purpose — the runner records "could not be
            # loaded" as a step rather than failing the whole dry run, which is
            # more useful while a graph is still being drafted.
            continue
        sub_definitions[sub_id] = sub.definition

    return dry_run(definition, context=context, decisions=decisions, sub_definitions=sub_definitions)


async def get_execution(db: AsyncSession, organization_id: uuid.UUID, execution_id: uuid.UUID) -> WorkflowExecution:
    result = await db.execute(
        select(WorkflowExecution).where(
            WorkflowExecution.organization_id == organization_id,
            WorkflowExecution.id == execution_id,
        )
    )
    execution = result.scalar_one_or_none()
    if not execution:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow execution not found")
    return execution


async def resume_execution(
    db: AsyncSession, organization_id: uuid.UUID, execution_id: uuid.UUID, node_id: str, payload: dict
) -> WorkflowExecution:
    """Signals a run parked on a HUMAN_APPROVAL or WEBHOOK node."""
    execution = await get_execution(db, organization_id, execution_id)
    if execution.status != "RUNNING":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Execution is already {execution.status}")

    client = await Client.connect(settings.temporal_url)
    handle = client.get_workflow_handle(execution.temporal_workflow_id)
    await handle.signal(GraphWorkflow.resume, args=[node_id, payload])
    return execution


async def signal_reply(db: AsyncSession, organization_id: uuid.UUID, execution_id: uuid.UUID, content: str) -> bool:
    """Resumes a run parked at a WAIT_FOR_REPLY (or HUMAN_HANDOFF) node with
    the customer's next message. Returns False — without raising — if the
    execution isn't RUNNING or isn't actually parked on one of those node
    types, so conversation_router can fall back to the legacy agent path
    instead of dropping the message.

    Queries the workflow's own `waiting_for` state rather than assuming which
    node: the walker is single-cursor (see graph_workflow.GraphWorkflow), so
    there's at most one, but this still reads it from the source of truth
    instead of guessing from the stored definition.
    """
    execution = await get_execution(db, organization_id, execution_id)
    if execution.status != "RUNNING":
        return False

    client = await Client.connect(settings.temporal_url)
    handle = client.get_workflow_handle(execution.temporal_workflow_id)
    waiting = await handle.query(GraphWorkflow.waiting_for)
    if not waiting:
        return False

    await handle.signal(GraphWorkflow.resume, args=[waiting[0], {"reply": content}])
    return True


async def publish_workflow(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    workflow = await get_workflow(db, organization_id, workflow_id)
    workflow.status = "PUBLISHED"
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)
    return workflow


async def get_workflow(db: AsyncSession, organization_id: uuid.UUID, workflow_id: uuid.UUID) -> Workflow:
    result = await db.execute(
        select(Workflow).where(Workflow.organization_id == organization_id, Workflow.id == workflow_id)
    )
    workflow = result.scalar_one_or_none()
    if not workflow:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Workflow not found")
    version = await _latest_version(db, workflow.id)
    workflow.definition = version.definition if version else {}
    return workflow


async def list_workflows(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID | None = None
) -> list[Workflow]:
    filters = [Workflow.organization_id == organization_id]
    if agent_id is not None:
        filters.append(Workflow.agent_id == agent_id)
    result = await db.execute(select(Workflow).where(*filters))
    return list(result.scalars().all())


async def get_published_workflow_for_agent(
    db: AsyncSession, organization_id: uuid.UUID, agent_id: uuid.UUID
) -> tuple[Workflow, uuid.UUID] | None:
    """The workflow (and its exact version id) that conversation routing
    should start for a fresh conversation on one of this agent's channels.
    Most-recently-created PUBLISHED workflow wins if an agent somehow has more
    than one — publishing a replacement is expected to leave the old one
    DRAFT/ARCHIVED-equivalent, but nothing currently enforces that at publish
    time (see report: single-active-workflow-per-agent isn't validated yet).

    Returns the version id explicitly (rather than leaving the caller to
    re-derive "latest") so the WorkflowExecution it starts pins the exact
    graph this call saw — not whatever happens to be latest by the time the
    execution row is written a moment later.
    """
    result = await db.execute(
        select(Workflow)
        .where(Workflow.organization_id == organization_id, Workflow.agent_id == agent_id, Workflow.status == "PUBLISHED")
        .order_by(Workflow.created_at.desc())
    )
    workflow = result.scalars().first()
    if workflow is None:
        return None
    version = await _latest_version(db, workflow.id)
    if version is None:
        return None
    workflow.definition = version.definition
    return workflow, version.id


async def _latest_version(db: AsyncSession, workflow_id: uuid.UUID) -> WorkflowVersion:
    result = await db.execute(
        select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow_id).order_by(WorkflowVersion.version.desc())
    )
    return result.scalars().first()


async def start_execution(
    db: AsyncSession,
    organization_id: uuid.UUID,
    definition: dict,
    context: dict,
    workflow_id: uuid.UUID | None = None,
    workflow_version_id: uuid.UUID | None = None,
) -> WorkflowExecution:
    # Stamped into the context (not just the row) so node activities — which
    # only ever see `context`, never the execution row itself — can attribute
    # an AiExecution back to the run that produced it. See
    # app.agents.node_runtime and activities.run_inline_ai_agent_node.
    context = {
        **context,
        "organization_id": str(organization_id),
        "workflow_id": str(workflow_id) if workflow_id else None,
        "workflow_version_id": str(workflow_version_id) if workflow_version_id else None,
    }

    execution = WorkflowExecution(
        organization_id=organization_id,
        workflow_id=workflow_id,
        workflow_version_id=workflow_version_id,
        temporal_workflow_id=f"wf-{uuid.uuid4()}",
        status="RUNNING",
        context=context,
        started_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    await db.flush()
    context["execution_id"] = str(execution.id)
    execution.context = context

    client = await Client.connect(settings.temporal_url)
    await client.start_workflow(
        GraphWorkflow.run,
        {"execution_id": str(execution.id), "definition": definition, "context": context},
        id=execution.temporal_workflow_id,
        task_queue=TASK_QUEUE,
    )
    await db.commit()
    await db.refresh(execution)
    return execution


async def trigger_quote_sent_workflow(db: AsyncSession, organization_id: uuid.UUID, quotation: Quotation) -> None:
    """Fire-and-forget: a Temporal outage should never break sending a quote."""
    context = {
        "organization_id": str(organization_id),
        "quotation_id": str(quotation.id),
        "customer_id": str(quotation.customer_id),
    }
    result = await db.execute(
        select(Workflow).where(
            Workflow.organization_id == organization_id,
            Workflow.trigger_event == "quote.sent",
            Workflow.status == "PUBLISHED",
        )
    )
    published = result.scalar_one_or_none()

    if published:
        version = await _latest_version(db, published.id)
        definition, workflow_id, version_id = version.definition, published.id, version.id
    else:
        definition, workflow_id, version_id = DEFAULT_QUOTE_FOLLOWUP_DEFINITION, None, None

    try:
        await start_execution(db, organization_id, definition, context, workflow_id, version_id)
    except Exception:
        logger.warning("Failed to start quote.sent workflow for quotation %s", quotation.id, exc_info=True)


async def list_executions(db: AsyncSession, organization_id: uuid.UUID) -> list[WorkflowExecution]:
    result = await db.execute(
        select(WorkflowExecution)
        .where(WorkflowExecution.organization_id == organization_id)
        .order_by(WorkflowExecution.started_at.desc())
    )
    return list(result.scalars().all())
