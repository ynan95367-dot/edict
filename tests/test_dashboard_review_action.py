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


def test_pending_confirm_approve_applies_target_state(monkeypatch):
    """PendingConfirm approve should apply the recorded target state."""
    tasks = [{
        "id": "JJC-CONFIRM-001",
        "title": "confirm gate",
        "state": "PendingConfirm",
        "org": "尚书省",
        "now": "待确认: Review→Done",
        "flow_log": [],
        "pending_confirm": {
            "target_state": "Done",
            "requested_by": "shangshu",
            "confirm_by": "menxia",
        },
        "todos": [
            {"id": "1", "title": "已完成", "status": "completed"},
        ],
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

    result = dashboard_server.handle_review_action("JJC-CONFIRM-001", "approve", "报告可收口")

    assert result["ok"] is True
    task = saved["tasks"][0]
    assert task["state"] == "Done"
    assert task["org"] == "完成"
    assert task["now"] == "御批通过，任务完成"
    assert "pending_confirm" not in task
    assert dispatches == []


def test_pending_confirm_reject_returns_to_review(monkeypatch):
    """PendingConfirm reject should return the task to Review for rework."""
    tasks = [{
        "id": "JJC-CONFIRM-002",
        "title": "confirm reject",
        "state": "PendingConfirm",
        "org": "尚书省",
        "now": "待确认: Review→Done",
        "flow_log": [],
        "pending_confirm": {
            "target_state": "Done",
            "requested_by": "shangshu",
            "confirm_by": "menxia",
        },
        "todos": [
            {"id": "1", "title": "已完成", "status": "completed"},
        ],
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

    result = dashboard_server.handle_review_action("JJC-CONFIRM-002", "reject", "报告还要补充")

    assert result["ok"] is True
    task = saved["tasks"][0]
    assert task["state"] == "Review"
    assert task["org"] == "尚书省"
    assert task["now"] == "御批驳回，退回尚书省复审"
    assert "pending_confirm" not in task
    assert dispatches


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
            "mode": "execute",
            "riskLevel": "high",
            "runKind": "system",
            "targetDept": "兵部",
            "requiredCapabilities": ["governance.plan", "runtime.opencode", "shell.command", "artifact.outputs"],
            "governance": [
                {"stage": "intake", "dept": "太子", "label": "意图分拣"},
                {"stage": "plan", "dept": "中书省", "label": "成案"},
                {"stage": "approval", "dept": "皇上", "label": "人工确认"},
            ],
            "toolPolicy": {
                "permissions": ["agent.run", "shell.execute"],
                "permissionLabels": ["调用 Agent", "执行命令"],
                "requiresApproval": True,
                "approvalReason": "shell.execute 需要确认",
            },
            "policyGate": {
                "decision": "hold_for_policy",
                "status": "waiting_policy_approval",
                "reason": "shell.execute 需要确认",
                "requiresApproval": True,
            },
            "executionIsolation": {
                "mode": "patch_first_shared_worktree",
                "targetMode": "dedicated_worktree",
                "label": "Patch-first 隔离",
                "required": True,
                "requiresPatchReview": True,
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
    assert task["runSpec"]["runGraph"]["status"] == "ready"
    assert task["runSpec"]["runGraph"]["summary"]["blockedByPolicy"] is False
    assert not any(node["id"] == "control.wait" for node in task["runSpec"]["runGraph"]["nodes"])
    assert sched["policyGateDecision"] == "auto_dispatch"
    assert sched["policyGateStatus"] == "approved"
    assert dispatches


def test_policy_gate_can_be_approved_after_assigned(monkeypatch):
    """A policy-held task should be approvable even after the state has advanced."""
    tasks = [{
        "id": "JJC-REVIEW-POLICY-ASSIGNED",
        "title": "policy gate assigned",
        "state": "Assigned",
        "org": "尚书省",
        "now": "权限闸门拦截交办",
        "flow_log": [],
        "runSpec": {
            "mode": "execute",
            "riskLevel": "high",
            "runKind": "system",
            "targetDept": "兵部",
            "requiredCapabilities": ["runtime.opencode", "shell.command"],
            "governance": [],
            "toolPolicy": {
                "permissions": ["shell.execute"],
                "permissionLabels": ["执行命令"],
                "requiresApproval": True,
                "approvalReason": "shell.execute 需要确认",
            },
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

    result = dashboard_server.handle_review_action("JJC-REVIEW-POLICY-ASSIGNED", "approve", "同意执行")

    assert result["ok"] is True
    task = saved["tasks"][0]
    assert task["state"] == "Assigned"
    assert task["now"] == "权限闸门已准奏，继续交办执行"
    assert task["runSpec"]["policyGate"]["decision"] == "auto_dispatch"
    assert task["runSpec"]["policyGate"]["approvedBy"] == "huangshang"
    assert dispatches
