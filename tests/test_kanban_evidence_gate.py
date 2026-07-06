"""Integration: the evidence gate wired into kanban_update.cmd_done.

Behaviour contract:
* enforce ON  + artifact missing  -> task is VETOED (stays in Doing)
* enforce OFF + artifact missing   -> task advances to Review (signal recorded, not blocked)
* enforce ON  + prose output       -> NOT vetoed (nothing checkable on disk)
* enforce ON  + real nonempty file -> advances to Review
"""
import json
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kanban_update as kb


def _seed(tmp_path):
    tasks_file = tmp_path / "tasks_source.json"
    tasks_file.write_text(
        json.dumps([{"id": "T-1", "title": "x", "state": "Doing", "org": "工部"}], ensure_ascii=False),
        encoding="utf-8",
    )
    return tasks_file


def _state(tasks_file):
    return json.loads(tasks_file.read_text(encoding="utf-8"))[0]["state"]


def test_done_vetoed_when_enforcing_and_artifact_missing(tmp_path, monkeypatch):
    tasks_file = _seed(tmp_path)
    monkeypatch.setattr(kb, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(kb, "EVIDENCE_GATE_ENFORCE", True)
    kb.cmd_done("T-1", str(tmp_path / "nope.md"), "done")
    assert _state(tasks_file) == "Doing"


def test_done_allowed_when_not_enforcing_even_if_missing(tmp_path, monkeypatch):
    tasks_file = _seed(tmp_path)
    monkeypatch.setattr(kb, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(kb, "EVIDENCE_GATE_ENFORCE", False)
    kb.cmd_done("T-1", str(tmp_path / "nope.md"), "done")
    assert _state(tasks_file) == "Review"


def test_done_not_vetoed_for_prose_output_when_enforcing(tmp_path, monkeypatch):
    tasks_file = _seed(tmp_path)
    monkeypatch.setattr(kb, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(kb, "EVIDENCE_GATE_ENFORCE", True)
    kb.cmd_done("T-1", "工部健康检查完成", "done")
    assert _state(tasks_file) == "Review"


def test_done_passes_real_artifact_when_enforcing(tmp_path, monkeypatch):
    tasks_file = _seed(tmp_path)
    monkeypatch.setattr(kb, "TASKS_FILE", tasks_file)
    monkeypatch.setattr(kb, "EVIDENCE_GATE_ENFORCE", True)
    out = tmp_path / "real.md"
    out.write_text("hi", encoding="utf-8")
    kb.cmd_done("T-1", str(out), "done")
    assert _state(tasks_file) == "Review"
