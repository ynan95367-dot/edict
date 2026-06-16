# TaskStore 抽象 + SQLite 迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `dashboard/server.py` 与持久化之间插入一个 `TaskStore` 抽象层，提供 JSON（现状）与 SQLite（目标）两种实现，通过环境变量灰度切换，以彻底消除「每次改任务都全量重写 1.5MB JSON」的写放大问题。

**Architecture:** 定义 `TaskStore` 接口（`load_tasks/save_tasks/get_task/upsert_task/delete_task/count`）。`JsonTaskStore` 包装现有 `file_lock` 行为，保持完全向后兼容；`SqliteTaskStore` 以「SQLite 文档表 + 少量索引列」模式存储（`data` 列存完整任务 JSON，`id/state/org/priority/updated_at/seq` 反范式出来供查询与保序），WAL 模式下支持并发。一个工厂按 `EDICT_TASK_STORE=json|sqlite|shadow` 选实现；`shadow` 模式双写两边、读主写副并记录分歧，用于生产灰度验证。先用「对拍测试」证明两实现可观测等价，再把 `server.py` 的 `load_tasks/save_tasks` 接到工厂——其余 80+ 调用点零改动即可受益。

**Tech Stack:** Python 3.9+，标准库 `sqlite3`（零新增依赖），现有 `scripts/file_lock.py`，pytest（项目已用，`pytest.ini` 设 `pythonpath=.`，`tests/conftest.py` 已把 `dashboard/` 和 `scripts/` 加入 `sys.path`）。

---

## 背景事实（实现者须知）

- 现状持久化（`dashboard/server.py:444-452`）：
  ```python
  def load_tasks():
      task_data_dir = get_task_data_dir()
      return atomic_json_read(task_data_dir / 'tasks_source.json', [])

  def save_tasks(tasks):
      task_data_dir = get_task_data_dir()
      atomic_json_write(task_data_dir / 'tasks_source.json', tasks)
      _trigger_refresh()
  ```
- 任务是 **dict 列表**，字段含 `id`（如 `JJC-20260228-E2E`）、`title`、`state`、`org`、`priority`、`archived`、`updatedAt`、嵌套的 `flow_log` / `progress_log` / `todos` / `_scheduler` 等。
- `file_lock` 在 `scripts/`；`server.py:26-27` 已 `sys.path.insert(0, scripts_dir)`，`tests/conftest.py` 亦然。新 `store` 包是 `dashboard/store/`，`server.py` 作为脚本运行时其所在目录 `dashboard/` 即 `sys.path[0]`，故 `from store import ...` 可用。
- 不要在本计划中改动 80+ 个直接调用 `load_tasks()/save_tasks()` 的业务处——它们透过工厂自动受益。把它们逐步迁移到 `upsert_task()` 单行写是**后续计划**，不在本范围。

## File Structure

- Create: `dashboard/store/__init__.py` — 工厂 `get_task_store()` + `reset_task_store()`
- Create: `dashboard/store/base.py` — `TaskStore` 抽象基类
- Create: `dashboard/store/json_store.py` — `JsonTaskStore`
- Create: `dashboard/store/sqlite_store.py` — `SqliteTaskStore`
- Create: `dashboard/store/shadow_store.py` — `ShadowTaskStore`（双写灰度）
- Create: `scripts/migrate_tasks_to_sqlite.py` — JSON→SQLite 迁移脚本
- Create: `tests/test_task_store_contract.py` — 跨实现契约测试
- Create: `tests/test_task_store_differential.py` — JSON↔SQLite 对拍测试
- Create: `tests/test_task_store_factory.py` — 工厂/灰度开关测试
- Create: `tests/test_migrate_tasks_to_sqlite.py` — 迁移脚本测试
- Modify: `dashboard/server.py:444-452` — `load_tasks/save_tasks` 委托工厂
- Modify: `.gitignore` — 忽略 `data/*.db`、`data/*.db-wal`、`data/*.db-shm`

---

### Task 1: 定义 TaskStore 契约与契约测试

