import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models.task import (
    STATE_TRANSITIONS,
    TERMINAL_STATES,
    TaskState,
    Task,
    _contract_transitions,
)


def test_transitions_match_control_plane_contract():
    assert STATE_TRANSITIONS == _contract_transitions()


def test_terminal_states():
    assert TERMINAL_STATES == {TaskState.Done, TaskState.Cancelled}


def test_no_transitions_out_of_terminal_states():
    for t in TERMINAL_STATES:
        assert STATE_TRANSITIONS.get(t, set()) == set()


def test_taizi_flows_to_zhongshu_not_done():
    assert TaskState.Zhongshu in STATE_TRANSITIONS[TaskState.Taizi]
    assert TaskState.Done not in STATE_TRANSITIONS[TaskState.Taizi]


def test_org_for_state():
    assert Task.org_for_state(TaskState.Menxia) == "门下省"
    assert Task.org_for_state(TaskState.Doing, "兵部") == "兵部"
