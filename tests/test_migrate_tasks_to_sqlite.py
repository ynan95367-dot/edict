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
