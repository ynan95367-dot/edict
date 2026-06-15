import logging
import os
import pathlib
import threading

from .base import TaskStore

log = logging.getLogger("edict.store")
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
    with _lock:
        _store = None


def get_task_store(data_dir: str | pathlib.Path | None = None) -> TaskStore:
    """按 EDICT_TASK_STORE 选实现（json|sqlite|shadow），进程内单例。

    `data_dir` 仅在首次构建时生效；后续调用传入的 `data_dir` 会被忽略。
    服务端应保证每次传入相同路径（或不传，依赖环境变量）。
    """
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
    if backend != "json":
        log.warning("unknown EDICT_TASK_STORE=%r, falling back to json", backend)
    return JsonTaskStore(json_path)
