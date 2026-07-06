"""Non-LLM evidence gate: re-derive "done" from on-disk artifacts.

The executing agent self-reports completion. This module is the independent
half of the control plane: given a task's acceptance contract and the
runtime-declared output, it decides whether reality matches the contract — by
checking the filesystem and git, never by trusting the agent's word.

Design notes
------------
* Pure decision (`evaluate`) is separated from IO probing (`collect_facts`) so
  the decision logic is unit-testable without a filesystem.
* Predicates carry a `tier`: ``required`` predicates can veto a transition;
  ``advisory`` predicates are recorded but never block (used for fuzzy checks
  like diff-scope that we do not yet trust enough to fail a task on).
* The single most important correctness property is the prose-vs-path guard in
  `looks_like_path`: roughly half of legacy `output` fields are descriptions
  ("工部健康检查完成"), not paths. Emitting a path predicate for those would
  false-veto legitimate work, so we never do.

No LLM, no network, no heavy dependencies.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Dict, List, Optional

# --- predicate type vocabulary (small and fixed: the reliability surface) ---
ARTIFACT_EXISTS = "artifact_exists"
ARTIFACT_NONEMPTY = "artifact_nonempty"
DIFF_NONEMPTY = "diff_nonempty"
DIFF_IN_SCOPE = "diff_in_scope"

_PATH_PREDICATES = (ARTIFACT_EXISTS, ARTIFACT_NONEMPTY)
_DIFF_PREDICATES = (DIFF_NONEMPTY, DIFF_IN_SCOPE)

_REQUIRED = "required"
_ADVISORY = "advisory"

_EXT_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")
_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def looks_like_path(value: str) -> bool:
    """Conservatively decide whether a string is a real path vs prose.

    Bias hard toward False (prose): a missed real path costs us a check; a prose
    string mistaken for a path costs us a false veto on legitimate work.
    """
    s = (value or "").strip()
    if not s or " " in s:
        # Any internal space => treat as prose. Real output paths in this repo
        # never contain spaces; the safe direction is to under-claim.
        return False
    if s.startswith(("/", "~", "./", "../")):
        return True
    if _WIN_DRIVE_RE.match(s):
        return True
    if "/" in s or "\\" in s:
        return True
    if _EXT_RE.search(s):  # bare filename like report.md
        return True
    return False


# --- contract derivation -----------------------------------------------------

def _bind_paths(acceptance: List[Dict[str, Any]], output_path: str) -> List[Dict[str, Any]]:
    """Fill in `path` for path predicates that declared none, using output_path."""
    bound: List[Dict[str, Any]] = []
    for pred in acceptance:
        pred = dict(pred)
        if pred.get("type") in _PATH_PREDICATES and not pred.get("path"):
            if output_path and looks_like_path(output_path):
                pred["path"] = output_path
            else:
                # No real path to check against: demote to advisory so a prose
                # deliverable can never be vetoed by an unbindable predicate.
                pred["tier"] = _ADVISORY
                pred["path"] = None
        bound.append(pred)
    return bound


def acceptance_for_done(task: Dict[str, Any], output_path: str) -> List[Dict[str, Any]]:
    """The contract to enforce at completion time.

    Prefers an explicit `acceptance` already attached to the task (e.g. compiled
    by the RunSpec at creation time); otherwise derives a minimal one from the
    declared output. Returns ``[]`` when there is nothing verifiable on disk.
    """
    existing = task.get("acceptance")
    if isinstance(existing, list) and existing:
        return _bind_paths(existing, output_path)

    if output_path and looks_like_path(output_path):
        return [{"type": ARTIFACT_NONEMPTY, "tier": _REQUIRED, "path": output_path}]
    return []


def acceptance_for_runspec(run_kind: str, capability_ids, mode: str, deliverable: str = "") -> List[Dict[str, Any]]:
    """The contract a RunSpec declares up front (paths bound later, at done-time).

    Deterministic, mirrors the keyword/run_kind logic already used by
    ``_infer_deliverable`` in the dashboard. Honest about its limits: generic
    "summary" tasks get an empty contract rather than a fake check.
    """
    caps = set(capability_ids or [])
    if mode == "plan":
        return []  # a plan produces an argument, not an on-disk artifact
    if run_kind in ("coding", "system"):
        return [
            {"type": DIFF_NONEMPTY, "tier": _REQUIRED},
            {"type": DIFF_IN_SCOPE, "tier": _ADVISORY},  # scope detection still fuzzy
        ]
    text = (deliverable or "").lower()
    doc_words = ("ppt", "pptx", "excel", "xlsx", "word", "docx", "文件", "文档",
                 "表格", "文章", "报告", "截图", "补丁")
    if "artifact.outputs" in caps or any(w in text for w in doc_words):
        # Path bound at done-time from the runtime-declared output.
        return [{"type": ARTIFACT_NONEMPTY, "tier": _REQUIRED}]
    return []


# --- pure evaluation ---------------------------------------------------------

def _check(pred: Dict[str, Any], facts: Dict[str, Any]) -> (bool, str):
    ptype = pred.get("type")
    artifacts = facts.get("artifacts") or {}
    diff_files = facts.get("diff_files")

    if ptype in _PATH_PREDICATES:
        path = pred.get("path")
        if not path:
            return False, "no path bound"
        info = artifacts.get(path) or {}
        exists = bool(info.get("exists"))
        if ptype == ARTIFACT_EXISTS:
            return exists, ("exists" if exists else "missing on disk")
        size = int(info.get("size") or 0)
        if not exists:
            return False, "missing on disk"
        if size <= 0:
            return False, "exists but empty"
        return True, f"{size} bytes"

    if ptype == DIFF_NONEMPTY:
        if diff_files is None:
            return False, "no diff data"
        return (len(diff_files) > 0), (f"{len(diff_files)} files changed" if diff_files else "no changes")

    if ptype == DIFF_IN_SCOPE:
        if diff_files is None:
            return False, "no diff data"
        scope = pred.get("scope") or []
        if not scope:
            return True, "no scope constraint"
        out = [f for f in diff_files if not any(_under(f, s) for s in scope)]
        if out:
            return False, "out of scope: " + ", ".join(out[:5])
        return True, "all changes in scope"

    return False, f"unknown predicate {ptype}"


def _under(path: str, prefix: str) -> bool:
    p = path.replace("\\", "/").lstrip("./")
    pre = prefix.replace("\\", "/").rstrip("/")
    return p == pre or p.startswith(pre + "/") or p.startswith(pre)


def evaluate(acceptance: List[Dict[str, Any]], facts: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: decide the gate result from already-collected facts.

    ``ok`` is True iff every ``required`` predicate passes. Advisory predicates
    are recorded but never affect ``ok``.
    """
    results: List[Dict[str, Any]] = []
    failed_required: List[str] = []
    for pred in acceptance or []:
        passed, detail = _check(pred, facts)
        tier = pred.get("tier", _REQUIRED)
        rec = {
            "type": pred.get("type"),
            "tier": tier,
            "passed": bool(passed),
            "detail": detail,
        }
        if pred.get("path"):
            rec["path"] = pred["path"]
        results.append(rec)
        if tier == _REQUIRED and not passed:
            failed_required.append(pred.get("type"))
    return {"ok": len(failed_required) == 0, "results": results, "failed": failed_required}


