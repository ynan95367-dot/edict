"""Tests for scripts/shiguan_eval.py — retroactive false-completion detector.

Buckets every historical Done task into:
* verified      — declared artifact is present & non-empty on disk
* would_veto    — declared a real path, but the artifact is missing/empty
* not_checkable — output is prose / empty (no on-disk contract; never a veto)
"""
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import shiguan_eval as se


def test_verified_when_artifact_present(tmp_path):
    f = tmp_path / "out.md"
    f.write_text("content", encoding="utf-8")
    row = se.classify({"id": "A", "state": "Done", "output": str(f)}, str(tmp_path))
    assert row["category"] == "verified"


def test_would_veto_when_path_declared_but_missing(tmp_path):
    row = se.classify({"id": "B", "state": "Done", "output": str(tmp_path / "gone.md")}, str(tmp_path))
    assert row["category"] == "would_veto"


def test_not_checkable_for_prose_output(tmp_path):
    row = se.classify({"id": "C", "state": "Done", "output": "工部健康检查完成"}, str(tmp_path))
    assert row["category"] == "not_checkable"


def test_not_checkable_for_empty_output(tmp_path):
    row = se.classify({"id": "D", "state": "Done", "output": ""}, str(tmp_path))
    assert row["category"] == "not_checkable"


def test_evaluate_done_only_counts_done_tasks(tmp_path):
    f = tmp_path / "ok.md"
    f.write_text("x", encoding="utf-8")
    tasks = [
        {"id": "A", "state": "Done", "output": str(f)},
        {"id": "B", "state": "Done", "output": str(tmp_path / "missing.md")},
        {"id": "C", "state": "Done", "output": "纯文字摘要"},
        {"id": "Z", "state": "Doing", "output": str(tmp_path / "missing2.md")},  # ignored
    ]
    report = se.evaluate_done(tasks, str(tmp_path))
    assert report["summary"]["total_done"] == 3
    assert report["summary"]["verified"] == 1
    assert report["summary"]["would_veto"] == 1
    assert report["summary"]["not_checkable"] == 1
