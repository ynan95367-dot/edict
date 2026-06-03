"""Tests for dashboard auto-dispatch error handling."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def test_dispatch_records_missing_openclaw_cli(monkeypatch, tmp_path):
    """Missing OpenClaw CLI should become an actionable dispatch status."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260415-004'
    task = {
        'id': task_id,
        'title': '小任务',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-04-15T15:34:16Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')
    (data_dir / 'agent_config.json').write_text('{}', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_openclaw_bin', lambda: None)
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'openclaw-missing'
    assert 'OpenClaw CLI 未找到' in sched['lastDispatchError']
    assert '[WinError 2]' not in sched['lastDispatchError']
    assert any('OpenClaw CLI 未找到' in item['remark'] for item in updated['flow_log'])
    outbox = json.loads((data_dir / 'runtime_outbox.json').read_text(encoding='utf-8'))
    assert outbox[0]['kind'] == 'dispatch'
    assert outbox[0]['status'] == 'failed'


def test_dispatch_records_missing_opencode_cli(monkeypatch, tmp_path):
    """OpenCode mode should report a missing opencode CLI distinctly."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260526-001'
    task = {
        'id': task_id,
        'title': '切换 OpenCode',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-26T15:34:16Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: None)
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'opencode-missing'
    assert 'OpenCode CLI 未找到' in sched['lastDispatchError']
    assert any('OpenCode CLI 未找到' in item['remark'] for item in updated['flow_log'])


def test_dispatch_records_pending_outbox_before_worker(monkeypatch, tmp_path):
    """Dispatch requests should be durable before any background execution."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260529-010'
    task = {
        'id': task_id,
        'title': '持久队列测试',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-29T10:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)
    monkeypatch.setattr(srv, '_kick_dispatch_worker', lambda: None)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    active_id = updated['_scheduler']['activeDispatchId']
    outbox = json.loads((data_dir / 'runtime_outbox.json').read_text(encoding='utf-8'))
    assert outbox[0]['id'] == active_id
    assert outbox[0]['status'] == 'pending'
    assert outbox[0]['taskId'] == task_id
    assert outbox[0]['agentId'] == 'taizi'


def test_policy_gate_blocks_dispatch_before_outbox(monkeypatch, tmp_path):
    """A held RunSpec must not be enqueued even if a retry/scan asks to dispatch."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-POLICY-HOLD'
    task = {
        'id': task_id,
        'title': '需要 shell 审批',
        'state': 'Menxia',
        'org': '门下省',
        'updatedAt': '2026-06-03T10:00:00Z',
        'flow_log': [],
        'runSpec': {
            'policyGate': {
                'decision': 'hold_for_policy',
                'status': 'waiting_policy_approval',
                'reason': 'shell.execute 需要人工确认',
                'requiresApproval': True,
            },
            'toolPolicy': {
                'permissions': ['shell.execute'],
                'requiresApproval': True,
            },
        },
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_kick_dispatch_worker', lambda: (_ for _ in ()).throw(AssertionError('worker should not start')))

    srv.dispatch_for_state(task_id, task, 'Menxia', trigger='taizi-retry')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'policy-held'
    assert sched['lastDispatchTrigger'] == 'taizi-retry'
    assert sched['policyGateDecision'] == 'hold_for_policy'
    assert 'shell.execute' in sched['lastDispatchError']
    assert not (data_dir / 'runtime_outbox.json').exists()
    assert any('权限闸门拦截派发' in item['remark'] for item in updated['flow_log'])


def test_policy_gate_blocks_legacy_outbox_worker(monkeypatch, tmp_path):
    """Worker execution must re-check policy so old queued items cannot bypass approval."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-POLICY-WORKER'
    dispatch_id = 'dispatch_policy_worker'
    outbox_path = data_dir / 'runtime_outbox.json'
    task = {
        'id': task_id,
        'title': '旧队列项需要拦截',
        'state': 'Menxia',
        'org': '门下省',
        'traceId': 'trc_policy_worker',
        'updatedAt': '2026-06-03T10:00:00Z',
        'flow_log': [],
        '_scheduler': {
            'lastDispatchAgent': 'menxia',
            'lastDispatchState': 'Menxia',
            'lastDispatchStatus': 'running',
            'activeDispatchId': dispatch_id,
            'activeDispatchState': 'Menxia',
            'activeDispatchStartedAt': '2026-06-03T10:00:01Z',
        },
        'runSpec': {
            'policyGate': {
                'decision': 'hold_for_policy',
                'status': 'waiting_policy_approval',
                'reason': 'browser.control 需要确认',
                'requiresApproval': True,
            },
        },
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')
    outbox_path.write_text(json.dumps([
        {
            'id': dispatch_id,
            'kind': 'dispatch',
            'taskId': task_id,
            'state': 'Menxia',
            'agentId': 'menxia',
            'trigger': 'startup-recovery',
            'status': 'running',
            'attempts': 1,
            'createdAt': '2026-06-03T10:00:01Z',
            'updatedAt': '2026-06-03T10:00:01Z',
            'payload': {'traceId': 'trc_policy_worker'},
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: (_ for _ in ()).throw(AssertionError('gateway should not be probed')))

    srv._execute_dispatch_outbox_item(json.loads(outbox_path.read_text(encoding='utf-8'))[0])

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    outbox = json.loads(outbox_path.read_text(encoding='utf-8'))[0]
    assert sched['lastDispatchStatus'] == 'policy-held'
    assert sched['lastDispatchError'] == 'browser.control 需要确认'
    assert 'activeDispatchId' not in sched
    assert outbox['status'] == 'done'
    assert outbox['result']['blockedByPolicy'] is True
    assert any('权限闸门拦截派发' in item['remark'] for item in updated['flow_log'])


def test_dispatch_skips_duplicate_unfinished_outbox(monkeypatch, tmp_path):
    """Repeated scans should not enqueue duplicate work for the same task/state/agent."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260602-DUP'
    task = {
        'id': task_id,
        'title': '重复派发测试',
        'state': 'Zhongshu',
        'org': '中书省',
        'updatedAt': '2026-06-02T10:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_existing',
            'kind': 'dispatch',
            'taskId': task_id,
            'state': 'Zhongshu',
            'agentId': 'zhongshu',
            'trigger': 'kanban-state',
            'status': 'pending',
            'attempts': 0,
            'maxAttempts': 1,
            'createdAt': '2026-06-02T10:00:01Z',
            'updatedAt': '2026-06-02T10:00:01Z',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)
    kicked = {'value': False}
    monkeypatch.setattr(srv, '_kick_dispatch_worker', lambda: kicked.__setitem__('value', True))

    srv.dispatch_for_state(task_id, task, 'Zhongshu', trigger='scan-retry')

    outbox = json.loads(outbox_path.read_text(encoding='utf-8'))
    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]

    assert len(outbox) == 1
    assert outbox[0]['id'] == 'dispatch_existing'
    assert updated['_scheduler']['activeDispatchId'] == 'dispatch_existing'
    assert kicked['value'] is True


def test_dispatch_uses_opencode_run_attach(monkeypatch, tmp_path):
    """OpenCode mode should dispatch through `opencode run --attach --dir --agent`."""
    import event_log
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260526-002'
    task = {
        'id': task_id,
        'title': '启动 OpenCode 适配',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-26T16:00:00Z',
        'runSpec': {
            'executionIsolation': {
                'mode': 'patch_first_shared_worktree',
                'targetMode': 'dedicated_worktree',
                'label': 'Patch-first 隔离',
                'required': True,
                'patchFirst': True,
                'requiresPatchReview': True,
                'checkpoint': 'before_dispatch',
                'rollback': 'reverse_patch_or_checkpoint',
                'reason': '测试隔离约束',
                'guardrails': ['审批前禁止 commit、push'],
            },
        },
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')
    worktree_dir = tmp_path / 'task-worktree'
    worktree_dir.mkdir()

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setenv('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': True)
    monkeypatch.setattr(srv, '_opencode_session_error', lambda session_id: '')
    monkeypatch.setattr(
        srv,
        '_allocate_task_worktree',
        lambda task_id, isolation: {
            **isolation,
            'mode': 'dedicated_worktree',
            'status': 'active',
            'worktreePath': str(worktree_dir),
            'worktreeBranch': 'edict/JJC-20260526-002',
            'baseHead': 'abc1234',
        },
    )
    monkeypatch.setattr(
        srv,
        'save_tasks',
        lambda tasks: tasks_path.write_text(
            json.dumps(tasks, ensure_ascii=False),
            encoding='utf-8',
        ),
    )

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Completed:
        returncode = 0
        stdout = '{"sessionID":"ses_ok"}\n'
        stderr = ''

    captured = {'cmds': [], 'envs': []}

    def fake_run(cmd, **kwargs):
        captured['cmds'].append(cmd)
        captured['envs'].append(kwargs.get('env') or {})
        return Completed()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv, '_run_capture_timeout', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    opencode_cmd = next(cmd for cmd in captured['cmds'] if cmd[:2] == ['/usr/local/bin/opencode', 'run'])
    assert opencode_cmd[opencode_cmd.index('--attach') + 1] == 'http://127.0.0.1:4096'
    assert opencode_cmd[opencode_cmd.index('--dir') + 1] == str(worktree_dir)
    assert opencode_cmd[opencode_cmd.index('--agent') + 1] == 'taizi'
    assert '[trc_' in opencode_cmd[opencode_cmd.index('--title') + 1]
    assert captured['envs'][0]['EDICT_TASK_ID'] == task_id
    assert captured['envs'][0]['EDICT_TRACE_ID'].startswith('trc_')
    assert captured['envs'][0]['EDICT_AGENT_ID'] == 'taizi'
    assert captured['envs'][0]['EDICT_ISOLATION_MODE'] == 'dedicated_worktree'
    assert captured['envs'][0]['EDICT_WORKTREE_PATH'] == str(worktree_dir)
    assert captured['envs'][0]['EDICT_PATCH_FIRST'] == '1'
    assert captured['envs'][0]['EDICT_PATCH_REVIEW_REQUIRED'] == '1'
    assert 'Patch-first 隔离' in opencode_cmd[-1]
    assert str(worktree_dir) in opencode_cmd[-1]
    assert '审批前禁止 commit、push' in opencode_cmd[-1]

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert sched['lastDispatchStatus'] == 'success'
    assert sched['lastDispatchSession'] == 'ses_ok'
    assert sched['lastDispatchTraceId'] == updated['traceId']
    assert sched['runtimeSessions'][-1]['sessionId'] == 'ses_ok'
    assert sched['runtimeSessions'][-1]['traceId'] == updated['traceId']
    events = event_log.list_events(task_id=task_id)
    bound = next(e for e in events if e['kind'] == 'dispatch_session_bound')
    assert bound['traceId'] == updated['traceId']
    assert bound['sessionId'] == 'ses_ok'
    assert bound['payload']['dispatchId'] == sched['lastDispatchSessionDispatchId']


def test_opencode_session_not_found_restarts_and_retries(monkeypatch, tmp_path):
    """A stale OpenCode server should be restarted once before failing dispatch."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260602-004'
    task = {
        'id': task_id,
        'title': '查询最新大模型新闻',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-06-02T01:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setenv('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': True)
    monkeypatch.setattr(srv, '_opencode_session_error', lambda session_id: '')
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    restarts = []
    monkeypatch.setattr(srv, '_restart_opencode_server', lambda: restarts.append(True) or True)

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Failed:
        returncode = 1
        stdout = ''
        stderr = '\x1b[91m\x1b[1mError: \x1b[0mSession not found\n'

    class Completed:
        returncode = 0
        stdout = '{"sessionID":"ses_ok"}\n'
        stderr = ''

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Failed() if len(calls) == 1 else Completed()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv, '_run_capture_timeout', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='imperial-edict')

    assert len(calls) == 2
    assert restarts == [True]
    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    assert updated['_scheduler']['lastDispatchStatus'] == 'success'
    assert updated['_scheduler']['lastDispatchSession'] == 'ses_ok'


def test_opencode_session_message_not_found_restarts_and_retries(monkeypatch, tmp_path):
    """A missing session message endpoint should use the same stale-session recovery path."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260602-005'
    task = {
        'id': task_id,
        'title': '派发后读取 session 失败',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-06-02T02:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setenv('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': True)
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    restarts = []
    monkeypatch.setattr(srv, '_restart_opencode_server', lambda: restarts.append(True) or True)

    session_errors = iter([
        'OpenCode session 结果读取失败: HTTP Error 404: Not Found',
        '',
    ])
    monkeypatch.setattr(srv, '_opencode_session_error', lambda session_id: next(session_errors))

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Completed:
        returncode = 0
        stderr = ''

        def __init__(self, session_id):
            self.stdout = f'{{"sessionID":"{session_id}"}}\n'

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Completed('ses_bad' if len(calls) == 1 else 'ses_ok')

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv, '_run_capture_timeout', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='imperial-edict')

    assert len(calls) == 2
    assert restarts == [True]
    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    assert updated['_scheduler']['lastDispatchStatus'] == 'success'
    assert updated['_scheduler']['lastDispatchSession'] == 'ses_ok'


def test_opencode_session_preflight_restarts_before_dispatch(monkeypatch, tmp_path):
    """OpenCode session preflight should restart stale server before calling `opencode run`."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260602-006'
    task = {
        'id': task_id,
        'title': '派发前 session 预检',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-06-02T03:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setenv('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': True)
    monkeypatch.setattr(srv, '_opencode_session_error', lambda session_id: '')
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    probes = []
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': probes.append(agent_id) and len(probes) > 1)
    restarts = []
    monkeypatch.setattr(srv, '_restart_opencode_server', lambda: restarts.append(True) or True)

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Completed:
        returncode = 0
        stdout = '{"sessionID":"ses_ok"}\n'
        stderr = ''

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Completed()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv, '_run_capture_timeout', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='imperial-edict')

    assert probes == ['taizi']
    assert restarts == [True]
    assert len(calls) == 1
    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    assert updated['_scheduler']['lastDispatchStatus'] == 'success'
    assert updated['_scheduler']['lastDispatchSession'] == 'ses_ok'


def test_stale_dispatch_result_does_not_override_newer_progress(monkeypatch, tmp_path):
    """A late dispatch result must not overwrite progress from a newer task state."""
    import event_log
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    task_id = 'JJC-20260527-001'
    task = {
        'id': task_id,
        'title': '审查代码',
        'state': 'Taizi',
        'org': '太子',
        'updatedAt': '2026-05-27T12:00:00Z',
    }
    tasks_path = data_dir / 'tasks_source.json'
    tasks_path.write_text(json.dumps([task], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_resolve_opencode_bin', lambda: '/usr/local/bin/opencode')
    monkeypatch.setattr(srv, '_opencode_session_probe', lambda agent_id='taizi': True)
    monkeypatch.setattr(srv, '_opencode_session_error', lambda session_id: '')
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    class ImmediateThread:
        def __init__(self, target=None, daemon=None):
            self.target = target

        def start(self):
            if self.target:
                self.target()

    class Completed:
        returncode = 0
        stdout = '{"sessionID":"ses_ok"}\n'
        stderr = ''

    def fake_run(cmd, **kwargs):
        tasks = json.loads(tasks_path.read_text(encoding='utf-8'))
        tasks[0]['state'] = 'Zhongshu'
        tasks[0]['org'] = '中书省'
        sched = tasks[0]['_scheduler']
        sched['lastDispatchStatus'] = 'progress'
        sched['lastProgressAt'] = '2026-05-27T12:00:05Z'
        sched.pop('activeDispatchId', None)
        tasks_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding='utf-8')
        return Completed()

    monkeypatch.setattr(srv.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(srv, '_run_capture_timeout', fake_run)

    srv.dispatch_for_state(task_id, task, 'Taizi', trigger='test')

    updated = json.loads(tasks_path.read_text(encoding='utf-8'))[0]
    sched = updated['_scheduler']
    assert updated['state'] == 'Zhongshu'
    assert sched['lastDispatchStatus'] == 'progress'
    assert sched.get('lastDispatchSession') != 'ses_ok'
    assert not sched.get('runtimeSessions')
    assert not any(e['kind'] == 'dispatch_session_bound' for e in event_log.list_events(task_id=task_id))


def test_opencode_agents_are_idle_without_recent_session(monkeypatch):
    """OpenCode server availability should not make every agent look busy."""
    import server as srv

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_opencode_agent_names', lambda: {d['id'] for d in srv._AGENT_DEPTS})
    monkeypatch.setattr(srv, '_opencode_config_has_agent', lambda agent_id: True)
    monkeypatch.setattr(srv, '_get_opencode_agent_session_status', lambda agent_id: (0, 0, False))

    data = srv.get_agents_status()

    assert data['gateway']['runtime'] == 'opencode'
    assert data['gateway']['label'] == 'OpenCode'
    taizi = next(a for a in data['agents'] if a['id'] == 'taizi')
    assert taizi['status'] == 'idle'
    assert taizi['statusLabel'] == '🟡 待命'
    assert taizi['processAlive'] is False


def test_opencode_recent_session_marks_agent_running(monkeypatch):
    """Recent OpenCode session activity should still surface as running."""
    import server as srv

    now_ms = int(srv.datetime.datetime.now().timestamp() * 1000)
    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, '_check_gateway_alive', lambda: True)
    monkeypatch.setattr(srv, '_check_gateway_probe', lambda: True)
    monkeypatch.setattr(srv, '_opencode_agent_names', lambda: {d['id'] for d in srv._AGENT_DEPTS})
    monkeypatch.setattr(srv, '_opencode_config_has_agent', lambda agent_id: True)
    monkeypatch.setattr(
        srv,
        '_get_opencode_agent_session_status',
        lambda agent_id: (now_ms, 1, agent_id == 'taizi'),
    )

    data = srv.get_agents_status()
    taizi = next(a for a in data['agents'] if a['id'] == 'taizi')
    zhongshu = next(a for a in data['agents'] if a['id'] == 'zhongshu')

    assert taizi['status'] == 'running'
    assert taizi['statusLabel'] == '🟢 运行中'
    assert zhongshu['status'] == 'idle'


def test_opencode_model_prefers_agent_config(monkeypatch, tmp_path):
    """OpenCode dispatch should honor per-agent dashboard model choices."""
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'agent_config.json').write_text(json.dumps({
        'runtime': 'opencode',
        'agents': [
            {'id': 'taizi', 'model': 'github-copilot/gpt-5.2-codex'},
        ],
    }, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'opencode.json').write_text('{}', encoding='utf-8')

    monkeypatch.setenv('OPENCODE_MODEL', 'opencode/deepseek-v4-flash-free')
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, 'BASE', tmp_path / 'dashboard')

    assert srv._opencode_model('taizi') == 'github-copilot/gpt-5.2-codex'
    assert srv._opencode_model('zhongshu') == 'opencode/deepseek-v4-flash-free'
