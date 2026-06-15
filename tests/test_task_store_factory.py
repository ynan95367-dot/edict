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
    result = store_pkg.get_task_store(tmp_path)
    assert isinstance(result, ShadowTaskStore)
    assert isinstance(result.primary, JsonTaskStore)
    assert isinstance(result.secondary, SqliteTaskStore)


def test_factory_is_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "json")
    store_pkg.reset_task_store()
    a = store_pkg.get_task_store(tmp_path)
    b = store_pkg.get_task_store(tmp_path)
    assert a is b


def test_factory_unknown_value_falls_back_to_json(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("EDICT_TASK_STORE", "sqlit")  # typo
    store_pkg.reset_task_store()
    import logging
    with caplog.at_level(logging.WARNING, logger="edict.store"):
        s = store_pkg.get_task_store(tmp_path)
    assert isinstance(s, JsonTaskStore)
    assert any("unknown EDICT_TASK_STORE" in r.message for r in caplog.records)
