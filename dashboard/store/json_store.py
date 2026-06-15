"""JsonTaskStore —— 包装现有 file_lock 行为，与旧 load/save 完全等价。"""
from __future__ import annotations

import logging
import pathlib
from typing import Any

log = logging.getLogger("edict.store")

from file_lock import atomic_json_read, atomic_json_update, atomic_json_write

from .base import TaskStore


class JsonTaskStore(TaskStore):
    def __init__(self, path: str | pathlib.Path):
        self.path = pathlib.Path(path)

    def load_tasks(self) -> list[dict[str, Any]]:
        data = atomic_json_read(self.path, [])
        return data if isinstance(data, list) else []

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        tasks = tasks if isinstance(tasks, list) else []
        kept = [t for t in tasks if t.get("id")]
        if len(kept) != len(tasks):
            log.warning("save_tasks dropped %d task(s) without 'id'", len(tasks) - len(kept))
        atomic_json_write(self.path, kept)

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