**Files:**
- Create: `dashboard/store/base.py`
- Create: `dashboard/store/__init__.py`（先放占位，Task 5 补工厂）
- Test: `tests/test_task_store_contract.py`

- [ ] **Step 1: 写抽象基类**

`dashboard/store/base.py`:
```python
"""TaskStore 抽象 —— 任务持久化的统一接口。

上层（dashboard/server.py）只依赖本接口，不直接读写文件，
便于在 JSON / SQLite / Shadow 实现间灰度切换。
"""
from __future__ import annotations

import abc
from typing import Any


class TaskStore(abc.ABC):
    @abc.abstractmethod
    def load_tasks(self) -> list[dict[str, Any]]:
        """返回全部任务（保持保存时的顺序）。"""

    @abc.abstractmethod
    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        """整体替换任务集合（语义同旧的全量写：未出现的 id 被删除）。"""

    @abc.abstractmethod
    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """按 id 取单个任务，不存在返回 None。"""

    @abc.abstractmethod
    def upsert_task(self, task: dict[str, Any]) -> None:
        """插入或更新单个任务（按 id）。task 必须含非空 'id'。"""

    @abc.abstractmethod
    def delete_task(self, task_id: str) -> bool:
        """删除单个任务，删除成功返回 True，本不存在返回 False。"""

    @abc.abstractmethod
    def count(self) -> int:
        """任务总数。"""
```

- [ ] **Step 2: 写占位 `__init__.py`**

`dashboard/store/__init__.py`:
```python
from .base import TaskStore

__all__ = ["TaskStore"]
```

- [ ] **Step 3: 写契约测试（参数化覆盖两实现，先只让其可收集）**

`tests/test_task_store_contract.py`:
```python
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(params=["json", "sqlite"])
def store(request, tmp_path):
    if request.param == "json":
        from store.json_store import JsonTaskStore
        return JsonTaskStore(tmp_path / "tasks_source.json")
    from store.sqlite_store import SqliteTaskStore
    return SqliteTaskStore(tmp_path / "tasks.db")


def _by_id(items):
    return {t["id"]: t for t in items}


def test_empty_store_loads_empty(store):
    assert store.load_tasks() == []
    assert store.count() == 0


def test_save_then_load_roundtrip(store):
    tasks = [{"id": "A", "state": "Taizi", "title": "一"},
             {"id": "B", "state": "Doing", "title": "二"}]
    store.save_tasks(tasks)
    assert _by_id(store.load_tasks()) == _by_id(tasks)
    assert store.count() == 2


def test_upsert_inserts_and_updates(store):
    store.upsert_task({"id": "A", "state": "Taizi"})
    assert store.get_task("A")["state"] == "Taizi"
    store.upsert_task({"id": "A", "state": "Done"})
    assert store.get_task("A")["state"] == "Done"
    assert store.count() == 1


def test_get_missing_returns_none(store):
    assert store.get_task("nope") is None


def test_delete(store):
    store.upsert_task({"id": "A"})
    assert store.delete_task("A") is True
    assert store.delete_task("A") is False
    assert store.get_task("A") is None


def test_save_tasks_replaces_whole_set(store):
    store.save_tasks([{"id": "A"}, {"id": "B"}])
    store.save_tasks([{"id": "B"}, {"id": "C"}])
    assert set(_by_id(store.load_tasks())) == {"B", "C"}


def test_full_fidelity_nested_fields(store):
    task = {"id": "X", "state": "Review",
            "flow_log": [{"from": "Zhongshu", "to": "Menxia"}],
            "progress_log": [{"agent": "menxia", "tokens": 4500}],
            "_scheduler": {"retryCount": 1}}
    store.save_tasks([task])
    assert store.get_task("X") == task


def test_load_preserves_save_order(store):
    store.save_tasks([{"id": "C"}, {"id": "A"}, {"id": "B"}])
    assert [t["id"] for t in store.load_tasks()] == ["C", "A", "B"]
```

- [ ] **Step 4: 运行契约测试，确认按预期失败**

Run: `python -m pytest tests/test_task_store_contract.py -q`
Expected: 收集阶段或夹具导入 `store.json_store` / `store.sqlite_store` 失败（`ModuleNotFoundError`）——因为实现尚未创建。

