from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_edict_entrypoint_uses_resolved_python_runtime():
    script = (ROOT / "edict.sh").read_text()

    assert "resolve_python()" in script
    assert "PYTHON_BIN=$(resolve_python)" in script
    assert 'export EDICT_PYTHON="$PYTHON_BIN"' in script
    assert 'nohup env EDICT_PYTHON="$PYTHON_BIN" "$PYTHON_BIN" "$REPO_DIR/scripts/refresh_watcher.py"' in script
    assert 'nohup env EDICT_PYTHON="$PYTHON_BIN" "$PYTHON_BIN" "$REPO_DIR/dashboard/server.py"' in script
    assert 'opencode_health=$("$PYTHON_BIN" -' in script
    assert 'health=$("$PYTHON_BIN" -c' in script
    assert 'nohup python3 "$REPO_DIR/scripts/refresh_watcher.py"' not in script
    assert 'nohup python3 "$REPO_DIR/dashboard/server.py"' not in script
