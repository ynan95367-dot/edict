import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.models.task import Task, TaskState


def test_to_dict_shape_and_legacy_fields():
    t = Task(
        title="标题",
        state=TaskState.Review,
        flow_log=[{"from": "Zhongshu", "to": "Menxia"}],
        progress_log=[{"agent": "menxia"}],
        todos=[],
        scheduler={"retryCount": 1},
    )
    d = t.to_dict()
    assert d["title"] == "标题"
    assert d["state"] == "Review"
    assert d["flow_log"] == [{"from": "Zhongshu", "to": "Menxia"}]
    # legacy / old-frontend compatibility
    assert d["id"] == d["task_id"]
    assert d["_scheduler"] == {"retryCount": 1}
    assert "updatedAt" in d


def test_to_dict_handles_none_collections():
    t = Task(title="x", state=TaskState.Taizi)
    d = t.to_dict()
    assert d["tags"] == []
    assert d["todos"] == []
    assert d["meta"] == {}
    assert d["flow_log"] == []