- [ ] **Step 5: 提交**

```bash
git add dashboard/store/base.py dashboard/store/__init__.py tests/test_task_store_contract.py
git commit -m "feat(store): define TaskStore contract + contract tests"
```

---

### Task 2: 实现 JsonTaskStore（让 json 参数下契约全绿）

**Files:**
- Create: `dashboard/store/json_store.py`
- Test: `tests/test_task_store_contract.py`（复用 Task 1）

- [ ] **Step 1: 写实现**

`dashboard/store/json_store.py`:
```python
"""JsonTaskStore —— 包装现有 file_lock 行为，与旧 load/save 完全等价。"""
from __future__ import annotations

import pathlib
from typing import Any

from file_lock import atomic_json_read, atomic_json_update, atomic_json_write

from .base import TaskStore


class JsonTaskStore(TaskStore):
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)

    def load_tasks(self) -> list[dict[str, Any]]:
        data = atomic_json_read(self.path, [])
        return data if isinstance(data, list) else []

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        atomic_json_write(self.path, tasks if isinstance(tasks, list) else [])

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        for t in self.load_tasks():
            if str(t.get("id")) == str(task_id):
                return t
        return None

    def upsert_task(self, task: dict[str, Any]) -> None:
        tid = str(task.get("id") or "")
        if not tid:
            raise ValueError("task missing 'id'")

        def _mod(items):
            items = items if isinstance(items, list) else []
            for i, t in enumerate(items):
                if str(t.get("id")) == tid:
                    items[i] = task
                    return items
            items.append(task)
            return items

        atomic_json_update(self.path, _mod, [])

    def delete_task(self, task_id: str) -> bool:
        found = {"v": False}

        def _mod(items):
            items = items if isinstance(items, list) else []
            kept = [t for t in items if str(t.get("id")) != str(task_id)]
            found["v"] = len(kept) != len(items)
            return kept

        atomic_json_update(self.path, _mod, [])
        return found["v"]

    def count(self) -> int:
        return len(self.load_tasks())
```

- [ ] **Step 2: 只跑 json 参数，确认通过**

Run: `python -m pytest "tests/test_task_store_contract.py" -q -k "json"`
Expected: PASS（sqlite 参数仍会 error，因为实现未建——下一任务处理）。

- [ ] **Step 3: 提交**

```bash
git add dashboard/store/json_store.py
git commit -m "feat(store): JsonTaskStore wrapping file_lock (contract-green for json)"
```

---

### Task 3: 实现 SqliteTaskStore（让 sqlite 参数下契约全绿）

**Files:**
- Create: `dashboard/store/sqlite_store.py`
- Test: `tests/test_task_store_contract.py`（复用 Task 1）

- [ ] **Step 1: 写实现**

