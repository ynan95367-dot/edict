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
