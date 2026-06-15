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
