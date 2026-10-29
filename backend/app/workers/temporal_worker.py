import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import settings
from app.services.workflow_service import TASK_QUEUE
from app.workflows.activities import (
    evaluate_condition,
    execute_action,
    http_request,
    load_sub_workflow,
    mark_execution_complete,
    record_step,
    request_human_approval,
    request_human_handoff,
    run_ai_agent_node,
    run_inline_ai_agent_node,
    run_tool_node,
    send_message_node,
)
from app.workflows.graph_workflow import GraphWorkflow

# Every activity a node type can reach. A node whose activity isn't registered
# here doesn't fail fast — the task sits unassigned on the queue until it times
# out, which reads as a workflow that mysteriously stalls.
ACTIVITIES = [
    execute_action,
    evaluate_condition,
    record_step,
    mark_execution_complete,
    run_ai_agent_node,
    run_inline_ai_agent_node,
    run_tool_node,
    send_message_node,
    http_request,
    load_sub_workflow,
    request_human_approval,
    request_human_handoff,
]


async def main() -> None:
    client = await Client.connect(settings.temporal_url)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[GraphWorkflow],
        activities=ACTIVITIES,
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
