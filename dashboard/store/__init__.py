import logging
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

log = logging.getLogger("edict.store")
_stores: dict[tuple[str, str], TaskStore] = {}
_lock = threading.Lock()


def reset_task_store() -> None:
    """测试用：清掉所有缓存的 store 实例。"""
    global _stores
    with _lock:
        _stores = {}


def _resolve_data_dir(data_dir) -> pathlib.Path:
    return pathlib.Path(
        data_dir
        or os.environ.get("EDICT_DATA_DIR")
        or (pathlib.Path(__file__).resolve().parents[2] / "data")
    )


def get_task_store(data_dir=None) -> TaskStore:
    """按 EDICT_TASK_STORE 选实现（json|sqlite|shadow）。

    按 (backend, 解析后的 data_dir) 缓存实例：相同上下文复用同一实例
    （SQLite 连接池得以复用），不同 data_dir（如各测试的 tmp 目录）互不干扰。
    """
    base = _resolve_data_dir(data_dir).resolve()
    backend = (os.environ.get("EDICT_TASK_STORE") or "json").strip().lower()
    key = (backend, str(base))
    s = _stores.get(key)
    if s is not None:
        return s
    with _lock:
        s = _stores.get(key)
        if s is None:
            s = _build_store(backend, base)
            _stores[key] = s
        return s


def _build_store(backend: str, base: pathlib.Path) -> TaskStore:
    json_path = base / "tasks_source.json"
    db_path = base / "tasks.db"
    if backend == "sqlite":
        return SqliteTaskStore(db_path)
    if backend == "shadow":
        return ShadowTaskStore(JsonTaskStore(json_path), SqliteTaskStore(db_path))
    if backend != "json":
        log.warning("unknown EDICT_TASK_STORE=%r, falling back to json", backend)
    return JsonTaskStore(json_path)
