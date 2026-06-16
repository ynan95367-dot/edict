import pathlib
import sys

import pytest
from sqlalchemy import select

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models.outbox import OutboxEvent
from app.models.task import TaskState
from app.services.task_service import TaskService


async def _outbox(session, **filters):
    stmt = select(OutboxEvent)
    for k, v in filters.items():
        stmt = stmt.where(getattr(OutboxEvent, k) == v)
    return (await session.execute(stmt)).scalars().all()


async def test_create_task_writes_outbox_same_txn(session):
    svc = TaskService(session)
    task = await svc.create_task(title="t", initial_state=TaskState.Taizi)
    assert task.task_id is not None
    rows = await _outbox(session, event_type="task.created")
    assert len(rows) == 1
    assert rows[0].topic == "task.created"
    assert rows[0].payload["state"] == "Taizi"


async def test_valid_transition_updates_state_flow_and_outbox(session):
    svc = TaskService(session)
    task = await svc.create_task(title="t", initial_state=TaskState.Taizi)
    updated = await svc.transition_state(
        task.task_id, TaskState.Zhongshu, agent="taizi", reason="go"
    )
    assert updated.state == TaskState.Zhongshu
    assert updated.flow_log[-1]["to"] == "Zhongshu"
    assert updated.flow_log[-1]["agent"] == "taizi"
    rows = await _outbox(session, topic="task.status")
    assert len(rows) == 1


async def test_invalid_transition_raises(session):
    svc = TaskService(session)
    task = await svc.create_task(title="t", initial_state=TaskState.Taizi)
    with pytest.raises(ValueError):
        await svc.transition_state(task.task_id, TaskState.Done)


async def test_terminal_transition_uses_completed_topic(session):
    svc = TaskService(session)
    task = await svc.create_task(title="t", initial_state=TaskState.Doing)
    await svc.transition_state(task.task_id, TaskState.Done, reason="done")
    rows = await _outbox(session, topic="task.completed")
    assert len(rows) == 1


async def test_request_dispatch_emits_dispatch_event(session):
    svc = TaskService(session)
    task = await svc.create_task(title="t", initial_state=TaskState.Assigned)
    await svc.request_dispatch(task.task_id, "zhongshu", "go")
    rows = await _outbox(session, event_type="task.dispatch.request")
    assert len(rows) == 1
    assert rows[0].topic == "task.dispatch"
