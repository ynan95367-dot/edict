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
