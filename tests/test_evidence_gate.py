"""Tests for scripts/evidence_gate.py — the non-LLM evidence gate.

The gate re-derives "done" from on-disk artifacts instead of trusting the
agent's self-report. The single most important correctness property is that it
MUST NOT false-veto a task whose `output` field is a prose description rather
than a real path (half the legacy `output` fields are prose).
"""
import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import evidence_gate as eg


# --- looks_like_path: the prose-vs-path discriminator (the false-veto guard) ---

def test_absolute_path_is_a_path():
    assert eg.looks_like_path("/docs/wechat-article.md") is True


def test_home_relative_path_is_a_path():
    assert eg.looks_like_path("~/Downloads/整理报告_20260613.md") is True


def test_bare_filename_with_extension_is_a_path():
    assert eg.looks_like_path("report.md") is True


def test_chinese_prose_is_not_a_path():
    assert eg.looks_like_path("工部健康检查完成") is False


def test_prose_summary_is_not_a_path():
    assert eg.looks_like_path("大模型新闻调研摘要") is False


def test_string_with_spaces_is_not_a_path():
    # Conservative: anything with an internal space is treated as prose, never
    # a path — false-veto avoidance beats catching the rare spaced path.
    assert eg.looks_like_path("见 outputs/foo.md") is False


def test_empty_is_not_a_path():
    assert eg.looks_like_path("") is False


# --- acceptance_for_done: derive the contract at completion time ---

def test_path_output_yields_required_nonempty_predicate():
    acc = eg.acceptance_for_done({}, "/tmp/out.md")
    types = [p["type"] for p in acc]
    assert eg.ARTIFACT_NONEMPTY in types
    pred = next(p for p in acc if p["type"] == eg.ARTIFACT_NONEMPTY)
    assert pred["tier"] == "required"
    assert pred["path"] == "/tmp/out.md"


def test_prose_output_yields_no_path_predicate():
    # THE guard: a prose deliverable must not produce a checkable path predicate.
    acc = eg.acceptance_for_done({}, "工部健康检查完成")
    assert all(p.get("path") is None for p in acc)
    assert not any(p["type"] in (eg.ARTIFACT_EXISTS, eg.ARTIFACT_NONEMPTY) for p in acc)


def test_empty_output_yields_no_predicates():
    assert eg.acceptance_for_done({}, "") == []


def test_explicit_acceptance_on_task_is_honored_and_path_bound():
    task = {"acceptance": [{"type": eg.ARTIFACT_NONEMPTY, "tier": "required"}]}
    acc = eg.acceptance_for_done(task, "/tmp/out.md")
    pred = next(p for p in acc if p["type"] == eg.ARTIFACT_NONEMPTY)
    assert pred["path"] == "/tmp/out.md"


# --- evaluate: pure decision over collected facts ---

def _facts(artifacts=None, diff_files=None):
    return {"artifacts": artifacts or {}, "diff_files": diff_files, "root": "/repo"}


def test_required_failure_makes_gate_not_ok():
    acc = [{"type": eg.ARTIFACT_NONEMPTY, "tier": "required", "path": "/a"}]
    facts = _facts(artifacts={"/a": {"exists": False, "size": 0}})
    res = eg.evaluate(acc, facts)
    assert res["ok"] is False
    assert eg.ARTIFACT_NONEMPTY in res["failed"]


def test_required_pass_makes_gate_ok():
    acc = [{"type": eg.ARTIFACT_NONEMPTY, "tier": "required", "path": "/a"}]
    facts = _facts(artifacts={"/a": {"exists": True, "size": 12}})
    res = eg.evaluate(acc, facts)
    assert res["ok"] is True
    assert res["failed"] == []


def test_advisory_failure_does_not_block():
    acc = [{"type": eg.DIFF_IN_SCOPE, "tier": "advisory", "scope": ["src/"]}]
    facts = _facts(diff_files=["other/x.py"])
    res = eg.evaluate(acc, facts)
    assert res["ok"] is True  # advisory never blocks
    rec = next(r for r in res["results"] if r["type"] == eg.DIFF_IN_SCOPE)
    assert rec["passed"] is False  # ...but it is recorded as failed


def test_existing_but_empty_artifact_fails_nonempty():
    acc = [{"type": eg.ARTIFACT_NONEMPTY, "tier": "required", "path": "/a"}]
    facts = _facts(artifacts={"/a": {"exists": True, "size": 0}})
    res = eg.evaluate(acc, facts)
    assert res["ok"] is False


def test_diff_nonempty_passes_when_changes_exist():
    acc = [{"type": eg.DIFF_NONEMPTY, "tier": "required"}]
    assert eg.evaluate(acc, _facts(diff_files=["src/a.py"]))["ok"] is True
    assert eg.evaluate(acc, _facts(diff_files=[]))["ok"] is False


def test_diff_in_scope_passes_when_all_changes_under_scope():
    acc = [{"type": eg.DIFF_IN_SCOPE, "tier": "required", "scope": ["src/"]}]
    assert eg.evaluate(acc, _facts(diff_files=["src/a.py", "src/b.py"]))["ok"] is True
    assert eg.evaluate(acc, _facts(diff_files=["src/a.py", "docs/x.md"]))["ok"] is False


def test_empty_acceptance_is_ok():
    # Nothing to verify (e.g. a plan task) → the gate does not block.
    assert eg.evaluate([], _facts())["ok"] is True


# --- collect_facts + gate: IO integration against a real tmp dir ---

def test_collect_facts_reflects_real_file(tmp_path):
    f = tmp_path / "out.md"
    f.write_text("hello", encoding="utf-8")
    acc = [{"type": eg.ARTIFACT_NONEMPTY, "tier": "required", "path": str(f)}]
    facts = eg.collect_facts(acc, str(f), str(tmp_path))
    assert facts["artifacts"][str(f)]["exists"] is True
    assert facts["artifacts"][str(f)]["size"] == 5


def test_gate_vetoes_missing_artifact(tmp_path):
    missing = tmp_path / "nope.md"
    res = eg.gate({}, str(missing), str(tmp_path))
    assert res["ok"] is False


def test_gate_passes_real_nonempty_artifact(tmp_path):
    f = tmp_path / "real.md"
    f.write_text("content", encoding="utf-8")
    res = eg.gate({}, str(f), str(tmp_path))
    assert res["ok"] is True


def test_gate_is_silent_on_prose_output(tmp_path):
    # A prose deliverable has nothing to check on disk → gate must pass.
    res = eg.gate({}, "工部健康检查完成", str(tmp_path))
    assert res["ok"] is True
