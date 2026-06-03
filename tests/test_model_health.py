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


def test_model_registry_merges_opencode_cli_server_manual_and_latency(monkeypatch, tmp_path):
    """The registry should expose the live OpenCode CLI list, server metadata, manual models, and latency."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(
        json.dumps(
            {
                'runtime': 'opencode',
                'defaultModel': 'opencode/deepseek-v4-flash-free',
                'knownModels': [
                    {'id': 'opencode/deepseek-v4-flash-free', 'label': 'DeepSeek Free', 'provider': 'OpenCode'},
                ],
                'agents': [
                    {'id': 'taizi', 'label': '太子', 'role': '太子', 'emoji': '🤴', 'model': 'opencode/deepseek-v4-flash-free'},
                ],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (data_dir / 'custom_models.json').write_text(
        json.dumps(
            {
                'version': 1,
                'models': [
                    {
                        'id': 'openrouter/anthropic/claude-3.5-sonnet',
                        'providerId': 'openrouter',
                        'providerName': 'OpenRouter',
                        'modelId': 'anthropic/claude-3.5-sonnet',
                        'label': 'Claude via OpenRouter',
                        'apiType': 'openai',
                        'baseURL': 'https://openrouter.ai/api/v1',
                        'apiKey': 'sk-test-secret',
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (tmp_path / 'opencode.json').write_text('{}', encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/tmp/opencode')
    monkeypatch.setattr(srv, '_append_runtime_event', lambda *args, **kwargs: None)

    class Result:
        returncode = 0
        stdout = 'moonshotai-cn/kimi-k2.6\nopenai/gpt-5.5-pro\n'
        stderr = ''

    monkeypatch.setattr(srv.subprocess, 'run', lambda *args, **kwargs: Result())

    class FakeResponse:
        def read(self):
            return json.dumps(
                {
                    'providers': [
                        {
                            'id': 'github-copilot',
                            'name': 'GitHub Copilot',
                            'models': {
                                'gpt-5.4-fast': {
                                    'id': 'gpt-5.4-fast',
                                    'name': 'GPT-5.4 Fast',
                                    'status': 'active',
                                    'limit': {'context': 400000},
                                }
                            },
                        }
                    ]
                }
            ).encode('utf-8')

    monkeypatch.setattr(srv, 'urlopen', lambda *args, **kwargs: FakeResponse())
    srv._record_model_health('taizi', 'moonshotai-cn/kimi-k2.6', 'ok', latency_ms=680)

    registry = srv.get_model_registry(force=False)
    by_id = {m['id']: m for m in registry['models']}

    assert registry['ok'] is True
    assert 'moonshotai-cn/kimi-k2.6' in by_id
    assert 'openai/gpt-5.5-pro' in by_id
    assert 'github-copilot/gpt-5.4-fast' in by_id
    assert 'openrouter/anthropic/claude-3.5-sonnet' in by_id
    assert by_id['moonshotai-cn/kimi-k2.6']['provider'] == 'Moonshot AI (China)'
    assert by_id['moonshotai-cn/kimi-k2.6']['latencyMs'] == 680
    assert by_id['openrouter/anthropic/claude-3.5-sonnet']['apiKeyMasked'] == 'sk-t••••cret'
    assert registry['summary']['total'] == 4


def test_model_probe_records_latency_into_registry(monkeypatch, tmp_path):
    """Active probes should measure a model and make the registry show real latency."""
    import subprocess
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(
        json.dumps(
            {
                'runtime': 'opencode',
                'defaultModel': 'opencode/big-pickle',
                'knownModels': [{'id': 'opencode/big-pickle', 'label': 'Big Pickle', 'provider': 'OpenCode Zen'}],
                'agents': [{'id': 'taizi', 'label': '太子', 'role': '太子', 'emoji': '🤴', 'model': 'opencode/big-pickle'}],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/tmp/opencode')
    monkeypatch.setattr(srv, '_opencode_cli_model_entries', lambda force=False: (
        [{'id': 'opencode/big-pickle', 'label': 'Big Pickle', 'provider': 'OpenCode Zen', 'source': 'opencode-cli'}],
        {'id': 'opencode-cli', 'label': 'OpenCode CLI', 'ok': True, 'count': 1, 'latencyMs': 4, 'error': ''},
    ))
    monkeypatch.setattr(srv, '_opencode_provider_model_entries', lambda: (
        [],
        {'id': 'opencode-server', 'label': 'OpenCode Server', 'ok': True, 'count': 0, 'latencyMs': 2, 'error': ''},
    ))
    monkeypatch.setattr(
        srv,
        '_run_model_probe_capture',
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, '{"type":"done"}\n', ''),
    )

    result = srv._probe_model_once('opencode/big-pickle', timeout_sec=10)
    assert result['ok'] is True
    assert result['status'] == 'ok'
    assert isinstance(result['latencyMs'], int)

    srv._record_model_probe('opencode/big-pickle', result['status'], latency_ms=211, source='test-probe')
    registry = srv.get_model_registry(force=False)
    item = next(model for model in registry['models'] if model['id'] == 'opencode/big-pickle')
    health = srv.get_model_health()
    taizi = next(agent for agent in health['agents'] if agent['agentId'] == 'taizi')

    assert item['latencyMs'] == 211
    assert item['latencyLabel'] == '快'
    assert item['latencySource'] == 'test-probe'
    assert registry['summary']['measured'] == 1
    assert taizi['lastLatencyMs'] == 211
    assert taizi['source'] == 'model_probe'


def test_run_model_probes_now_starts_background_batch(monkeypatch, tmp_path):
    """Manual probe requests should return immediately with a running queue."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(
        json.dumps({'runtime': 'opencode', 'knownModels': [], 'agents': []}, ensure_ascii=False),
        encoding='utf-8',
    )

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(srv, '_opencode_cli_model_entries', lambda force=False: (
        [{'id': 'opencode/big-pickle', 'label': 'Big Pickle', 'provider': 'OpenCode Zen', 'source': 'opencode-cli'}],
        {'id': 'opencode-cli', 'label': 'OpenCode CLI', 'ok': True, 'count': 1, 'latencyMs': 4, 'error': ''},
    ))
    monkeypatch.setattr(srv, '_opencode_provider_model_entries', lambda: (
        [],
        {'id': 'opencode-server', 'label': 'OpenCode Server', 'ok': True, 'count': 0, 'latencyMs': 2, 'error': ''},
    ))

    calls = []

    class ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, **_):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            calls.append(self.args[0])

    monkeypatch.setattr(srv, '_REAL_THREAD', ImmediateThread)

    result = srv.run_model_probes_now({'modelIds': ['opencode/big-pickle'], 'timeoutSec': 10})

    assert result['ok'] is True
    assert result['started'] is True
    assert result['count'] == 1
    assert calls == [['opencode/big-pickle']]


