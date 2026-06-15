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