`dashboard/store/sqlite_store.py`:
```python
"""SqliteTaskStore —— SQLite 文档表实现。

设计要点：
- `data` 列存完整任务 JSON（保真，避免为众多任务字段做 schema 迁移）；
- `id/state/org/priority/updated_at` 反范式出来供后续 SQL 查询与索引；
- `seq` 保存写入顺序，使 load_tasks 顺序与旧 JSON 行为一致；
- WAL + busy_timeout，读写并发不再单锁串行；
- 每线程一个连接（http.server 多线程），check_same_thread=False。
"""
from __future__ import annotations

import json
import pathlib
import threading
from typing import Any

from .base import TaskStore


class SqliteTaskStore(TaskStore):
    def __init__(self, db_path: str | pathlib.Path):
        self.db_path = pathlib.Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()

    def _conn(self):
        import sqlite3
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return conn

    def _init_schema(self):
        conn = self._conn()
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id          TEXT PRIMARY KEY,
                seq         INTEGER,
                state       TEXT,
                org         TEXT,
                priority    TEXT,
                archived    INTEGER DEFAULT 0,
                updated_at  TEXT,
                data        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
            CREATE INDEX IF NOT EXISTS idx_tasks_updated ON tasks(updated_at);
            CREATE INDEX IF NOT EXISTS idx_tasks_seq ON tasks(seq);
            """
        )
        conn.commit()

    @staticmethod
    def _cols(task: dict[str, Any], seq: int):
        return (
            str(task.get("id") or ""),
            seq,
            str(task.get("state") or ""),
            str(task.get("org") or ""),
            str(task.get("priority") or ""),
            1 if task.get("archived") else 0,
            str(task.get("updatedAt") or ""),
            json.dumps(task, ensure_ascii=False),
        )

    _UPSERT = (
        "INSERT INTO tasks (id,seq,state,org,priority,archived,updated_at,data) "
        "VALUES (?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "seq=excluded.seq, state=excluded.state, org=excluded.org, "
        "priority=excluded.priority, archived=excluded.archived, "
        "updated_at=excluded.updated_at, data=excluded.data"
    )

    def load_tasks(self) -> list[dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT data FROM tasks ORDER BY seq ASC, id ASC"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        tasks = tasks if isinstance(tasks, list) else []
        incoming = [t for t in tasks if t.get("id")]
        incoming_ids = [str(t.get("id")) for t in incoming]
        conn = self._conn()
        try:
            conn.execute("BEGIN")
            for i, t in enumerate(incoming):
                conn.execute(self._UPSERT, self._cols(t, i))
            if incoming_ids:
                ph = ",".join("?" * len(incoming_ids))
                conn.execute(f"DELETE FROM tasks WHERE id NOT IN ({ph})", incoming_ids)
            else:
                conn.execute("DELETE FROM tasks")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._conn().execute(
            "SELECT data FROM tasks WHERE id=?", (str(task_id),)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def upsert_task(self, task: dict[str, Any]) -> None:
        tid = str(task.get("id") or "")
        if not tid:
            raise ValueError("task missing 'id'")
        conn = self._conn()
        row = conn.execute("SELECT seq FROM tasks WHERE id=?", (tid,)).fetchone()
        if row is not None and row["seq"] is not None:
            seq = row["seq"]
        else:
            mx = conn.execute("SELECT MAX(seq) AS m FROM tasks").fetchone()["m"]
            seq = 0 if mx is None else mx + 1
        conn.execute(self._UPSERT, self._cols(task, seq))
        conn.commit()

    def delete_task(self, task_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (str(task_id),))
        conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
```

- [ ] **Step 2: 跑完整契约测试（两实现都应通过）**

Run: `python -m pytest tests/test_task_store_contract.py -q`
Expected: PASS（json + sqlite 全部用例通过，含 `test_load_preserves_save_order`、`test_full_fidelity_nested_fields`）。

- [ ] **Step 3: 提交**

```bash
git add dashboard/store/sqlite_store.py
git commit -m "feat(store): SqliteTaskStore (WAL, document table) passes contract"
```

---

### Task 4: JSON↔SQLite 对拍测试（同操作序列观测等价）

**Files:**
- Test: `tests/test_task_store_differential.py`

- [ ] **Step 1: 写对拍测试**

`tests/test_task_store_differential.py`:
```python
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

from store.json_store import JsonTaskStore
from store.sqlite_store import SqliteTaskStore


def _by_id(items):
    return {t["id"]: t for t in items}


_METHOD = {"save": "save_tasks", "upsert": "upsert_task", "delete": "delete_task"}


def test_json_and_sqlite_observably_identical(tmp_path):
    j = JsonTaskStore(tmp_path / "t.json")
    s = SqliteTaskStore(tmp_path / "t.db")
    ops = [
        ("save", [{"id": "A", "state": "Taizi"}, {"id": "B", "state": "Doing"}]),
        ("upsert", {"id": "A", "state": "Zhongshu", "flow_log": [{"to": "Zhongshu"}]}),
        ("upsert", {"id": "C", "state": "Menxia"}),
        ("delete", "B"),
        ("save", [{"id": "C", "state": "Done"}, {"id": "D", "state": "Pending"}]),
    ]
    for op, arg in ops:
        getattr(j, _METHOD[op])(arg)
        getattr(s, _METHOD[op])(arg)
        assert _by_id(j.load_tasks()) == _by_id(s.load_tasks()), f"diverged after {op}"
        assert j.count() == s.count()
        assert [t["id"] for t in j.load_tasks()] == [t["id"] for t in s.load_tasks()]
```