def test_model_registry_hides_agent_config_only_legacy_models(monkeypatch, tmp_path):
    """Legacy OpenClaw/Copilot leftovers should not appear when OpenCode has a live catalog."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(
        json.dumps(
            {
                'runtime': 'opencode',
                'defaultModel': 'opencode/deepseek-v4-flash-free',
                'knownModels': [
                    {'id': 'anthropic/claude-haiku-3-5', 'label': 'Claude Haiku 3.5', 'provider': 'Anthropic'},
                    {'id': 'copilot/o3-mini', 'label': 'O3 Mini', 'provider': 'Copilot'},
                    {'id': 'moonshotai-cn/kimi-k2.6', 'label': 'Kimi K2.6', 'provider': 'Moonshot AI (China)'},
                ],
                'agents': [
                    {'id': 'taizi', 'label': '太子', 'role': '太子', 'emoji': '🤴', 'model': 'anthropic/claude-haiku-3-5'},
                ],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(srv, '_sync_opencode_agent_config', lambda force=False: False)
    monkeypatch.setattr(
        srv,
        '_opencode_cli_model_entries',
        lambda force=False: (
            [{'id': 'moonshotai-cn/kimi-k2.6', 'label': 'Kimi K2.6', 'provider': 'Moonshot AI (China)', 'source': 'opencode-cli'}],
            {'id': 'opencode-cli', 'label': 'OpenCode CLI', 'ok': True, 'count': 1, 'latencyMs': 1, 'error': ''},
        ),
    )
    monkeypatch.setattr(
        srv,
        '_opencode_provider_model_entries',
        lambda: ([], {'id': 'opencode-server', 'label': 'OpenCode Server', 'ok': True, 'count': 0, 'latencyMs': 1, 'error': ''}),
    )

    registry = srv.get_model_registry(force=True)
    ids = [m['id'] for m in registry['models']]

    assert ids == ['moonshotai-cn/kimi-k2.6']
    assert registry['summary']['providers'] == {'Moonshot AI (China)': 1}


def test_add_custom_model_writes_dashboard_and_opencode_provider(monkeypatch, tmp_path):
    """Manual API models should become selectable and be written into opencode.json."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(
        json.dumps(
            {
                'runtime': 'opencode',
                'knownModels': [
                    {'id': 'opencode/deepseek-v4-flash-free', 'label': 'DeepSeek Free', 'provider': 'OpenCode'},
                ],
                'agents': [],
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    (tmp_path / 'opencode.json').write_text('{}', encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(srv, '_append_runtime_event', lambda *args, **kwargs: None)
    monkeypatch.setattr(srv, '_opencode_cli_model_entries', lambda force=False: ([], {'id': 'opencode-cli', 'label': 'OpenCode CLI', 'ok': True, 'count': 0, 'latencyMs': 0, 'error': ''}))
    monkeypatch.setattr(srv, '_opencode_provider_model_entries', lambda: ([], {'id': 'opencode-server', 'label': 'OpenCode Server', 'ok': True, 'count': 0, 'latencyMs': 0, 'error': ''}))

    result = srv.add_custom_model_to_registry(
        {
            'providerId': 'openrouter',
            'providerName': 'OpenRouter',
            'modelId': 'anthropic/claude-3.5-sonnet',
            'label': 'Claude via OpenRouter',
            'apiType': 'openai',
            'baseURL': 'https://openrouter.ai/api/v1',
            'apiKey': 'sk-test-secret',
        }
    )
    cfg = json.loads((data_dir / 'agent_config.json').read_text(encoding='utf-8'))
    ocfg = json.loads((tmp_path / 'opencode.json').read_text(encoding='utf-8'))
    custom = json.loads((data_dir / 'custom_models.json').read_text(encoding='utf-8'))

    assert result['ok'] is True
    assert result['model']['id'] == 'openrouter/anthropic/claude-3.5-sonnet'
    assert result['model']['apiKeyMasked'] == 'sk-t••••cret'
    assert 'openrouter/anthropic/claude-3.5-sonnet' in [m['id'] for m in cfg['knownModels']]
    assert ocfg['provider']['openrouter']['options']['baseURL'] == 'https://openrouter.ai/api/v1'
    assert ocfg['provider']['openrouter']['options']['apiKey'] == 'sk-test-secret'
    assert ocfg['provider']['openrouter']['models']['anthropic/claude-3.5-sonnet']['name'] == 'Claude via OpenRouter'
    assert custom['models'][0]['apiKey'] == 'sk-test-secret'
