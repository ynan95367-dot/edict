# v2 Backend Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock down the dormant v2 FastAPI backend (`edict/backend`) by replacing wildcard CORS with a configurable whitelist and adding the missing service-layer test coverage (pure-logic + real-Postgres integration), wired into the existing CI job.

**Architecture:** CORS becomes a `Settings.cors_origins` field (env `CORS_ORIGINS`, comma-split) consumed by `main.py`. Tests split into pure-logic (no DB — state machine, `to_dict`, CORS parsing) and DB-integration (`TaskService` against the Postgres that CI already provisions; skipped locally when `DATABASE_URL` is unset). The CI `edict-backend` job already runs Postgres + Redis + `alembic upgrade head`; we add `pytest`.

**Tech Stack:** FastAPI, SQLAlchemy async, pydantic-settings, pytest, pytest-asyncio, Postgres (CI service).

**Working dir for all commands:** `/Users/bingsen/clawd/openclaw-sansheng-liubu`. The CI job and DB tests run with **`edict/backend`** as the working directory (so `from app.… import …` resolves; `app/__init__.py` exists). Working tree is clean — but still `git add` only the files each task names.

---

### Task 1: CORS whitelist (config + middleware + test)

**Files:**
- Modify: `edict/backend/app/config.py`
- Modify: `edict/backend/app/main.py:57-64`
- Create: `edict/backend/tests/test_config_cors.py`
- Create: `edict/backend/pytest.ini`
- Create: `edict/backend/requirements-dev.txt`

- [ ] **Step 1: Create pytest config + dev deps**

`edict/backend/pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
testpaths = tests
```

`edict/backend/requirements-dev.txt`:
```
pytest>=8.0
pytest-asyncio>=0.24
```

- [ ] **Step 2: Write the failing CORS test**

`edict/backend/tests/test_config_cors.py`:
```python
import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.config import Settings


def test_cors_default_includes_dashboard():
    s = Settings()
    assert "http://127.0.0.1:7891" in s.cors_origins
    assert "http://localhost:5173" in s.cors_origins


def test_cors_comma_split(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "http://a, http://b ,http://c")
    s = Settings()
    assert s.cors_origins == ["http://a", "http://b", "http://c"]


def test_cors_wildcard_preserved(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    s = Settings()
    assert s.cors_origins == ["*"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd edict/backend && pip install -r requirements-dev.txt && python -m pytest tests/test_config_cors.py -q`
Expected: FAIL — `Settings` has no `cors_origins` attribute.

- [ ] **Step 4: Add the `cors_origins` setting**

In `edict/backend/app/config.py`, add the import at top (with the other pydantic imports):
```python
from pydantic import Field, field_validator
```
Add this field inside `class Settings` (e.g. just after the `debug` field):
```python
    # ── CORS ──
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:7891",
        "http://127.0.0.1:7891",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd edict/backend && python -m pytest tests/test_config_cors.py -q`
Expected: PASS (3 passed).

- [ ] **Step 6: Wire the whitelist into middleware**

In `edict/backend/app/main.py`, replace the CORS block (currently lines 57-64):
```python
# CORS — 开发环境允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
with:
```python
# CORS — 可配置白名单（env CORS_ORIGINS，逗号分隔；默认本地开发源）
_cors_origins = get_settings().cors_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=("*" not in _cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)
```
(`get_settings` is already imported at `main.py:21`.)

- [ ] **Step 7: Verify app still imports**

Run: `cd edict/backend && python -c "from app.main import app; print(len(app.routes))"`
Expected: prints a route count, no error.

- [ ] **Step 8: Commit**

```bash
git add edict/backend/app/config.py edict/backend/app/main.py edict/backend/tests/test_config_cors.py edict/backend/pytest.ini edict/backend/requirements-dev.txt
git commit -m "feat(v2): configurable CORS whitelist + test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pure-logic tests (state machine + to_dict)

**Files:**
- Create: `edict/backend/tests/test_state_machine.py`
- Create: `edict/backend/tests/test_task_to_dict.py`

- [ ] **Step 1: Write the state-machine test**

`edict/backend/tests/test_state_machine.py`:
```python
import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models.task import (
    STATE_TRANSITIONS,
    TERMINAL_STATES,
    TaskState,
    Task,
    _contract_transitions,
)


def test_transitions_match_control_plane_contract():
    assert STATE_TRANSITIONS == _contract_transitions()


def test_terminal_states():
    assert TERMINAL_STATES == {TaskState.Done, TaskState.Cancelled}


def test_no_transitions_out_of_terminal_states():
    for t in TERMINAL_STATES:
        assert STATE_TRANSITIONS.get(t, set()) == set()


def test_taizi_flows_to_zhongshu_not_done():
    assert TaskState.Zhongshu in STATE_TRANSITIONS[TaskState.Taizi]
    assert TaskState.Done not in STATE_TRANSITIONS[TaskState.Taizi]


def test_org_for_state():
    assert Task.org_for_state(TaskState.Menxia) == "门下省"
    assert Task.org_for_state(TaskState.Doing, "兵部") == "兵部"
```

