"""Shared pytest guards for dashboard/runtime tests."""
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


@pytest.fixture(autouse=True)
def isolate_runtime_event_ledger(tmp_path, monkeypatch):
    """Keep test events out of the user's real dashboard ledger."""
    try:
        import event_log
    except Exception:
        return
    monkeypatch.setattr(event_log, 'EVENTS_DIR', tmp_path / 'events', raising=False)
