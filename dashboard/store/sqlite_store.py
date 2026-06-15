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
import logging
import pathlib
import threading
from typing import Any

log = logging.getLogger("edict.store")

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
        # Tasks without 'id' cannot be stored (id is the PK); drop with a warning,
        # consistent with JsonTaskStore. See test_save_tasks_drops_tasks_without_id.
        incoming = [t for t in tasks if t.get("id")]
        if len(incoming) != len(tasks):
            log.warning("save_tasks dropped %d task(s) without 'id'", len(tasks) - len(incoming))
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
        try:
            conn.execute("BEGIN")
            row = conn.execute("SELECT seq FROM tasks WHERE id=?", (tid,)).fetchone()
            if row is not None and row["seq"] is not None:
                seq = row["seq"]
            else:
                mx = conn.execute("SELECT MAX(seq) AS m FROM tasks").fetchone()["m"]
                seq = 0 if mx is None else mx + 1
            conn.execute(self._UPSERT, self._cols(task, seq))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def delete_task(self, task_id: str) -> bool:
        conn = self._conn()
        cur = conn.execute("DELETE FROM tasks WHERE id=?", (str(task_id),))
        conn.commit()
        return cur.rowcount > 0

    def count(self) -> int:
        return self._conn().execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"]
