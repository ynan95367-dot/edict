"""Tests for model health observation and automatic failover."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def _write_agent_config(data_dir, current_model, fallback_model):
    (data_dir / 'agent_config.json').write_text(
        json.dumps(
            {
                'runtime': 'opencode',
                'defaultModel': current_model,
                'knownModels': [
                    {'id': current_model, 'label': 'Claude Opus', 'provider': 'GitHub Copilot'},
                    {'id': fallback_model, 'label': 'GPT Codex', 'provider': 'OpenAI Codex'},
                    {'id': 'opencode/deepseek-v4-flash-free', 'label': 'DeepSeek Free', 'provider': 'OpenCode'},
                ],
                'agents': [
                    {'id': 'taizi', 'label': '太子', 'role': '太子', 'emoji': '🤴', 'model': current_model},
                ],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )


def test_model_health_reports_timeout_and_same_tier_fallback(monkeypatch, tmp_path):
    """The health API should expose observed timeouts and same-tier fallback candidates."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    current_model = 'github-copilot/claude-opus-4.6'
    fallback_model = 'openai-codex/gpt-5.3-codex'
    _write_agent_config(data_dir, current_model, fallback_model)

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_outbox_list', lambda **kwargs: [])
    monkeypatch.setattr(srv, '_append_runtime_event', lambda *args, **kwargs: None)

    srv._record_model_health(
        'taizi',
        current_model,
        'timeout',
        error='request timed out while calling provider',
        task_id='JJC-MODEL-001',
        trace_id='trc_model',
        dispatch_id='dispatch_model',
    )

    data = srv.get_model_health()
    taizi = next(agent for agent in data['agents'] if agent['agentId'] == 'taizi')

    assert data['summary']['timeout'] == 1
    assert taizi['status'] == 'timeout'
    assert taizi['fallbackModel'] == fallback_model
    assert taizi['tier'] == 'frontier'
    assert 'timed out' in taizi['lastError']


def test_model_failover_updates_configs_and_logs(monkeypatch, tmp_path):
    """Automatic model failover should update dashboard config, opencode config, and change log."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    dashboard_dir = tmp_path / 'dashboard'
    dashboard_dir.mkdir()
    current_model = 'github-copilot/claude-opus-4.6'
    fallback_model = 'openai-codex/gpt-5.3-codex'
    _write_agent_config(data_dir, current_model, fallback_model)
    (tmp_path / 'opencode.json').write_text(
        json.dumps({'model': current_model, 'agent': {'taizi': {'model': current_model}}}, ensure_ascii=False),
        encoding='utf-8',
    )

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'BASE', dashboard_dir)
    monkeypatch.setattr(srv, '_append_runtime_event', lambda *args, **kwargs: None)

    replacement = srv._maybe_apply_model_failover(
        'taizi',
        current_model,
        'timeout',
        'provider request timeout after 310s',
        task_id='JJC-MODEL-002',
        trace_id='trc_model_2',
        dispatch_id='dispatch_model_2',
    )

    cfg = json.loads((data_dir / 'agent_config.json').read_text(encoding='utf-8'))
    ocfg = json.loads((tmp_path / 'opencode.json').read_text(encoding='utf-8'))
    change_log = json.loads((data_dir / 'model_change_log.json').read_text(encoding='utf-8'))
    health = json.loads((data_dir / 'model_health.json').read_text(encoding='utf-8'))

    assert replacement == fallback_model
    assert cfg['agents'][0]['model'] == fallback_model
    assert ocfg['agent']['taizi']['model'] == fallback_model
    assert change_log[-1]['autoFailover'] is True
    assert change_log[-1]['oldModel'] == current_model
    assert change_log[-1]['newModel'] == fallback_model
    assert health['failovers'][-1]['oldModel'] == current_model
    assert health['failovers'][-1]['newModel'] == fallback_model