- [ ] **Step 2: 运行，确认通过**

Run: `python -m pytest tests/test_task_store_differential.py -q`
Expected: PASS。

- [ ] **Step 3: 提交**

```bash
git add tests/test_task_store_differential.py
git commit -m "test(store): differential equivalence json vs sqlite"
```

---

### Task 5: 工厂 + 灰度开关（含 ShadowTaskStore）

**Files:**
- Create: `dashboard/store/shadow_store.py`
- Modify: `dashboard/store/__init__.py`
- Test: `tests/test_task_store_factory.py`

- [ ] **Step 1: 写 ShadowTaskStore（双写、读主、记录分歧）**

`dashboard/store/shadow_store.py`:
```python
"""ShadowTaskStore —— 灰度双写：写两边、读主端、记录分歧。

用于生产切换前验证 SQLite 与 JSON 行为一致而不承担风险：
- 写：主 + 副都写（副失败仅告警，不影响主）；
- 读：返回主端结果，并比对副端、记录 divergence。
"""
from __future__ import annotations

import logging
from typing import Any

from .base import TaskStore

log = logging.getLogger("edict.store.shadow")


def _by_id(items):
    return {str(t.get("id")): t for t in items if isinstance(t, dict)}


class ShadowTaskStore(TaskStore):
    def __init__(self, primary: TaskStore, secondary: TaskStore):
        self.primary = primary
        self.secondary = secondary

    def load_tasks(self) -> list[dict[str, Any]]:
        p = self.primary.load_tasks()
        try:
            s = self.secondary.load_tasks()
            if _by_id(p) != _by_id(s):
                log.warning("shadow divergence: primary=%d secondary=%d", len(p), len(s))
        except Exception as e:  # noqa: BLE001
            log.warning("shadow secondary read failed: %s", e)
        return p

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.primary.save_tasks(tasks)
        try:
            self.secondary.save_tasks(tasks)
        except Exception as e:  # noqa: BLE001
            log.warning("shadow secondary save failed: %s", e)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.primary.get_task(task_id)

    def upsert_task(self, task: dict[str, Any]) -> None:
        self.primary.upsert_task(task)
        try:
            self.secondary.upsert_task(task)
        except Exception as e:  # noqa: BLE001
            log.warning("shadow secondary upsert failed: %s", e)

    def delete_task(self, task_id: str) -> bool:
        r = self.primary.delete_task(task_id)
        try:
            self.secondary.delete_task(task_id)
        except Exception as e:  # noqa: BLE001
            log.warning("shadow secondary delete failed: %s", e)
        return r

    def count(self) -> int:
        return self.primary.count()
```

- [ ] **Step 2: 写工厂**

`dashboard/store/__init__.py`（整体替换为）:
```python
import os
import pathlib
import threading

from .base import TaskStore
from .json_store import JsonTaskStore
from .sqlite_store import SqliteTaskStore
from .shadow_store import ShadowTaskStore

__all__ = [
    "TaskStore", "JsonTaskStore", "SqliteTaskStore", "ShadowTaskStore",
    "get_task_store", "reset_task_store",
]

_store: TaskStore | None = None
_lock = threading.Lock()


def reset_task_store() -> None:
    """测试用：清掉单例。"""
    global _store
    _store = None


def get_task_store(data_dir: str | pathlib.Path | None = None) -> TaskStore:
    """按 EDICT_TASK_STORE 选实现（json|sqlite|shadow），进程内单例。"""
    global _store
    if _store is not None:
        return _store
    with _lock:
        if _store is None:
            _store = _build_store(data_dir)
    return _store


def _build_store(data_dir) -> TaskStore:
    base = pathlib.Path(
        data_dir
        or os.environ.get("EDICT_DATA_DIR")
        or (pathlib.Path(__file__).resolve().parents[2] / "data")
    )
    backend = (os.environ.get("EDICT_TASK_STORE") or "json").strip().lower()
    json_path = base / "tasks_source.json"
    db_path = base / "tasks.db"
    if backend == "sqlite":
        return SqliteTaskStore(db_path)
    if backend == "shadow":
        return ShadowTaskStore(JsonTaskStore(json_path), SqliteTaskStore(db_path))
    return JsonTaskStore(json_path)
```

