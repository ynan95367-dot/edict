import importlib.util
import json
from pathlib import Path


def _load_sync_opencode_agents():
    root = Path(__file__).resolve().parents[1]
    script_path = root / "scripts" / "sync_opencode_agents.py"
    spec = importlib.util.spec_from_file_location("sync_opencode_agents", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_collect_opencode_models_keeps_configured_models_selectable(monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(
        sync_opencode_agents,
        "discover_opencode_models",
        lambda: [
            {"id": "opencode/mimo-v2.5-free", "label": "Mimo V2.5 Free", "provider": "OpenCode"},
            {"id": "github-copilot/gpt-5.3-codex", "label": "GPT 5.3 Codex", "provider": "GitHub Copilot"},
        ],
    )

    cfg = {
        "model": "opencode/deepseek-v4-flash-free",
        "agent": {"taizi": {"model": "github-copilot/gpt-5.3-codex"}},
    }
    existing = [{"id": "anthropic/claude-sonnet-4-6", "label": "Claude Sonnet 4.6", "provider": "Anthropic"}]

    models = sync_opencode_agents.collect_opencode_models(
        cfg,
        existing,
        "opencode/deepseek-v4-flash-free",
    )
    ids = [m["id"] for m in models]

    assert ids[0] == "opencode/deepseek-v4-flash-free"
    assert "github-copilot/gpt-5.3-codex" in ids
    assert "opencode/mimo-v2.5-free" in ids
    assert "anthropic/claude-sonnet-4-6" not in ids
    assert len(ids) == len(set(ids))
    assert models[0]["provider"] == "OpenCode"
    assert next(m for m in models if m["id"] == "github-copilot/gpt-5.3-codex")["provider"] == "GitHub Copilot"


def test_sync_dashboard_config_injects_opencode_known_models(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(sync_opencode_agents, "BASE", tmp_path)
    monkeypatch.setattr(sync_opencode_agents, "DATA", tmp_path / "data")
    monkeypatch.setattr(sync_opencode_agents, "PROMPTS_DIR", tmp_path / ".opencode" / "prompts")
    monkeypatch.setattr(sync_opencode_agents, "discover_opencode_models", lambda: [])

    cfg = {
        "model": "opencode/deepseek-v4-flash-free",
        "agent": {"taizi": {"model": "opencode/big-pickle"}},
    }

    sync_opencode_agents.sync_dashboard_config(cfg)

    out = json.loads((tmp_path / "data" / "agent_config.json").read_text(encoding="utf-8"))
    ids = [m["id"] for m in out["knownModels"]]
    taizi = next(agent for agent in out["agents"] if agent["id"] == "taizi")

    assert out["runtime"] == "opencode"
    assert ids[:2] == ["opencode/deepseek-v4-flash-free", "opencode/big-pickle"]
    assert taizi["model"] == "opencode/big-pickle"


def test_sync_opencode_config_recovers_logged_model_choice(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(sync_opencode_agents, "BASE", tmp_path)
    monkeypatch.setattr(sync_opencode_agents, "DATA", tmp_path / "data")
    monkeypatch.setattr(sync_opencode_agents, "OPENCODE_CFG", tmp_path / "opencode.json")

    (tmp_path / "data").mkdir()
    (tmp_path / "opencode.json").write_text(
        json.dumps({"model": "opencode/deepseek-v4-flash-free", "agent": {"taizi": {}}}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "agent_config.json").write_text(
        json.dumps({"agents": [{"id": "taizi", "model": "opencode/deepseek-v4-flash-free"}]}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "model_change_log.json").write_text(
        json.dumps([
            {
                "runtime": "opencode",
                "agentId": "taizi",
                "oldModel": "opencode/deepseek-v4-flash-free",
                "newModel": "github-copilot/gemini-3.5-flash",
            }
        ]),
        encoding="utf-8",
    )

    cfg = sync_opencode_agents.sync_opencode_config()

    assert cfg["agent"]["taizi"]["model"] == "github-copilot/gemini-3.5-flash"


def test_sync_dashboard_config_recovers_logged_model_choice(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(sync_opencode_agents, "BASE", tmp_path)
    monkeypatch.setattr(sync_opencode_agents, "DATA", tmp_path / "data")
    monkeypatch.setattr(sync_opencode_agents, "PROMPTS_DIR", tmp_path / ".opencode" / "prompts")
    monkeypatch.setattr(sync_opencode_agents, "discover_opencode_models", lambda: [])

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "agent_config.json").write_text(
        json.dumps({"knownModels": [], "agents": [{"id": "zhongshu", "model": "opencode/deepseek-v4-flash-free"}]}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "model_change_log.json").write_text(
        json.dumps([
            {
                "runtime": "opencode",
                "agentId": "zhongshu",
                "oldModel": "opencode/deepseek-v4-flash-free",
                "newModel": "github-copilot/gpt-4o",
            }
        ]),
        encoding="utf-8",
    )

    sync_opencode_agents.sync_dashboard_config({"model": "opencode/deepseek-v4-flash-free", "agent": {"zhongshu": {}}})

    out = json.loads((tmp_path / "data" / "agent_config.json").read_text(encoding="utf-8"))
    zhongshu = next(agent for agent in out["agents"] if agent["id"] == "zhongshu")
    ids = [m["id"] for m in out["knownModels"]]

    assert zhongshu["model"] == "github-copilot/gpt-4o"
    assert "github-copilot/gpt-4o" in ids


def test_discover_opencode_models_uses_configured_bin(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    fake_bin = tmp_path / "opencode"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv("OPENCODE_BIN", str(fake_bin))
    monkeypatch.setattr(sync_opencode_agents, "MODEL_CACHE", tmp_path / "model_cache.json")

    calls = []

    class Result:
        returncode = 0
        stdout = "opencode/deepseek-v4-flash-free\ngithub-copilot/gpt-5.5-fast\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(sync_opencode_agents.subprocess, "run", fake_run)

    models = sync_opencode_agents.discover_opencode_models()
    ids = [m["id"] for m in models]

    assert calls[0][0] == str(fake_bin)
    assert ids == ["opencode/deepseek-v4-flash-free", "github-copilot/gpt-5.5-fast"]


def test_discover_opencode_models_force_refresh_skips_cache(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    fake_bin = tmp_path / "opencode"
    fake_bin.write_text("#!/bin/sh\n", encoding="utf-8")
    cache = tmp_path / "model_cache.json"
    cache.write_text(
        json.dumps({"generatedAt": 9999999999, "models": [{"id": "opencode/big-pickle"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_BIN", str(fake_bin))
    monkeypatch.setenv("OPENCODE_MODEL_REFRESH", "1")
    monkeypatch.setattr(sync_opencode_agents, "MODEL_CACHE", cache)

    calls = []

    class Result:
        returncode = 0
        stdout = "moonshotai-cn/kimi-k2.6\nopenai/gpt-5.5-pro\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(sync_opencode_agents.subprocess, "run", fake_run)

    models = sync_opencode_agents.discover_opencode_models()
    ids = [m["id"] for m in models]

    assert calls
    assert ids == ["moonshotai-cn/kimi-k2.6", "openai/gpt-5.5-pro"]
    assert models[0]["provider"] == "Moonshot AI (China)"


def test_sync_opencode_config_filters_legacy_models_when_live_catalog_exists(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    monkeypatch.delenv("OPENCODE_MODEL", raising=False)
    monkeypatch.delenv("OPENCODE_DEFAULT_MODEL", raising=False)
    monkeypatch.setattr(sync_opencode_agents, "BASE", tmp_path)
    monkeypatch.setattr(sync_opencode_agents, "DATA", tmp_path / "data")
    monkeypatch.setattr(sync_opencode_agents, "OPENCODE_CFG", tmp_path / "opencode.json")
    monkeypatch.setattr(
        sync_opencode_agents,
        "discover_opencode_models",
        lambda: [
            {"id": "opencode/big-pickle", "label": "Big Pickle", "provider": "OpenCode"},
            {"id": "moonshotai-cn/kimi-k2.6", "label": "Kimi K2.6", "provider": "Moonshot AI (China)"},
        ],
    )

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "agent_config.json").write_text(
        json.dumps({"agents": [{"id": "taizi", "model": "copilot/o3-mini"}]}),
        encoding="utf-8",
    )
    (tmp_path / "opencode.json").write_text(
        json.dumps(
            {
                "model": "copilot/o3-mini",
                "agent": {
                    "taizi": {"model": "copilot/o3-mini"},
                    "zhongshu": {"model": "moonshotai-cn/kimi-k2.6"},
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = sync_opencode_agents.sync_opencode_config()

    assert cfg["model"] == "opencode/deepseek-v4-flash-free"
    assert cfg["agent"]["taizi"]["model"] == "opencode/deepseek-v4-flash-free"
    assert cfg["agent"]["zhongshu"]["model"] == "moonshotai-cn/kimi-k2.6"


def test_cleanup_unmanaged_opencode_artifacts_ignores_busy_runtime_dir(tmp_path, monkeypatch):
    sync_opencode_agents = _load_sync_opencode_agents()
    opencode_dir = tmp_path / ".opencode"
    node_modules = opencode_dir / "node_modules"
    node_modules.mkdir(parents=True)
    monkeypatch.setattr(sync_opencode_agents, "OPENCODE_DIR", opencode_dir)

    def fail_rmtree(path):
        raise OSError(66, "Directory not empty")

    monkeypatch.setattr(sync_opencode_agents.shutil, "rmtree", fail_rmtree)

    sync_opencode_agents.cleanup_unmanaged_opencode_artifacts()
