import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dashboard"))
sys.path.insert(0, str(ROOT / "scripts"))


def _setup(srv, tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "DATA", tmp_path)
    monkeypatch.setattr(srv, "_ACTIVE_TASK_DATA_DIR", tmp_path)


def test_load_save_roundtrip_json(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "json")
    import store as store_pkg
    store_pkg.reset_task_store()
    import server as srv
    _setup(srv, tmp_path, monkeypatch)
    srv.save_tasks([{"id": "Z", "state": "Doing", "title": "t"}])
    assert (tmp_path / "tasks_source.json").exists()
    assert [t["id"] for t in srv.load_tasks()] == ["Z"]


def test_load_save_roundtrip_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("EDICT_TASK_STORE", "sqlite")
    import store as store_pkg
    store_pkg.reset_task_store()
    import server as srv
    _setup(srv, tmp_path, monkeypatch)
    srv.save_tasks([{"id": "Z", "state": "Doing", "title": "t"}])
    assert (tmp_path / "tasks.db").exists()
    assert [t["id"] for t in srv.load_tasks()] == ["Z"]