- [ ] **Step 3: 写工厂测试**

`tests/test_task_store_factory.py`:
```python
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))

import store as store_pkg
from store.json_store import JsonTaskStore
from store.sqlite_store import SqliteTaskStore
from store.shadow_store import ShadowTaskStore


def test_factory_defaults_to_json(tmp_path, monkeypatch):
    monkeypatch.delenv("EDICT_TASK_STORE", raising=False)
    store_pkg.reset_task_store()
    assert isinstance(store_pkg.get_task_store(tmp_path), JsonTaskStore)


def test_factory_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "sqlite")
    store_pkg.reset_task_store()
    assert isinstance(store_pkg.get_task_store(tmp_path), SqliteTaskStore)


def test_factory_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "shadow")
    store_pkg.reset_task_store()
    assert isinstance(store_pkg.get_task_store(tmp_path), ShadowTaskStore)


def test_factory_is_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "json")
    store_pkg.reset_task_store()
    a = store_pkg.get_task_store(tmp_path)
    b = store_pkg.get_task_store(tmp_path)
    assert a is b
```

- [ ] **Step 4: 运行工厂测试**

Run: `python -m pytest tests/test_task_store_factory.py -q`
Expected: PASS（4 个用例）。

- [ ] **Step 5: 提交**

```bash
git add dashboard/store/shadow_store.py dashboard/store/__init__.py tests/test_task_store_factory.py
git commit -m "feat(store): factory + EDICT_TASK_STORE flag (json|sqlite|shadow)"
```

---

### Task 6: 迁移脚本 JSON→SQLite

**Files:**
- Create: `scripts/migrate_tasks_to_sqlite.py`
- Test: `tests/test_migrate_tasks_to_sqlite.py`

- [ ] **Step 1: 写失败测试**

`tests/test_migrate_tasks_to_sqlite.py`:
```python
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))


def test_migration_copies_all_tasks(tmp_path):
    (tmp_path / "tasks_source.json").write_text(
        json.dumps([{"id": "A", "state": "Doing"}, {"id": "B", "state": "Done"}]),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "migrate_tasks_to_sqlite.py"),
         "--data-dir", str(tmp_path)],
        check=True,
    )
    from store.sqlite_store import SqliteTaskStore
    s = SqliteTaskStore(tmp_path / "tasks.db")
    assert s.count() == 2
    assert s.get_task("A")["state"] == "Doing"


def test_migration_is_idempotent(tmp_path):
    (tmp_path / "tasks_source.json").write_text(
        json.dumps([{"id": "A"}]), encoding="utf-8")
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "migrate_tasks_to_sqlite.py"),
             "--data-dir", str(tmp_path)],
            check=True,
        )
    from store.sqlite_store import SqliteTaskStore
    assert SqliteTaskStore(tmp_path / "tasks.db").count() == 1
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_migrate_tasks_to_sqlite.py -q`
Expected: FAIL（脚本不存在，subprocess 返回非零，`CalledProcessError`）。

- [ ] **Step 3: 写迁移脚本**