- [ ] **Step 2: Write the to_dict test**

`edict/backend/tests/test_task_to_dict.py`:
```python
import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models.task import Task, TaskState


def test_to_dict_shape_and_legacy_fields():
    t = Task(
        title="标题",
        state=TaskState.Review,
        flow_log=[{"from": "Zhongshu", "to": "Menxia"}],
        progress_log=[{"agent": "menxia"}],
        todos=[],
        scheduler={"retryCount": 1},
    )
    d = t.to_dict()
    assert d["title"] == "标题"
    assert d["state"] == "Review"
    assert d["flow_log"] == [{"from": "Zhongshu", "to": "Menxia"}]
    # legacy / old-frontend compatibility
    assert d["id"] == d["task_id"]
    assert d["_scheduler"] == {"retryCount": 1}
    assert "updatedAt" in d


def test_to_dict_handles_none_collections():
    t = Task(title="x", state=TaskState.Taizi)
    d = t.to_dict()
    assert d["tags"] == []
    assert d["todos"] == []
    assert d["meta"] == {}
    assert d["flow_log"] == []
```

- [ ] **Step 3: Run both, verify pass**

Run: `cd edict/backend && python -m pytest tests/test_state_machine.py tests/test_task_to_dict.py -q`
Expected: PASS (9 passed). These need no DB (in-memory model objects + module constants).

- [ ] **Step 4: Commit**

```bash
git add edict/backend/tests/test_state_machine.py edict/backend/tests/test_task_to_dict.py
git commit -m "test(v2): pure-logic state-machine + to_dict coverage

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: DB integration tests (real Postgres, skip without DATABASE_URL)

**Files:**
- Create: `edict/backend/tests/conftest.py`
- Create: `edict/backend/tests/test_task_service_db.py`

- [ ] **Step 1: Write the async DB fixture**

`edict/backend/tests/conftest.py`:
```python
import os
import pathlib
import sys

import pytest
import pytest_asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest_asyncio.fixture
async def session():
    """Per-test AsyncSession against the real test Postgres.

    Skips when DATABASE_URL is unset (local runs without a DB). Ensures tables
    exist (idempotent over alembic-migrated schema), truncates the tables the
    service touches, yields a session, disposes the engine afterward.
    """
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set; skipping DB integration tests")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from app.db import Base
    import app.models.task  # noqa: F401 — register tables on Base.metadata
    import app.models.outbox  # noqa: F401

    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE tasks, outbox_events RESTART IDENTITY CASCADE"))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Write the service tests**

`edict/backend/tests/test_task_service_db.py`:
```python
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
```

- [ ] **Step 3: Run locally (expect skip without DB)**

Run: `cd edict/backend && python -m pytest tests/test_task_service_db.py -q`
Expected: `5 skipped` (no local `DATABASE_URL`). To exercise for real against a local PG: `DATABASE_URL=postgresql+asyncpg://USER:PW@localhost:5432/DB python -m pytest tests/test_task_service_db.py -q` → 5 passed.

- [ ] **Step 4: Commit**

```bash
git add edict/backend/tests/conftest.py edict/backend/tests/test_task_service_db.py
git commit -m "test(v2): real-Postgres service-layer integration tests

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Run v2 tests in CI

**Files:**
- Modify: `.github/workflows/ci.yml` (the `edict-backend` job, after line 119 `alembic upgrade head`)

- [ ] **Step 1: Add pytest deps + run step to the existing edict-backend job**

In `.github/workflows/ci.yml`, in the `edict-backend` job, change the "Install backend dependencies" step (line 107-108) to also install dev deps:
```yaml
      - name: Install backend dependencies
        run: |
          pip install -r edict/backend/requirements.txt
          pip install -r edict/backend/requirements-dev.txt
```
Then, immediately after the existing "Run Alembic migrations" step (ends line 119), add a new step:
```yaml
      - name: Run backend tests
        working-directory: edict/backend
        env:
          DATABASE_URL: postgresql+asyncpg://edict:edict_dev_2024@localhost:5432/edict
        run: python -m pytest tests/ -v
```
(The `edict-backend` job already provisions postgres:16 + redis and sets `DATABASE_URL`; alembic has already created the schema by this point, so the DB tests run for real and the pure-logic tests run unconditionally.)

- [ ] **Step 2: Validate YAML locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml valid')"`
Expected: `ci.yml valid`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(v2): run backend pytest in edict-backend job

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

- **Spec coverage:** A.1 CORS → Task 1; A.2 pure-logic → Task 2; A.3 DB integration → Task 3; A.4 CI (⑧) → Task 4. All covered.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type/name consistency:** `cors_origins`, `_split_cors_origins`, `session` fixture, `_outbox` helper, topic strings (`task.created/status/completed/dispatch`) and `event_type` values match `task_service.py`/`event_bus.py`/`outbox.py` as read.
- **Note:** CI job already has Postgres+Redis+DATABASE_URL+alembic — Task 4 only adds deps + a pytest step (no service plumbing needed).