# --- IO probing --------------------------------------------------------------

def _git_diff_files(root: str) -> Optional[List[str]]:
    if not root or not os.path.isdir(root):
        return None
    try:
        names = set()
        for args in (["diff", "--name-only"], ["diff", "--name-only", "--cached"]):
            out = subprocess.run(
                ["git", "-C", root, *args],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0:
                names.update(n for n in out.stdout.splitlines() if n.strip())
        return sorted(names)
    except Exception:
        return None


def collect_facts(acceptance: List[Dict[str, Any]], output_path: str, root: str) -> Dict[str, Any]:
    """Probe the filesystem/git for everything the given acceptance needs."""
    artifacts: Dict[str, Any] = {}
    need_diff = False
    for pred in acceptance or []:
        ptype = pred.get("type")
        if ptype in _PATH_PREDICATES:
            path = pred.get("path") or (output_path if looks_like_path(output_path) else "")
            if path:
                resolved = os.path.expanduser(path)
                if not os.path.isabs(resolved) and root:
                    resolved_join = os.path.join(root, resolved)
                else:
                    resolved_join = resolved
                exists = os.path.exists(resolved) or os.path.exists(resolved_join)
                target = resolved if os.path.exists(resolved) else resolved_join
                size = 0
                if exists:
                    try:
                        size = os.path.getsize(target) if os.path.isfile(target) else 1
                    except OSError:
                        size = 0
                artifacts[path] = {"exists": exists, "size": size}
        elif ptype in _DIFF_PREDICATES:
            need_diff = True
    diff_files = _git_diff_files(root) if need_diff else None
    return {"artifacts": artifacts, "diff_files": diff_files, "root": root}


def gate(task: Dict[str, Any], output_path: str, root: str) -> Dict[str, Any]:
    """Top-level: derive the contract, probe reality, and decide.

    Returns the same shape as `evaluate`, plus the resolved `acceptance` so the
    decision can be written verbatim into the event ledger.
    """
    acceptance = acceptance_for_done(task, output_path)
    facts = collect_facts(acceptance, output_path, root)
    result = evaluate(acceptance, facts)
    result["acceptance"] = acceptance
    return result
