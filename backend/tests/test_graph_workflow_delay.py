"""One runnable check for the DELAY-node resume signal (see
graph_workflow.py's DELAY branch): a signal on the node id should unpark it
immediately, instead of forcing the caller to wait out the full duration —
this is what lets a sandbox chat (or an early customer reply) skip a 24h
quote-follow-up delay instead of getting stuck.
"""

import asyncio
import uuid

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.workflows.graph_workflow import GraphWorkflow

TASK_QUEUE = "test-delay-skip"

DEFINITION = {
    "nodes": [
        {"id": "t", "type": "TRIGGER", "config": {}},
        {"id": "d", "type": "DELAY", "config": {"duration_seconds": 86400}},  # 24h
        {"id": "e", "type": "END", "config": {}},
    ],
    "edges": [{"source": "t", "target": "d"}, {"source": "d", "target": "e"}],
}


@activity.defn(name="record_step")
async def _record_step(*_args) -> None:
    pass


@activity.defn(name="mark_execution_complete")
async def _mark_execution_complete(*_args) -> None:
    pass


@pytest.mark.asyncio
async def test_delay_signal_skips_the_wait():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client, task_queue=TASK_QUEUE,
            workflows=[GraphWorkflow], activities=[_record_step, _mark_execution_complete],
        ):
            handle = await env.client.start_workflow(
                GraphWorkflow.run,
                {"execution_id": str(uuid.uuid4()), "definition": DEFINITION, "context": {}},
                id=f"test-{uuid.uuid4()}", task_queue=TASK_QUEUE,
            )

            for _ in range(50):  # poll until the DELAY node actually parks
                if await handle.query(GraphWorkflow.waiting_for) == ["d"]:
                    break
                await asyncio.sleep(0.05)
            else:
                pytest.fail("DELAY node never parked")

            await handle.signal(GraphWorkflow.resume, args=["d", {}])

            # Real wall-clock timeout, not simulated time: if the signal didn't
            # actually skip the 24h sleep this would hang until the test runner
            # kills it, not resolve in a few seconds.
            result = await asyncio.wait_for(handle.result(), timeout=10)
            assert result["status"] == "COMPLETED"
