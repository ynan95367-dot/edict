"""Shared control-plane contract for the JSON dashboard and FastAPI backend."""
from __future__ import annotations

from typing import Any


CONTRACT_VERSION = "control-plane-v1-20260604"

TASK_STATES = (
    "Pending",
    "Taizi",
    "Zhongshu",
    "Menxia",
    "Assigned",
    "Next",
    "Doing",
    "Review",
    "PendingConfirm",
    "Blocked",
    "Done",
    "Cancelled",
)

TERMINAL_STATES = frozenset({"Done", "Cancelled"})

STATE_TRANSITIONS = {
    "Pending": frozenset({"Taizi", "Cancelled"}),
    "Taizi": frozenset({"Zhongshu", "Cancelled"}),
    "Zhongshu": frozenset({"Menxia", "Cancelled", "Blocked"}),
    "Menxia": frozenset({"Assigned", "Zhongshu", "Cancelled"}),
    "Assigned": frozenset({"Doing", "Next", "Cancelled", "Blocked"}),
    "Next": frozenset({"Doing", "Cancelled", "Blocked"}),
    "Doing": frozenset({"Review", "Done", "Blocked", "Cancelled"}),
    "Review": frozenset({"Done", "Menxia", "Doing", "Cancelled", "PendingConfirm"}),
    "PendingConfirm": frozenset({"Done", "Review", "Cancelled"}),
    "Blocked": frozenset({
        "Taizi",
        "Zhongshu",
        "Menxia",
        "Assigned",
        "Next",
        "Doing",
        "Review",
        "Cancelled",
    }),
    "Done": frozenset(),
    "Cancelled": frozenset(),
}

STATE_AGENT_MAP = {
    "Taizi": "taizi",
    "Zhongshu": "zhongshu",
    "Menxia": "menxia",
    "Assigned": "shangshu",
    "Doing": None,
    "Next": None,
    "Review": "shangshu",
    "Pending": "zhongshu",
    "PendingConfirm": "shangshu",
}

ORG_AGENT_MAP = {
    "礼部": "libu",
    "户部": "hubu",
    "兵部": "bingbu",
    "刑部": "xingbu",
    "工部": "gongbu",
    "吏部": "libu_hr",
    "中书省": "zhongshu",
    "门下省": "menxia",
    "尚书省": "shangshu",
}

STATE_ORG_MAP = {
    "Pending": "中书省",
    "Taizi": "太子",
    "Zhongshu": "中书省",
    "Menxia": "门下省",
    "Assigned": "尚书省",
    "Next": "尚书省",
    "Doing": "执行中",
    "Review": "尚书省",
    "PendingConfirm": "尚书省",
    "Blocked": "阻塞",
    "Done": "完成",
    "Cancelled": "已取消",
}

STATE_LABELS = {
    "Pending": "待处理",
    "Taizi": "太子",
    "Zhongshu": "中书省",
    "Menxia": "门下省",
    "Assigned": "尚书省",
    "Next": "待执行",
    "Doing": "执行中",
    "Review": "审查",
    "PendingConfirm": "待确认",
    "Blocked": "阻塞",
    "Done": "完成",
    "Cancelled": "已取消",
}

STATE_FLOW = {
    "Pending": ("Taizi", "皇上", "太子", "待处理旨意转交太子分拣"),
    "Taizi": ("Zhongshu", "太子", "中书省", "太子分拣完毕，转中书省起草"),
    "Zhongshu": ("Menxia", "中书省", "门下省", "中书省方案提交门下省审议"),
    "Menxia": ("Assigned", "门下省", "尚书省", "门下省准奏，转尚书省派发"),
    "Assigned": ("Doing", "尚书省", "六部", "尚书省开始派发执行"),
    "Next": ("Doing", "尚书省", "六部", "待执行任务开始执行"),
    "Doing": ("Review", "六部", "尚书省", "各部完成，进入汇总"),
    "Review": ("Done", "尚书省", "太子", "全流程完成，回奏太子转报皇上"),
}

AGENT_DEPTS = (
    {"id": "taizi", "label": "太子", "emoji": "🤴", "role": "太子", "rank": "储君"},
    {"id": "zhongshu", "label": "中书省", "emoji": "📜", "role": "中书令", "rank": "正一品"},
    {"id": "menxia", "label": "门下省", "emoji": "🔍", "role": "侍中", "rank": "正一品"},
    {"id": "shangshu", "label": "尚书省", "emoji": "📮", "role": "尚书令", "rank": "正一品"},
    {"id": "hubu", "label": "户部", "emoji": "💰", "role": "户部尚书", "rank": "正二品"},
    {"id": "libu", "label": "礼部", "emoji": "📝", "role": "礼部尚书", "rank": "正二品"},
    {"id": "bingbu", "label": "兵部", "emoji": "⚔️", "role": "兵部尚书", "rank": "正二品"},
    {"id": "xingbu", "label": "刑部", "emoji": "⚖️", "role": "刑部尚书", "rank": "正二品"},
    {"id": "gongbu", "label": "工部", "emoji": "🔧", "role": "工部尚书", "rank": "正二品"},
    {"id": "libu_hr", "label": "吏部", "emoji": "👔", "role": "吏部尚书", "rank": "正二品"},
    {"id": "zaochao", "label": "钦天监", "emoji": "📰", "role": "朝报官", "rank": "正三品"},
)

AGENT_LABELS = {"main": "太子", **{item["id"]: item["label"] for item in AGENT_DEPTS}}


def state_targets(state: str) -> set[str]:
    return set(STATE_TRANSITIONS.get(str(state or ""), frozenset()))


def is_terminal_state(state: str) -> bool:
    return str(state or "") in TERMINAL_STATES


def org_for_state(state: str, assignee_org: str | None = None) -> str:
    state = str(state or "")
    if state in {"Doing", "Next"}:
        return assignee_org or "六部"
    return STATE_ORG_MAP.get(state, assignee_org or "太子")


def expected_agent_for_state(state: str, org: str = "", target_dept: str = "") -> str:
    state = str(state or "")
    agent_id = STATE_AGENT_MAP.get(state)
    if agent_id is None and state in {"Doing", "Next"}:
        agent_id = ORG_AGENT_MAP.get(target_dept or org or "")
    return agent_id or ""


def agent_label(agent_id: str) -> str:
    return AGENT_LABELS.get(str(agent_id or ""), str(agent_id or ""))


def known_agent_ids() -> set[str]:
    ids = {agent for agent in STATE_AGENT_MAP.values() if agent}
    ids.update(ORG_AGENT_MAP.values())
    ids.update(item["id"] for item in AGENT_DEPTS)
    return ids


def as_serializable_contract() -> dict[str, Any]:
    return {
        "ok": True,
        "version": CONTRACT_VERSION,
        "taskStates": list(TASK_STATES),
        "terminalStates": sorted(TERMINAL_STATES),
        "stateTransitions": {state: sorted(targets) for state, targets in STATE_TRANSITIONS.items()},
        "stateAgentMap": dict(STATE_AGENT_MAP),
        "orgAgentMap": dict(ORG_AGENT_MAP),
        "stateOrgMap": dict(STATE_ORG_MAP),
        "stateLabels": dict(STATE_LABELS),
        "stateFlow": {state: list(flow) for state, flow in STATE_FLOW.items()},
        "agentDepts": [dict(item) for item in AGENT_DEPTS],
        "agentLabels": dict(AGENT_LABELS),
    }