`scripts/migrate_tasks_to_sqlite.py`:
```python
#!/usr/bin/env python3
"""Migrate tasks_source.json → tasks.db (SQLite). 幂等。"""
from __future__ import annotations

import argparse
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))                      # scripts/ (file_lock)
sys.path.insert(0, str(_HERE.parent / "dashboard"))  # dashboard/ (store)

from store.json_store import JsonTaskStore   # noqa: E402
from store.sqlite_store import SqliteTaskStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(_HERE.parent / "data"))
    args = ap.parse_args()

    data_dir = pathlib.Path(args.data_dir)
    src = JsonTaskStore(data_dir / "tasks_source.json")
    dst = SqliteTaskStore(data_dir / "tasks.db")
    tasks = src.load_tasks()
    dst.save_tasks(tasks)
    print(f"migrated {len(tasks)} tasks → {data_dir / 'tasks.db'} (now {dst.count()})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行，确认通过**

Run: `python -m pytest tests/test_migrate_tasks_to_sqlite.py -q`
Expected: PASS（2 个用例）。

- [ ] **Step 5: 提交**

```bash
git add scripts/migrate_tasks_to_sqlite.py tests/test_migrate_tasks_to_sqlite.py
git commit -m "feat(store): idempotent JSON->SQLite migration script"
```

---

### Task 7: 接入 server.py 并忽略 db 文件

**Files:**
- Modify: `dashboard/server.py:444-452`
- Modify: `.gitignore`

- [ ] **Step 1: 改 `load_tasks`/`save_tasks` 委托工厂**

将 `dashboard/server.py:444-452` 的:
```python
def load_tasks():
    task_data_dir = get_task_data_dir()
    return atomic_json_read(task_data_dir / 'tasks_source.json', [])


def save_tasks(tasks):
    task_data_dir = get_task_data_dir()
    atomic_json_write(task_data_dir / 'tasks_source.json', tasks)
    _trigger_refresh()
```
替换为:
```python
from store import get_task_store


def load_tasks():
    return get_task_store(get_task_data_dir()).load_tasks()


def save_tasks(tasks):
    get_task_store(get_task_data_dir()).save_tasks(tasks)
    _trigger_refresh()
```

- [ ] **Step 2: 忽略 SQLite 运行文件**

在 `.gitignore` 末尾追加:
```
# SQLite task store (Task 7)
data/*.db
data/*.db-wal
data/*.db-shm
```

- [ ] **Step 3: 默认（json）回归——确认行为零变化**

Run: `python -m pytest tests/test_server.py tests/test_kanban.py tests/test_task_mutation_race.py -q`
Expected: PASS（未设 `EDICT_TASK_STORE`，默认走 JsonTaskStore，等价旧行为）。

- [ ] **Step 4: sqlite 模式跑同一批用例**

Run: `EDICT_TASK_STORE=sqlite python -m pytest tests/test_server.py tests/test_kanban.py tests/test_task_mutation_race.py -q`
Expected: PASS（若个别用例直接断言 `tasks_source.json` 文件内容而非经 `load_tasks()` 读取，则在此暴露——按断言改为经 store 读取，不要改回文件断言）。

- [ ] **Step 5: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add dashboard/server.py .gitignore
git commit -m "feat(store): route server.py load/save_tasks through TaskStore factory"
```

---

## 灰度上线 Runbook（实现完成后，按序执行）

1. **影子验证**：以 `EDICT_TASK_STORE=shadow` 启动 → 先 `python scripts/migrate_tasks_to_sqlite.py` 把现有 JSON 灌入 db → 正常使用一段时间，`grep "shadow divergence" logs/*` 应为空。
2. **切换**：确认无分歧后改 `EDICT_TASK_STORE=sqlite` 重启。`data/tasks_source.json` 作为回滚保险保留。
3. **回滚**：异常时改回 `EDICT_TASK_STORE=json`（或不设）重启即可，因影子期 JSON 一直在同步写。

> 本计划范围**只建立持久化接缝并默认行为不变**；将 80+ 处 `load_tasks()→修改→save_tasks()` 调用点逐步改写为 `store.upsert_task(单任务)` 以兑现「单行写」全部收益，是后续独立计划。

---

## Self-Review

- **Spec coverage**：表结构 ✅（Task 3）、对拍测试 ✅（Task 4）、灰度开关 ✅（Task 5 的 `EDICT_TASK_STORE` + `shadow`）、迁移 ✅（Task 6）、接入 ✅（Task 7）。
- **Placeholder scan**：无 TODO/TBD，每个代码步骤均为完整可粘贴代码。
- **Type consistency**：接口方法名 `load_tasks/save_tasks/get_task/upsert_task/delete_task/count` 在 base、json、sqlite、shadow、工厂、测试中一致；`_UPSERT` 列顺序与 `_cols()` 返回顺序一致（id,seq,state,org,priority,archived,updated_at,data）。
