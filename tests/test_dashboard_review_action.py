"""Regression tests for dashboard review completion gates."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys


DASHBOARD_DIR = pathlib.Path(__file__).resolve().parent.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD_DIR))

_SPEC = importlib.util.spec_from_file_location("dashboard_server", DASHBOARD_DIR / "server.py")
dashboard_server = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(dashboard_server)


def test_review_approve_rejects_incomplete_todos(monkeypatch):
    """Review approve should not close a task when todos are still incomplete."""
    tasks = [{
        "id": "JJC-REVIEW-001",
        "title": "review gate",
        "state": "Review",
        "org": "尚书省",
        "now": "汇总中",
        "flow_log": [],
        "todos": [
            {"id": "1", "title": "已完成", "status": "completed"},
            {"id": "2", "title": "未完成", "status": "in-progress"},
        ],
    }]

    saved = {}

    monkeypatch.setattr(dashboard_server, "load_tasks", lambda: json.loads(json.dumps(tasks, ensure_ascii=False)))
    monkeypatch.setattr(
        dashboard_server,
        "save_tasks",
        lambda payload: saved.setdefault("tasks", json.loads(json.dumps(payload, ensure_ascii=False))),
    )

    result = dashboard_server.handle_review_action("JJC-REVIEW-001", "approve", "试图提前完结")

    assert result["ok"] is False
    assert "2/2" not in result["error"]
    assert "不能直接准奏完结" in result["error"]
    assert "tasks" not in saved


def test_review_approve_allows_complete_todos(monkeypatch):
    """Review approve may finish a task once all todos are completed."""
    tasks = [{
        "id": "JJC-REVIEW-002",
        "title": "review gate ok",
        "state": "Review",
        "org": "尚书省",
        "now": "汇总中",
        "flow_log": [],
        "todos": [
            {"id": "1", "title": "已完成", "status": "completed"},
            {"id": "2", "title": "已完成2", "status": "completed"},
        ],
    }]

    saved = {}

    monkeypatch.setattr(dashboard_server, "load_tasks", lambda: json.loads(json.dumps(tasks, ensure_ascii=False)))
    monkeypatch.setattr(
        dashboard_server,
        "save_tasks",
        lambda payload: saved.setdefault("tasks", json.loads(json.dumps(payload, ensure_ascii=False))),
    )

    result = dashboard_server.handle_review_action("JJC-REVIEW-002", "approve", "全部完成")

    assert result["ok"] is True
    assert saved["tasks"][0]["state"] == "Done"


def test_menxia_approve_releases_policy_gate(monkeypatch):
    """Approving a policy-held RunSpec should release the dispatch gate."""
    tasks = [{
        "id": "JJC-REVIEW-POLICY",
        "title": "policy gate",
        "state": "Menxia",
        "org": "门下省",
        "now": "Policy Gate：等待权限审批",
        "flow_log": [],
        "runSpec": {
            "policyGate": {
                "decision": "hold_for_policy",
                "status": "waiting_policy_approval",
                "reason": "shell.execute 需要确认",
                "requiresApproval": True,
            },
        },
    }]
    saved = {}
    dispatches = []

    monkeypatch.setattr(dashboard_server, "load_tasks", lambda: json.loads(json.dumps(tasks, ensure_ascii=False)))
    monkeypatch.setattr(
        dashboard_server,
        "save_tasks",
        lambda payload: saved.setdefault("tasks", json.loads(json.dumps(payload, ensure_ascii=False))),
    )
    monkeypatch.setattr(dashboard_server, "dispatch_for_state", lambda *args, **kwargs: dispatches.append((args, kwargs)))

    result = dashboard_server.handle_review_action("JJC-REVIEW-POLICY", "approve", "同意执行")

    assert result["ok"] is True
    task = saved["tasks"][0]
    gate = task["runSpec"]["policyGate"]
    sched = task["_scheduler"]
    assert task["state"] == "Assigned"
    assert gate["decision"] == "auto_dispatch"
    assert gate["status"] == "approved"
    assert gate["requiresApproval"] is False
    assert gate["approvedBy"] == "menxia"
    assert sched["policyGateDecision"] == "auto_dispatch"
    assert sched["policyGateStatus"] == "approved"
    assert dispatches
