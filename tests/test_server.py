"""tests for dashboard/server.py route handling"""
import json, pathlib, shutil, subprocess, sys, threading, time
from http.client import HTTPConnection

import pytest

# Add project paths
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dashboard'))
sys.path.insert(0, str(ROOT / 'scripts'))


def test_healthz(tmp_path):
    """GET /healthz returns 200 with status ok."""
    # Create minimal data dir
    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    (data_dir / 'live_status.json').write_text('{}')
    (data_dir / 'agent_config.json').write_text('{}')

    # Import and patch server
    import server as srv
    srv.DATA = data_dir

    from http.server import HTTPServer
    port = 18971

    httpd = HTTPServer(('127.0.0.1', port), srv.Handler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()

    time.sleep(0.1)
    conn = HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('GET', '/healthz')
    resp = conn.getresponse()
    body = json.loads(resp.read())
    conn.close()

    assert resp.status == 200
    assert body['status'] in ('ok', 'degraded')

    httpd.server_close()


def test_legacy_dashboard_html_redirects_to_react_route(tmp_path, monkeypatch):
    import server as srv

    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>react</html>', encoding='utf-8')
    monkeypatch.setattr(srv, 'DIST', dist)

    from http.server import HTTPServer
    port = 18972

    httpd = HTTPServer(('127.0.0.1', port), srv.Handler)
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()

    time.sleep(0.1)
    conn = HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request('GET', '/dashboard.html')
    resp = conn.getresponse()
    resp.read()
    location = resp.getheader('Location')
    conn.close()

    assert resp.status == 301
    assert location == '/dashboard'

    httpd.server_close()


def test_output_files_lists_docs_and_task_outputs(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    docs = root / 'docs'
    dist = root / 'dashboard' / 'dist'
    data = root / 'data'
    docs.mkdir(parents=True)
    dist.mkdir(parents=True)
    data.mkdir(parents=True)
    report = docs / 'report.md'
    report.write_text('# Report\n', encoding='utf-8')
    bundle = dist / 'index.html'
    bundle.write_text('<html></html>', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-1', 'title': '报告任务', 'output': str(report)}
    ]), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DIST', dist)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    result = srv.list_output_files()
    paths = {item['path']: item for item in result['files']}

    assert result['ok'] is True
    assert 'docs/report.md' in paths
    assert 'dashboard/dist/index.html' in paths
    assert paths['docs/report.md']['source'] == '任务产出'
    assert paths['docs/report.md']['taskId'] == 'JJC-1'
    assert paths['docs/report.md']['viewUrl'].startswith('/api/output-file?')
    task_group = next(group for group in result['groups'] if group['taskId'] == 'JJC-1')
    assert task_group['taskTitle'] == '报告任务'
    assert [item['path'] for item in task_group['files']] == ['docs/report.md']


def test_output_file_resolver_stays_in_allowed_roots(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    docs = root / 'docs'
    docs.mkdir(parents=True)
    allowed = docs / 'report.md'
    allowed.write_text('# Report\n', encoding='utf-8')
    root.joinpath('README.md').write_text('# Root\n', encoding='utf-8')
    tmp_path.joinpath('secret.md').write_text('secret\n', encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DIST', root / 'dashboard' / 'dist')

    assert srv._safe_project_file('docs/report.md') == allowed.resolve()
    assert srv._safe_project_file('README.md') is None
    assert srv._safe_project_file('../secret.md') is None


def test_source_file_reader_returns_bounded_line_window(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    src = root / 'src'
    src.mkdir(parents=True)
    file_path = src / 'sample.py'
    file_path.write_text('a = 1\nb = 2\nc = 3\nd = 4\n', encoding='utf-8')
    (root / 'node_modules').mkdir()
    blocked = root / 'node_modules' / 'x.py'
    blocked.write_text('secret\n', encoding='utf-8')
    outside = tmp_path / 'outside.py'
    outside.write_text('secret\n', encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)

    result = srv.read_source_file('src/sample.py', 2, 3, context=1)

    assert result['ok'] is True
    assert result['path'] == 'src/sample.py'
    assert result['viewStart'] == 1
    assert result['viewEnd'] == 4
    assert [line['no'] for line in result['lines'] if line['highlight']] == [2, 3]
    assert srv.read_source_file(str(outside))['ok'] is False
    assert srv.read_source_file('node_modules/x.py')['ok'] is False


def test_open_source_file_uses_editor_line_target(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    src = root / 'src'
    src.mkdir(parents=True)
    file_path = src / 'sample.py'
    file_path.write_text('print("hi")\n', encoding='utf-8')
    outside = tmp_path / 'outside.py'
    outside.write_text('secret\n', encoding='utf-8')
    captured = {}

    def fake_which(name):
        return '/usr/local/bin/code' if name == 'code' else None

    def fake_popen(cmd, **kwargs):
        captured['cmd'] = cmd
        captured['kwargs'] = kwargs

        class Proc:
            pass

        return Proc()

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv.shutil, 'which', fake_which)
    monkeypatch.setattr(srv.subprocess, 'Popen', fake_popen)

    result = srv.open_source_file('src/sample.py', 3)

    assert result['ok'] is True
    assert result['editor'] == 'VS Code'
    assert captured['cmd'] == ['/usr/local/bin/code', '-g', f'{file_path.resolve()}:3']
    assert captured['kwargs']['cwd'] == str(root)
    assert srv.open_source_file(str(outside), 1)['ok'] is False


def test_worktree_checkpoint_summarizes_git_state(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'commit.gpgsign', 'false'], cwd=root, check=True)
    tracked = root / 'tracked.txt'
    tracked.write_text('one\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'tracked.txt'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    tracked.write_text('two\n', encoding='utf-8')
    (root / 'new.txt').write_text('new\n', encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)

    checkpoint = srv.get_worktree_checkpoint()

    assert checkpoint['ok'] is True
    assert checkpoint['available'] is True
    assert checkpoint['head']
    assert checkpoint['dirty'] is True
    assert checkpoint['unstagedCount'] == 1
    assert checkpoint['untrackedCount'] == 1
    paths = {item['path'] for item in checkpoint['files']}
    assert {'tracked.txt', 'new.txt'}.issubset(paths)


def test_runtime_outbox_health_exposes_dead_letters(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-FAIL-1', 'title': '派发失败任务', 'state': 'Taizi'},
        {'id': 'JJC-PEND-1', 'title': '等待派发任务', 'state': 'Zhongshu'},
    ], ensure_ascii=False), encoding='utf-8')
    outbox = [
        {
            'id': 'dispatch_failed',
            'kind': 'dispatch',
            'taskId': 'JJC-FAIL-1',
            'state': 'Taizi',
            'agentId': 'taizi',
            'trigger': 'test',
            'traceId': 'trc_fail',
            'status': 'failed',
            'attempts': 1,
            'maxAttempts': 1,
            'createdAt': '2026-05-31T09:00:00Z',
            'updatedAt': '2026-05-31T09:01:00Z',
            'lastError': 'gateway offline',
        },
        {
            'id': 'dispatch_pending',
            'kind': 'dispatch',
            'taskId': 'JJC-PEND-1',
            'state': 'Zhongshu',
            'agentId': 'zhongshu',
            'status': 'pending',
            'attempts': 0,
            'maxAttempts': 1,
            'createdAt': '2026-05-31T09:02:00Z',
            'updatedAt': '2026-05-31T09:02:00Z',
        },
    ]
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps(outbox, ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    health = srv.get_runtime_outbox_health()

    assert health['ok'] is True
    assert health['failed'] == 1
    assert health['pending'] == 1
    assert health['summary']['tone'] == 'err'
    assert health['summary']['label'] == '当前任务失败 1'
    assert health['summary']['blockingLayer'] == 'queue'
    assert health['layers']['current']['failed'] == 1
    assert health['layers']['current']['pending'] == 1
    assert health['trend']['failed'] == 0
    assert health['deadLetters'][0]['taskId'] == 'JJC-FAIL-1'
    assert health['deadLetters'][0]['taskTitle'] == '派发失败任务'
    assert health['deadLetters'][0]['lastError'] == 'gateway offline'
    assert health['activeItems'][0]['taskId'] == 'JJC-PEND-1'


def test_runtime_outbox_health_separates_ghost_pending_from_current_failures(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-CURRENT-1', 'title': '当前代码任务', 'state': 'Taizi'},
    ], ensure_ascii=False), encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'ghost_handoff',
            'kind': 'handoff',
            'taskId': 'T-4',
            'state': 'Review',
            'agentId': 'shangshu',
            'status': 'pending',
            'createdAt': '2026-06-12T15:03:22Z',
            'updatedAt': '2026-06-12T15:03:22Z',
        },
        {
            'id': 'current_timeout',
            'kind': 'dispatch',
            'taskId': 'JJC-CURRENT-1',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'failed',
            'createdAt': '2026-06-12T15:04:00Z',
            'updatedAt': '2026-06-12T15:04:20Z',
            'lastError': 'OpenCode 执行请求超时（taizi，imperial-edict）',
        },
        {
            'id': 'current_failed',
            'kind': 'dispatch',
            'taskId': 'JJC-CURRENT-1',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'failed',
            'createdAt': '2026-06-12T15:04:22Z',
            'updatedAt': '2026-06-12T15:05:22Z',
            'lastError': 'opencode/deepseek-v4-flash-free: unknown certificate verification error',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    health = srv.get_runtime_outbox_health()

    assert health['layers']['current']['failed'] == 2
    assert health['layers']['ghost']['pending'] == 1
    assert health['layers']['current']['label'] == '当前任务阻塞'
    assert health['layers']['ghost']['label'] == '幽灵任务噪音'
    assert health['summary']['tone'] == 'err'
    assert health['summary']['blockingLayer'] == 'model'
    assert '模型连接失败' in health['summary']['detail']
    assert '幽灵任务' in health['layers']['ghost']['detail']


def test_runtime_outbox_health_downgrades_history_only_failures(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'old_failed',
            'kind': 'dispatch',
            'taskId': 'JJC-OLD-1',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'failed',
            'createdAt': '2026-06-10T10:00:00Z',
            'updatedAt': '2026-06-10T10:01:00Z',
            'lastError': 'OpenCode 执行请求超时（taizi，imperial-edict）',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    health = srv.get_runtime_outbox_health()

    assert health['failed'] == 1
    assert health['layers']['current']['failed'] == 0
    assert health['layers']['history']['failed'] == 1
    assert health['summary']['tone'] == 'warn'
    assert health['summary']['label'] == '仅历史失败'
    assert '不会判定当前任务失败' in health['summary']['detail']


def test_missing_task_handoff_is_closed_as_stale(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'handoff_missing',
            'kind': 'handoff',
            'taskId': 'T-MISSING',
            'state': 'Review',
            'agentId': 'shangshu',
            'trigger': 'kanban-done',
            'traceId': 'trc_missing',
            'status': 'pending',
            'attempts': 0,
            'maxAttempts': 3,
            'createdAt': '2026-06-10T00:00:00Z',
            'updatedAt': '2026-06-10T00:00:00Z',
            'lastError': '',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    srv._process_handoff_outbox_item(json.loads(outbox_path.read_text(encoding='utf-8'))[0])

    updated = json.loads(outbox_path.read_text(encoding='utf-8'))[0]
    assert updated['status'] == 'done'
    assert updated['result']['stale'] is True
    assert updated['result']['missingTask'] is True
    assert 'T-MISSING' in updated['result']['reason']
    assert srv.get_runtime_outbox_health()['failed'] == 0


def test_runtime_outbox_health_warns_about_stale_pending(tmp_path, monkeypatch):
    import datetime as dt
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-PEND-OLD', 'title': '长时间等待派发', 'state': 'Taizi'},
    ], ensure_ascii=False), encoding='utf-8')
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=900)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([{
        'id': 'dispatch_old_pending',
        'kind': 'dispatch',
        'taskId': 'JJC-PEND-OLD',
        'state': 'Taizi',
        'agentId': 'taizi',
        'status': 'pending',
        'attempts': 0,
        'maxAttempts': 1,
        'createdAt': old,
        'updatedAt': old,
    }], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_ACTIVE', True)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_HEARTBEAT_AT', old)

    health = srv.get_runtime_outbox_health()

    assert health['pending'] == 1
    assert health['oldestPendingAgeSec'] >= 899
    assert health['oldestRunningAgeSec'] == 0
    assert health['worker']['active'] is True
    assert health['worker']['heartbeatAgeSec'] >= 899
    assert health['summary']['tone'] == 'warn'
    assert health['summary']['label'] == 'Pending 堆积'
    assert health['trend']['enqueued'] == 0


def test_runtime_outbox_health_uses_oldest_queue_item(tmp_path, monkeypatch):
    import datetime as dt
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    now = dt.datetime.now(dt.timezone.utc)
    young = (now - dt.timedelta(seconds=60)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    old = (now - dt.timedelta(seconds=1200)).isoformat(timespec='seconds').replace('+00:00', 'Z')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {'id': 'pending_young', 'kind': 'dispatch', 'status': 'pending', 'createdAt': young, 'updatedAt': young},
        {'id': 'pending_old', 'kind': 'dispatch', 'status': 'pending', 'createdAt': old, 'updatedAt': old},
        {'id': 'running_young', 'kind': 'dispatch', 'status': 'running', 'createdAt': young, 'claimedAt': young},
        {'id': 'running_old', 'kind': 'dispatch', 'status': 'running', 'createdAt': old, 'claimedAt': old},
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_ACTIVE', True)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_HEARTBEAT_AT', now.isoformat(timespec='seconds').replace('+00:00', 'Z'))

    health = srv.get_runtime_outbox_health()

    assert health['oldestPendingAgeSec'] >= 1199
    assert health['oldestRunningAgeSec'] >= 1199
    assert health['summary']['label'] == 'Pending 堆积'


def test_runtime_outbox_health_warns_about_stale_worker_heartbeat(tmp_path, monkeypatch):
    import datetime as dt
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text('[]', encoding='utf-8')
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=900)).isoformat(timespec='seconds').replace('+00:00', 'Z')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_ACTIVE', True)
    monkeypatch.setattr(srv, '_DISPATCH_WORKER_HEARTBEAT_AT', old)

    health = srv.get_runtime_outbox_health()

    assert health['summary']['tone'] == 'warn'
    assert health['summary']['label'] == 'Worker 心跳旧'
    assert health['worker']['heartbeatAgeSec'] >= 899


def test_runtime_outbox_public_item_sanitizes_json_event_error():
    import server as srv

    event_stream = '\n'.join([
        json.dumps({'type': 'step_start', 'part': {'type': 'step-start'}}),
        json.dumps({'type': 'message_updated', 'part': {'type': 'text', 'text': 'working'}}),
    ])

    public = srv._public_outbox_item({
        'id': 'dispatch_failed_json',
        'kind': 'dispatch',
        'taskId': 'JJC-FAIL-JSON',
        'status': 'failed',
        'attempts': 1,
        'maxAttempts': 1,
        'createdAt': '2026-05-31T09:00:00Z',
        'lastError': event_stream,
    })

    assert public['lastError'] == '运行时返回了事件流，未给出明确错误'
    assert 'step_start' not in public['lastError']


def test_runtime_outbox_retry_requeues_failed_item(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-FAIL-2', 'title': '重新入队任务', 'state': 'Taizi'},
    ], ensure_ascii=False), encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_failed',
            'kind': 'dispatch',
            'taskId': 'JJC-FAIL-2',
            'state': 'Taizi',
            'agentId': 'taizi',
            'trigger': 'test',
            'traceId': 'trc_retry',
            'status': 'failed',
            'attempts': 1,
            'maxAttempts': 1,
            'createdAt': '2026-05-31T09:00:00Z',
            'updatedAt': '2026-05-31T09:01:00Z',
            'finishedAt': '2026-05-31T09:01:00Z',
            'lastError': 'OpenCode CLI 未找到',
        },
    ], ensure_ascii=False), encoding='utf-8')

    kicked = {'value': False}
    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)
    monkeypatch.setattr(srv, '_kick_dispatch_worker', lambda: kicked.update(value=True))

    result = srv.handle_runtime_outbox_retry('dispatch_failed', 'test retry')

    updated = json.loads(outbox_path.read_text(encoding='utf-8'))[0]
    assert result['ok'] is True
    assert updated['status'] == 'pending'
    assert updated['attempts'] == 0
    assert updated['lastError'] == ''
    assert updated['result']['requeueReason'] == 'test retry'
    assert kicked['value'] is True


def test_runtime_outbox_archive_hides_failed_item(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-FAIL-3', 'title': '待归档失败任务', 'state': 'Taizi'},
    ], ensure_ascii=False), encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_failed',
            'kind': 'dispatch',
            'taskId': 'JJC-FAIL-3',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'failed',
            'attempts': 1,
            'maxAttempts': 1,
            'createdAt': '2026-05-31T09:00:00Z',
            'updatedAt': '2026-05-31T09:01:00Z',
            'lastError': 'gateway offline',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    result = srv.handle_runtime_outbox_archive('dispatch_failed', reason='test archive')
    health = srv.get_runtime_outbox_health()
    updated = json.loads(outbox_path.read_text(encoding='utf-8'))[0]

    assert result['ok'] is True
    assert result['count'] == 1
    assert updated['status'] == 'archived'
    assert updated['result']['archiveReason'] == 'test archive'
    assert health['failed'] == 0
    assert health['archived'] == 1
    assert health['deadLetters'] == []


def test_runtime_outbox_archive_all_failed_items(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    outbox_path = data_dir / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {'id': 'failed_a', 'kind': 'dispatch', 'taskId': 'JJC-A', 'status': 'failed'},
        {'id': 'failed_b', 'kind': 'dispatch', 'taskId': 'JJC-B', 'status': 'failed'},
        {'id': 'pending_c', 'kind': 'dispatch', 'taskId': 'JJC-C', 'status': 'pending'},
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv._runtime_outbox, 'OUTBOX_FILE', outbox_path)

    result = srv.handle_runtime_outbox_archive(archive_all_failed=True, reason='batch archive')
    updated = json.loads(outbox_path.read_text(encoding='utf-8'))

    assert result['ok'] is True
    assert result['count'] == 2
    assert [item['status'] for item in updated] == ['archived', 'archived', 'pending']


def test_scheduler_state_exposes_opencode_session_diagnosis(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-DIAG-SESSION',
            'title': 'OpenCode 会话失效',
            'state': 'Taizi',
            'org': '太子',
            'updatedAt': '2026-06-02T09:00:00Z',
            '_scheduler': {
                'enabled': True,
                'lastDispatchStatus': 'opencode-session-stale',
                'lastDispatchError': 'Session not found',
                'stallThresholdSec': 180,
            },
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)

    result = srv.get_scheduler_state('JJC-DIAG-SESSION')

    assert result['ok'] is True
    diag = result['dispatchDiagnosis']
    assert diag['tone'] == 'warn'
    assert diag['label'] == 'OpenCode 会话失效'
    assert diag['retryable'] is True
    assert diag['action'] == 'retry'
    assert diag['actionLabel'] == '重新交办'
    assert 'OpenCode 会话失效' in diag['actionReason']
    assert 'Session not found' in diag['detail']


def test_scheduler_state_exposes_runtime_session_binding(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-DIAG-BIND',
            'title': '绑定 OpenCode session',
            'state': 'Taizi',
            'org': '太子',
            'traceId': 'trc_bind_123456',
            'updatedAt': '2026-06-02T09:00:00Z',
            '_scheduler': {
                'enabled': True,
                'lastDispatchStatus': 'success',
                'lastDispatchAgent': 'taizi',
                'lastDispatchState': 'Taizi',
                'lastDispatchTrigger': 'imperial-edict',
                'lastDispatchSession': 'ses_bind_abcdef',
                'lastDispatchTraceId': 'trc_bind_123456',
                'lastDispatchRuntime': 'opencode',
                'lastDispatchSessionBoundAt': '2026-06-02T09:00:05Z',
                'lastDispatchSessionDispatchId': 'dispatch_bind',
                'runtimeSessions': [{
                    'sessionId': 'ses_bind_abcdef',
                    'traceId': 'trc_bind_123456',
                    'agentId': 'taizi',
                    'runtime': 'opencode',
                    'dispatchId': 'dispatch_bind',
                    'trigger': 'imperial-edict',
                    'state': 'Taizi',
                    'boundAt': '2026-06-02T09:00:05Z',
                }],
            },
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)

    result = srv.get_scheduler_state('JJC-DIAG-BIND')

    assert result['ok'] is True
    binding = result['runtimeSession']
    assert binding['status'] == 'bound'
    assert binding['bound'] is True
    assert binding['sessionId'] == 'ses_bind_abcdef'
    assert binding['traceId'] == 'trc_bind_123456'
    assert binding['runtime'] == 'opencode'
    assert binding['dispatchId'] == 'dispatch_bind'
    assert result['runtimeSessions'][0]['sessionId'] == 'ses_bind_abcdef'


def test_scheduler_state_recovers_runtime_session_from_ledger(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-DIAG-LEDGER-BIND',
            'title': '从事件账本恢复 OpenCode session',
            'state': 'Doing',
            'org': '户部',
            'traceId': 'trc_ledger_bind',
            'updatedAt': '2026-06-02T09:00:00Z',
            '_scheduler': {
                'enabled': True,
                'lastDispatchStatus': 'success',
                'lastDispatchAgent': 'hubu',
                'lastDispatchState': 'Doing',
                'lastDispatchTrigger': 'imperial-edict',
            },
        },
    ], ensure_ascii=False), encoding='utf-8')

    events = [{
        'kind': 'dispatch_session_bound',
        'taskId': 'JJC-DIAG-LEDGER-BIND',
        'traceId': 'trc_ledger_bind',
        'sessionId': 'ses_ledger_bind',
        'agentId': 'hubu',
        'at': '2026-06-02T09:00:05Z',
        'payload': {
            'sessionId': 'ses_ledger_bind',
            'dispatchId': 'dispatch_ledger_bind',
            'trigger': 'imperial-edict',
            'state': 'Doing',
        },
    }]

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)
    monkeypatch.setattr(srv, '_ledger_list_events', lambda task_id='', limit=200: events if task_id == 'JJC-DIAG-LEDGER-BIND' else [])

    result = srv.get_scheduler_state('JJC-DIAG-LEDGER-BIND')

    assert result['ok'] is True
    binding = result['runtimeSession']
    assert binding['status'] == 'bound'
    assert binding['sessionId'] == 'ses_ledger_bind'
    assert binding['traceId'] == 'trc_ledger_bind'
    assert binding['dispatchId'] == 'dispatch_ledger_bind'
    assert result['runtimeSessions'][0]['source'] == 'event-ledger'


def test_scheduler_state_warns_when_success_dispatch_stalls(tmp_path, monkeypatch):
    import server as srv

    data_dir = tmp_path / 'data'
    data_dir.mkdir()
    old = (
        srv.datetime.datetime.now(srv.datetime.timezone.utc)
        - srv.datetime.timedelta(seconds=600)
    ).isoformat()
    data_dir.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-DIAG-STALL',
            'title': '派发后未推进',
            'state': 'Zhongshu',
            'org': '中书省',
            'updatedAt': old,
            '_scheduler': {
                'enabled': True,
                'lastDispatchStatus': 'success',
                'lastDispatchAt': old,
                'lastProgressAt': old,
                'stallThresholdSec': 180,
            },
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data_dir)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data_dir)

    result = srv.get_scheduler_state('JJC-DIAG-STALL')

    assert result['ok'] is True
    diag = result['dispatchDiagnosis']
    assert diag['tone'] == 'warn'
    assert diag['label'] == '已交办但未推进'
    assert diag['retryable'] is True
    assert diag['action'] == 'scan'
    assert diag['actionLabel'] == '立即扫描'
    assert '已交办但未推进' in diag['actionReason']
    assert '立即扫描' in diag['nextAction']


def test_runtime_outbox_requeues_orphaned_running_item(tmp_path, monkeypatch):
    import runtime_outbox

    outbox_path = tmp_path / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_running',
            'kind': 'dispatch',
            'taskId': 'JJC-RUN-1',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'running',
            'attempts': 1,
            'maxAttempts': 1,
            'claimedBy': 'dashboard-old',
            'claimedAt': '2026-06-02T01:00:00Z',
            'createdAt': '2026-06-02T01:00:00Z',
            'updatedAt': '2026-06-02T01:00:00Z',
        },
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(runtime_outbox, 'OUTBOX_FILE', outbox_path)

    result = runtime_outbox.requeue_orphaned_running('dashboard-new', 'startup recovery')

    updated = json.loads(outbox_path.read_text(encoding='utf-8'))[0]
    assert result['count'] == 1
    assert updated['status'] == 'pending'
    assert updated['attempts'] == 0
    assert updated['claimedAt'] == ''
    assert 'claimedBy' not in updated
    assert updated['lastError'] == 'startup recovery'


def test_runtime_outbox_claim_clears_previous_error(tmp_path, monkeypatch):
    import runtime_outbox

    outbox_path = tmp_path / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_pending',
            'kind': 'dispatch',
            'taskId': 'JJC-RUN-2',
            'state': 'Taizi',
            'agentId': 'taizi',
            'status': 'pending',
            'attempts': 0,
            'maxAttempts': 2,
            'lastError': 'dashboard startup recovery',
            'createdAt': '2026-06-02T01:00:00Z',
            'updatedAt': '2026-06-02T01:00:00Z',
        },
    ], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(runtime_outbox, 'OUTBOX_FILE', outbox_path)

    claimed = runtime_outbox.claim_pending(worker_id='dashboard-new', limit=1)

    updated = json.loads(outbox_path.read_text(encoding='utf-8'))[0]
    assert claimed[0]['id'] == 'dispatch_pending'
    assert updated['status'] == 'running'
    assert updated['lastError'] == ''


def test_runtime_outbox_compacts_unfinished_duplicates(tmp_path, monkeypatch):
    import runtime_outbox

    outbox_path = tmp_path / 'runtime_outbox.json'
    outbox_path.write_text(json.dumps([
        {
            'id': 'dispatch_running',
            'kind': 'dispatch',
            'taskId': 'JJC-DUP-1',
            'state': 'Zhongshu',
            'agentId': 'zhongshu',
            'status': 'running',
            'attempts': 1,
            'maxAttempts': 1,
        },
        {
            'id': 'dispatch_pending',
            'kind': 'dispatch',
            'taskId': 'JJC-DUP-1',
            'state': 'Zhongshu',
            'agentId': 'zhongshu',
            'status': 'pending',
            'attempts': 0,
            'maxAttempts': 1,
        },
    ], ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(runtime_outbox, 'OUTBOX_FILE', outbox_path)

    result = runtime_outbox.compact_unfinished_duplicates('duplicate cleanup')

    updated = json.loads(outbox_path.read_text(encoding='utf-8'))
    assert result['count'] == 1
    assert updated[0]['status'] == 'running'
    assert updated[1]['status'] == 'cancelled'
    assert updated[1]['lastError'] == 'duplicate cleanup'
    assert updated[1]['result']['dedupedInto'] == 'dispatch_running'


def test_opencode_activity_parser_tolerates_nondict_shapes(monkeypatch):
    import server as srv

    monkeypatch.setattr(srv, '_opencode_parts_for_message', lambda message: [
        {'type': 'tool', 'tool': 'bash', 'state': True, 'time': True},
        {'type': 'step-finish', 'tokens': True, 'cost': 0},
        {'type': 'text', 'text': '处理完成'},
    ])

    search_text = srv._opencode_message_search_text({'id': 'msg_test', 'summary': True}, include_parts=True)
    entries = srv._parse_opencode_parts({'id': 'msg_test', 'role': 'assistant', 'time': True})

    assert 'msg_test' in search_text
    assert any(entry.get('text') == '处理完成' for entry in entries)


def test_runtime_error_summary_hides_json_event_stream():
    import server as srv

    event_stream = '\n'.join([
        json.dumps({'type': 'step_start', 'part': {'type': 'step-start'}}),
        json.dumps({'type': 'message_updated', 'part': {'type': 'text', 'text': 'working'}}),
    ])
    error_stream = json.dumps({
        'type': 'error',
        'error': {'data': {'message': 'unknown certificate verification error'}},
    })
    truncated_event = '{"type":"step_start","timestamp":1780358912332,"sessionID":"ses_123","part":{"type":"step-start"'

    assert srv._runtime_error_summary(event_stream, default='OpenCode timeout') == 'OpenCode timeout'
    assert srv._runtime_error_summary(error_stream, default='OpenCode timeout') == 'unknown certificate verification error'
    assert srv._runtime_error_summary(truncated_event, default='OpenCode timeout') == 'OpenCode timeout'


def test_patch_review_create_and_approve(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import event_log
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    src = root / 'src'
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    file_path = src / 'app.py'
    file_path.write_text('print("one")\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'src/app.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    file_path.write_text('print("two")\n', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-PATCH-1', 'title': 'Patch 审批任务', 'state': 'Doing', 'traceId': 'trc_patch'}
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    created = srv.create_patch_review('JJC-PATCH-1', ['src/app.py'])

    assert created['ok'] is True
    review = created['review']
    assert review['status'] == 'pending'
    assert review['paths'] == ['src/app.py']
    assert '+print("two")' in review['diffPreview']
    assert review['stats']['insertions'] == 1
    approved = srv.handle_patch_review_action(review['id'], 'approve', 'looks good')
    assert approved['ok'] is True
    assert approved['review']['status'] == 'approved'
    stored = json.loads((data / 'patch_reviews.json').read_text(encoding='utf-8'))[0]
    assert stored['decisionReason'] == 'looks good'
    events = event_log.list_events(task_id='JJC-PATCH-1')
    kinds = [event['kind'] for event in events]
    assert kinds == ['patch_review_created', 'patch_review_approved']
    assert {event['traceId'] for event in events} == {'trc_patch'}
    assert events[0]['payload']['stats']['insertions'] == 1
    assert events[0]['payload']['fileCount'] == 1
    assert events[1]['payload']['reason'] == 'looks good'


def test_patch_review_reject_reverts_worktree(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    src = root / 'src'
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    file_path = src / 'app.py'
    file_path.write_text('print("one")\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'src/app.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    file_path.write_text('print("two")\n', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-PATCH-2', 'title': 'Patch 驳回任务', 'state': 'Doing'}
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    created = srv.create_patch_review('JJC-PATCH-2', ['src/app.py'])
    rejected = srv.handle_patch_review_action(created['review']['id'], 'reject', 'nope')

    assert rejected['ok'] is True
    assert rejected['review']['status'] == 'rejected'
    assert file_path.read_text(encoding='utf-8') == 'print("one")\n'


def test_patch_review_includes_untracked_new_file_and_reject_removes_it(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    src = root / 'src'
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    readme = root / 'README.md'
    readme.write_text('# repo\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'README.md'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    new_file = src / 'new_feature.py'
    new_file.write_text('print("new")\n', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-PATCH-NEW', 'title': '新增文件审批', 'state': 'Doing'}
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    created = srv.create_patch_review('JJC-PATCH-NEW', ['src/new_feature.py'])

    assert created['ok'] is True
    review = created['review']
    assert review['paths'] == ['src/new_feature.py']
    assert 'new file mode' in review['diffPreview']
    assert '+print("new")' in review['diffPreview']
    assert review['stats']['insertions'] == 1
    assert review['stats']['files'][0]['status'] == 'added'

    rejected = srv.handle_patch_review_action(review['id'], 'reject', '不要新增')
    assert rejected['ok'] is True
    assert rejected['review']['status'] == 'rejected'
    assert not new_file.exists()


def test_patch_review_deleted_file_reject_restores_it(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    src = root / 'src'
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    deleted_file = src / 'old_feature.py'
    deleted_file.write_text('print("old")\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'src/old_feature.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    deleted_file.unlink()
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {'id': 'JJC-PATCH-DEL', 'title': '删除文件审批', 'state': 'Doing'}
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    created = srv.create_patch_review('JJC-PATCH-DEL', ['src/old_feature.py'])

    assert created['ok'] is True
    review = created['review']
    assert review['paths'] == ['src/old_feature.py']
    assert 'deleted file mode' in review['diffPreview']
    assert '-print("old")' in review['diffPreview']
    assert review['stats']['deletions'] == 1
    assert review['stats']['files'][0]['status'] == 'deleted'

    rejected = srv.handle_patch_review_action(review['id'], 'reject', '恢复删除')
    assert rejected['ok'] is True
    assert rejected['review']['status'] == 'rejected'
    assert deleted_file.read_text(encoding='utf-8') == 'print("old")\n'


def test_patch_review_uses_mentioned_worktree_file_when_no_tool_event(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    outputs = root / 'outputs'
    data.mkdir(parents=True)
    outputs.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    readme = root / 'README.md'
    readme.write_text('# repo\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'README.md'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    report = outputs / 'weekly-report.md'
    report.write_text('# Weekly\n', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-PATCH-MENTION',
            'title': '进展提到文件',
            'state': 'Doing',
            'updatedAt': '2026-05-31T03:30:00Z',
            'progress_log': [{
                'at': '2026-05-31T03:30:00Z',
                'agent': 'libu',
                'text': '刷新版周报已保存到 outputs/weekly-report.md',
                'todos': [],
                'state': 'Doing',
                'org': '礼部',
            }],
        }
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    session = srv.get_task_coding_session('JJC-PATCH-MENTION')
    created = srv.create_patch_review('JJC-PATCH-MENTION')

    assert session['ok'] is True
    assert any(f['path'] == 'outputs/weekly-report.md' and f['changes'] > 0 for f in session['files'])
    assert created['ok'] is True
    assert created['review']['paths'] == ['outputs/weekly-report.md']
    assert '+# Weekly' in created['review']['diffPreview']


def test_patch_review_uses_task_dedicated_worktree_for_diff_checkpoint_and_reject(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    data = tmp_path / 'data'
    src = root / 'src'
    worktree = tmp_path / 'task-worktree'
    data.mkdir(parents=True)
    src.mkdir(parents=True)
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    main_file = src / 'app.py'
    main_file.write_text('print("one")\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'src/app.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(
        ['git', 'worktree', 'add', '-b', 'edict/JJC-WT-PATCH', str(worktree), 'HEAD'],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    worktree_file = worktree / 'src' / 'app.py'
    worktree_file.write_text('print("two")\n', encoding='utf-8')
    data.joinpath('tasks_source.json').write_text(json.dumps([
        {
            'id': 'JJC-WT-PATCH',
            'title': '专属 worktree Patch',
            'state': 'Doing',
            'traceId': 'trc_worktree_patch',
            'runSpec': {
                'executionIsolation': {
                    'mode': 'dedicated_worktree',
                    'targetMode': 'dedicated_worktree',
                    'status': 'active',
                    'patchFirst': True,
                    'requiresPatchReview': True,
                    'worktreePath': str(worktree),
                    'worktreeBranch': 'edict/JJC-WT-PATCH',
                },
            },
        }
    ], ensure_ascii=False), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    pre_session = srv.get_task_coding_session('JJC-WT-PATCH')
    created = srv.create_patch_review('JJC-WT-PATCH', ['src/app.py'])
    assert created['ok'] is True
    review = created['review']
    session = srv.get_task_coding_session('JJC-WT-PATCH')
    rejected = srv.handle_patch_review_action(review['id'], 'reject', '回滚任务 worktree')

    assert pathlib.Path(review['worktreePath']).resolve() == worktree.resolve()
    assert review['worktreeBranch'] == 'edict/JJC-WT-PATCH'
    assert '+print("two")' in review['diffPreview']
    assert main_file.read_text(encoding='utf-8') == 'print("one")\n'
    assert pre_session['isolationHealth']['status'] == 'warn'
    assert pre_session['isolationHealth']['label'] == '变更未生成 Patch 审批'
    assert session['ok'] is True
    assert session['isolationHealth']['status'] == 'warn'
    assert session['isolationHealth']['label'] == 'Patch 待审'
    assert session['isolationHealth']['pendingPatchCount'] == 1
    assert session['isolationHealth']['worktreeReady'] is True
    assert pathlib.Path(session['checkpoint']['root']).resolve() == worktree.resolve()
    assert session['checkpoint']['dirty'] is True
    assert any(item['path'] == 'src/app.py' for item in session['checkpoint']['files'])
    assert rejected['ok'] is True
    assert rejected['review']['status'] == 'rejected'
    assert pathlib.Path(rejected['review']['worktreePath']).resolve() == worktree.resolve()
    assert worktree_file.read_text(encoding='utf-8') == 'print("one")\n'
    assert main_file.read_text(encoding='utf-8') == 'print("one")\n'


def test_coding_session_merges_opencode_session_tool_events(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    outputs = root / 'outputs'
    oc_home = tmp_path / 'opencode'
    data.mkdir(parents=True)
    outputs.mkdir(parents=True)
    report = outputs / 'weekly-report.md'
    report.write_text('# Old\n', encoding='utf-8')
    tasks_file = data / 'tasks_source.json'
    tasks_file.write_text(json.dumps([
        {
            'id': 'JJC-OC-1',
            'title': 'OpenCode 工具事件',
            'state': 'Doing',
            'org': '户部',
            'updatedAt': '2026-05-31T04:00:00Z',
            'traceId': 'trc_oc',
        }
    ], ensure_ascii=False), encoding='utf-8')

    session_id = 'ses_oc1'
    user_msg = 'msg_oc_user'
    assistant_msg = 'msg_oc_assistant'
    session_dir = oc_home / 'storage' / 'session' / 'global'
    message_dir = oc_home / 'storage' / 'message' / session_id
    part_dir = oc_home / 'storage' / 'part' / assistant_msg
    session_dir.mkdir(parents=True)
    message_dir.mkdir(parents=True)
    part_dir.mkdir(parents=True)
    session_dir.joinpath(f'{session_id}.json').write_text(json.dumps({
        'id': session_id,
        'directory': str(root),
        'title': 'dispatch session',
        'time': {'created': 1780192800000, 'updated': 1780192810000},
    }, ensure_ascii=False), encoding='utf-8')
    message_dir.joinpath(f'{user_msg}.json').write_text(json.dumps({
        'id': user_msg,
        'sessionID': session_id,
        'role': 'user',
        'agent': 'hubu',
        'time': {'created': 1780192800000},
        'summary': {'title': 'JJC-OC-1 run'},
    }, ensure_ascii=False), encoding='utf-8')
    message_dir.joinpath(f'{assistant_msg}.json').write_text(json.dumps({
        'id': assistant_msg,
        'sessionID': session_id,
        'role': 'assistant',
        'agent': 'hubu',
        'time': {'created': 1780192801000},
        'summary': {'title': '读取、修改并测试'},
    }, ensure_ascii=False), encoding='utf-8')
    part_dir.joinpath('prt_001.json').write_text(json.dumps({
        'id': 'prt_001',
        'sessionID': session_id,
        'messageID': assistant_msg,
        'type': 'tool',
        'callID': 'call_read',
        'tool': 'read',
        'state': {
            'status': 'completed',
            'input': {'filePath': str(tasks_file), 'limit': 5},
            'output': '<file>tasks</file>',
            'time': {'start': 1780192801000, 'end': 1780192801100},
        },
    }, ensure_ascii=False), encoding='utf-8')
    part_dir.joinpath('prt_002.json').write_text(json.dumps({
        'id': 'prt_002',
        'sessionID': session_id,
        'messageID': assistant_msg,
        'type': 'tool',
        'callID': 'call_edit',
        'tool': 'edit',
        'state': {
            'status': 'completed',
            'input': {'filePath': str(report), 'oldString': '# Old', 'newString': '# New'},
            'output': 'Edit applied successfully.',
            'time': {'start': 1780192802000, 'end': 1780192802100},
        },
    }, ensure_ascii=False), encoding='utf-8')
    part_dir.joinpath('prt_003.json').write_text(json.dumps({
        'id': 'prt_003',
        'sessionID': session_id,
        'messageID': assistant_msg,
        'type': 'tool',
        'callID': 'call_bash',
        'tool': 'bash',
        'state': {
            'status': 'completed',
            'input': {'command': 'pytest -q tests/test_server.py'},
            'output': '1 passed',
            'metadata': {'exit': 0},
            'time': {'start': 1780192803000, 'end': 1780192803100},
        },
    }, ensure_ascii=False), encoding='utf-8')

    events = [{
        'kind': 'dispatch_succeeded',
        'taskId': 'JJC-OC-1',
        'traceId': 'trc_oc',
        'sessionId': session_id,
        'agentId': 'hubu',
        'at': '2026-05-31T04:00:03Z',
        'payload': {'sessionId': session_id},
    }]

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'OPENCODE_HOME', oc_home)
    monkeypatch.setattr(srv, '_ledger_list_events', lambda task_id='', limit=200: events if task_id == 'JJC-OC-1' else [])
    monkeypatch.setattr(srv, '_ledger_event_to_activity_entries', lambda event: [])

    session = srv.get_task_coding_session('JJC-OC-1')

    assert session['ok'] is True
    assert session['traceId'] == 'trc_oc'
    assert session['sessionId'] == session_id
    assert session['runtimeSession']['sessionId'] == session_id
    assert session['runtimeSession']['traceId'] == 'trc_oc'
    assert session['runtimeSession']['status'] == 'bound'
    files = {f['path']: f for f in session['files']}
    assert files['data/tasks_source.json']['reads'] == 1
    assert files['outputs/weekly-report.md']['changes'] == 1
    assert any(e['kind'] == 'test.run' and 'pytest -q' in e['command'] for e in session['events'])
    assert any(e['kind'] == 'test.result' and e['status'] == 'pass' for e in session['tests'])
    assert any(e['source'] == 'opencode-storage' for e in session['events'])


def test_coding_session_merges_opencode_sqlite_tool_events(tmp_path, monkeypatch):
    import sqlite3
    import server as srv

    root = tmp_path / 'repo'
    data = root / 'data'
    outputs = root / 'outputs'
    oc_home = tmp_path / 'opencode'
    data.mkdir(parents=True)
    outputs.mkdir(parents=True)
    oc_home.mkdir(parents=True)
    report = outputs / 'weekly-report-db.md'
    report.write_text('# DB\n', encoding='utf-8')
    tasks_file = data / 'tasks_source.json'
    tasks_file.write_text(json.dumps([
        {
            'id': 'JJC-OC-DB',
            'title': 'OpenCode DB 工具事件',
            'state': 'Doing',
            'org': '户部',
            'updatedAt': '2026-05-31T04:10:00Z',
            'traceId': 'trc_oc_db',
        }
    ], ensure_ascii=False), encoding='utf-8')

    session_id = 'ses_ocdb1'
    msg_id = 'msg_ocdb_assistant'
    db_path = oc_home / 'opencode.db'
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("""
            CREATE TABLE project (id text PRIMARY KEY, worktree text, name text, time_updated integer);
            CREATE TABLE session (
                id text PRIMARY KEY,
                project_id text,
                directory text,
                path text,
                title text,
                agent text,
                model text,
                time_created integer,
                time_updated integer
            );
            CREATE TABLE message (
                id text PRIMARY KEY,
                session_id text,
                time_created integer,
                time_updated integer,
                data text
            );
            CREATE TABLE part (
                id text PRIMARY KEY,
                message_id text,
                session_id text,
                time_created integer,
                time_updated integer,
                data text
            );
        """)
        conn.execute(
            "INSERT INTO project (id, worktree, name, time_updated) VALUES (?, ?, ?, ?)",
            ('proj1', str(root), 'repo', 1780193405000),
        )
        conn.execute(
            "INSERT INTO session (id, project_id, directory, path, title, agent, model, time_created, time_updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, 'proj1', str(root), str(root), 'JJC-OC-DB dispatch', 'hubu', '{"id":"m"}', 1780193400000, 1780193405000),
        )
        conn.execute(
            "INSERT INTO message (id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?)",
            (msg_id, session_id, 1780193401000, 1780193405000, json.dumps({
                'role': 'assistant',
                'agent': 'hubu',
                'time': {'created': 1780193401000},
            }, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            ('prt_db_read', msg_id, session_id, 1780193401000, 1780193401100, json.dumps({
                'type': 'tool',
                'tool': 'read',
                'callID': 'call_read',
                'state': {
                    'status': 'completed',
                    'input': {'filePath': str(tasks_file), 'limit': 3},
                    'output': 'tasks',
                    'time': {'start': 1780193401000, 'end': 1780193401100},
                },
            }, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            ('prt_db_bash', msg_id, session_id, 1780193402000, 1780193402100, json.dumps({
                'type': 'tool',
                'tool': 'bash',
                'callID': 'call_bash',
                'state': {
                    'status': 'completed',
                    'input': {'command': 'pytest -q tests/test_server.py'},
                    'output': '2 passed',
                    'metadata': {'exit': 0},
                    'time': {'start': 1780193402000, 'end': 1780193402100},
                },
            }, ensure_ascii=False)),
        )
        conn.execute(
            "INSERT INTO part (id, message_id, session_id, time_created, time_updated, data) VALUES (?, ?, ?, ?, ?, ?)",
            ('prt_db_patch', msg_id, session_id, 1780193403000, 1780193403100, json.dumps({
                'type': 'patch',
                'hash': 'abc123',
                'files': [str(report)],
            }, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()

    events = [{
        'kind': 'dispatch_succeeded',
        'taskId': 'JJC-OC-DB',
        'traceId': 'trc_oc_db',
        'sessionId': session_id,
        'agentId': 'hubu',
        'at': '2026-05-31T04:10:03Z',
        'payload': {'sessionId': session_id},
    }]

    monkeypatch.setenv('EDICT_RUNTIME', 'opencode')
    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'OPENCODE_HOME', oc_home)
    monkeypatch.setattr(srv, '_ledger_list_events', lambda task_id='', limit=200: events if task_id == 'JJC-OC-DB' else [])
    monkeypatch.setattr(srv, '_ledger_event_to_activity_entries', lambda event: [])

    session = srv.get_task_coding_session('JJC-OC-DB')

    assert session['ok'] is True
    assert session['traceId'] == 'trc_oc_db'
    assert session['sessionId'] == session_id
    assert session['runtimeSession']['sessionId'] == session_id
    files = {f['path']: f for f in session['files']}
    assert files['data/tasks_source.json']['reads'] == 1
    assert files['outputs/weekly-report-db.md']['changes'] == 1
    assert any(e['kind'] == 'test.run' and 'pytest -q' in e['command'] for e in session['events'])
    assert any(e['kind'] == 'test.result' and e['status'] == 'pass' for e in session['tests'])
    assert any(e['source'] == 'opencode-storage' for e in session['events'])


def test_coding_session_summarizes_task_execution(tmp_path, monkeypatch):
    import server as srv

    root = tmp_path / 'repo'
    docs = root / 'docs'
    scripts = root / 'scripts'
    data = root / 'data'
    docs.mkdir(parents=True)
    scripts.mkdir(parents=True)
    data.mkdir(parents=True)
    output = docs / 'session-report.md'
    output.write_text('# Session Report\n', encoding='utf-8')
    source = scripts / 'worker.py'
    source.write_text('def run():\n    return 42\n', encoding='utf-8')
    task = {
        'id': 'JJC-SESSION-1',
        'title': '执行驾驶舱验证',
        'state': 'Done',
        'org': '兵部',
        'now': '完成验证',
        'output': str(output),
        'updatedAt': '2026-05-31T10:00:00Z',
        'todos': [
            {'id': '1', 'title': '读取文件', 'status': 'completed'},
            {'id': '2', 'title': '运行测试', 'status': 'completed'},
        ],
        'flow_log': [
            {'at': '2026-05-31T09:00:00Z', 'from': '皇上', 'to': '太子', 'remark': '下旨'},
        ],
        'runSpec': {
            'mode': 'execute',
            'riskLevel': 'high',
            'runKind': 'system',
            'targetDept': '兵部',
            'requiredCapabilities': ['governance.plan', 'runtime.opencode', 'shell.command', 'artifact.outputs'],
            'governance': [
                {'stage': 'intake', 'dept': '太子', 'label': '意图分拣'},
                {'stage': 'plan', 'dept': '中书省', 'label': '生成 RunSpec'},
                {'stage': 'approval', 'dept': '皇上', 'label': '人工确认'},
            ],
            'toolPolicy': {
                'permissions': ['agent.run', 'shell.execute'],
                'requiresApproval': True,
                'approvalReason': 'shell.execute 需要确认',
            },
            'policyGate': {
                'decision': 'hold_for_policy',
                'status': 'waiting_policy_approval',
                'reason': 'shell.execute 需要确认',
                'requiresApproval': True,
            },
            'executionIsolation': {
                'mode': 'patch_first_shared_worktree',
                'targetMode': 'dedicated_worktree',
                'label': 'Patch-first 隔离',
                'required': True,
            },
        },
        'progress_log': [
            {
                'at': '2026-05-31T09:10:00Z',
                'agent': 'bingbu',
                'text': '正在读取文件并运行测试',
                'todos': [
                    {'id': '1', 'title': '读取文件', 'status': 'completed'},
                    {'id': '2', 'title': '运行测试', 'status': 'in-progress'},
                ],
            }
        ],
    }
    data.joinpath('tasks_source.json').write_text(json.dumps([task]), encoding='utf-8')

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    monkeypatch.setattr(srv, 'DIST', root / 'dashboard' / 'dist')
    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'get_agent_activity_by_keywords', lambda *args, **kwargs: [
        {
            'at': '2026-05-31T09:20:00Z',
            'kind': 'assistant',
            'agent': 'bingbu',
            'tools': [
                {
                    'name': 'read',
                    'input': {'filePath': 'scripts/worker.py', 'startLine': 1, 'endLine': 2},
                    'input_preview': '{"filePath":"scripts/worker.py","startLine":1,"endLine":2}',
                },
                {'name': 'bash', 'input': {'command': 'pytest -q'}, 'input_preview': 'pytest -q'},
            ],
        },
        {
            'at': '2026-05-31T09:21:00Z',
            'kind': 'tool_result',
            'agent': 'bingbu',
            'tool': 'bash',
            'exitCode': 0,
            'output': '2 passed',
        },
    ])

    session = srv.get_task_coding_session('JJC-SESSION-1')

    assert session['ok'] is True
    assert session['summary']['todoDone'] == 2
    assert session['summary']['testCount'] >= 2
    assert session['summary']['outputCount'] == 1
    assert session['outputs'][0]['path'] == 'docs/session-report.md'
    assert any(event['kind'] == 'test.run' for event in session['events'])
    read_event = next(event for event in session['events'] if event['kind'] == 'file.read')
    assert read_event['startLine'] == 1
    assert read_event['endLine'] == 2
    assert read_event['sourceUrl'].startswith('/api/source-file?')
    source_ref = next(item for item in session['files'] if item['path'] == 'scripts/worker.py')
    assert source_ref['lastStartLine'] == 1
    assert source_ref['lastEndLine'] == 2
    assert session['runSpec']['runGraph']['status'] == 'waiting_policy'
    assert session['runSpec']['runGraph']['summary']['blockedByPolicy'] is True
    assert any(node['id'] == 'policy.gate' for node in session['runSpec']['runGraph']['nodes'])


def test_capability_registry_defaults_when_file_missing(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setattr(srv, 'DATA', data)

    result = srv.list_capabilities()

    assert result['ok'] is True
    ids = {item['id'] for item in result['capabilities']}
    assert 'runtime.opencode' in ids
    assert 'governance.plan' in ids
    assert any(cat['id'] == 'browser' for cat in result['categories'])
    shell = next(item for item in result['capabilities'] if item['id'] == 'shell.command')
    assert 'shell.execute' in shell['permissions']
    assert shell['requiresApproval'] is True
    assert shell['availability']['status'] == 'ready'
    opencode = next(item for item in result['capabilities'] if item['id'] == 'runtime.opencode')
    assert opencode['availability']['status'] in {'ready', 'configured', 'missing'}


def test_preview_run_spec_includes_tool_policy(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setattr(srv, 'DATA', data)

    result = srv.preview_run_spec({
        'goal': '运行 pytest 检查当前仓库并修复前端构建问题',
        'mode': 'execute',
        'deliverable': '补丁和验证结果',
    })

    assert result['ok'] is True
    run = result['run']
    assert 'shell.command' in run['requiredCapabilities']
    assert 'shell.execute' in run['toolPolicy']['permissions']
    assert run['toolPolicy']['requiresApproval'] is True
    assert run['policyGate']['decision'] == 'hold_for_policy'
    assert run['policyGate']['status'] == 'waiting_policy_approval'
    assert run['executionIsolation']['mode'] == 'patch_first_shared_worktree'
    assert run['executionIsolation']['targetMode'] == 'dedicated_worktree'
    assert run['executionIsolation']['requiresPatchReview'] is True
    assert any(item['id'] == 'shell.command' for item in run['capabilityPolicies'])
    graph = run['runGraph']
    assert graph['version'] == 'run-graph-v1'
    assert graph['status'] == 'waiting_policy'
    assert graph['summary']['blockedByPolicy'] is True
    assert graph['summary']['runtime'] == 'runtime.opencode'
    node_ids = {node['id'] for node in graph['nodes']}
    assert 'policy.gate' in node_ids
    assert 'control.wait' in node_ids
    assert 'isolation.prepare' in node_ids
    assert 'capability.shell.command' in node_ids


def test_allocate_task_worktree_creates_dedicated_git_worktree(tmp_path, monkeypatch):
    if not shutil.which('git'):
        pytest.skip('git not available')
    import server as srv

    root = tmp_path / 'repo'
    root.mkdir()
    subprocess.run(['git', 'init'], cwd=root, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=root, check=True)
    root.joinpath('app.py').write_text('print("hello")\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'app.py'], cwd=root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=root, check=True, capture_output=True, text=True)

    monkeypatch.setattr(srv, 'PROJECT_ROOT', root)
    isolation = srv._execution_isolation_for_run(['code.workspace'], 'medium', 'coding', 'execute')

    allocated = srv._allocate_task_worktree('JJC-ISO-1', isolation)

    worktree_path = pathlib.Path(allocated['worktreePath'])
    assert allocated['mode'] == 'dedicated_worktree'
    assert allocated['previousMode'] == 'patch_first_shared_worktree'
    assert allocated['status'] == 'active'
    assert allocated['worktreeBranch'] == 'edict/JJC-ISO-1'
    assert allocated['requiresPatchReview'] is True
    assert worktree_path.exists()
    assert worktree_path.joinpath('.git').exists()
    assert worktree_path.joinpath('app.py').read_text(encoding='utf-8') == 'print("hello")\n'


def test_create_run_spec_creates_task_and_persists_mapping(tmp_path, monkeypatch):
    import event_log
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '检查当前仓库的前端构建并修复 OpenCode 模型配置入口',
        'mode': 'execute',
        'deliverable': '补丁和验证结果',
    })

    assert result['ok'] is True
    assert result['taskId'].startswith('JJC-')
    assert result['run']['id'].startswith('RUN-')
    assert result['run']['mode'] == 'execute'
    assert result['run']['requestedMode'] == 'execute'
    assert result['run']['intent']['reason'] == '用户手动指定'
    assert result['run']['deliverable'] == '补丁和验证结果'
    assert result['run']['profile']['deliverable']['source'] == 'user'
    assert 'code.workspace' in result['run']['requiredCapabilities']
    assert result['run']['targetDept'] == '兵部'

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    assert tasks[0]['templateId'] == 'agent-control-plane'
    assert tasks[0]['templateParams']['runId'] == result['run']['id']
    assert tasks[0]['runSpecId'] == result['run']['id']
    assert tasks[0]['runSpec']['executionIsolation']['requiresPatchReview'] is True

    specs = json.loads(data.joinpath('run_specs.json').read_text(encoding='utf-8'))
    assert specs[0]['taskId'] == result['taskId']
    assert specs[0]['deliverable'] == '补丁和验证结果'
    assert specs[0]['executionIsolation']['mode'] == 'patch_first_shared_worktree'

    events = event_log.list_events(task_id=result['taskId'])
    event_kinds = [event['kind'] for event in events]
    assert event_kinds[:4] == [
        'task_created',
        'user_instruction_received',
        'intent_profile_resolved',
        'run.spec.created',
    ]
    assert {event['traceId'] for event in events} == {tasks[0]['traceId']}
    assert events[1]['payload']['goal'].startswith('检查当前仓库')
    assert events[2]['payload']['targetDept'] == '兵部'
    assert events[3]['payload']['runId'] == result['run']['id']


def test_auto_run_spec_infers_plan_and_holds_for_review_without_dispatch(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    dispatches = []

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatches.append(args))
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '先给出当前平台 Agent Control Plane 的改造方案，不要直接修改文件',
        'mode': 'auto',
        'deliverable': '改造方案',
    })

    assert result['ok'] is True
    assert result['run']['requestedMode'] == 'auto'
    assert result['run']['mode'] == 'plan'
    assert result['run']['intent']['reason']
    assert result['run']['status'] == 'waiting_review'
    assert result['run']['governance'][-1]['stage'] == 'hold'
    assert dispatches == []

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    task = tasks[0]
    assert task['state'] == 'Menxia'
    assert task['_scheduler']['lastDispatchStatus'] == 'held'
    assert task['templateParams']['requestedMode'] == 'auto'
    assert task['runSpec']['mode'] == 'plan'
    assert task['runSpec']['intentReason']


def test_auto_run_spec_infers_execute_and_dispatches(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    dispatches = []

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatches.append((args, kwargs)))
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '整理当前任务看板输出摘要并生成一份归档报告',
        'mode': 'auto',
        'deliverable': '报告和结果摘要',
    })

    assert result['ok'] is True
    assert result['run']['requestedMode'] == 'auto'
    assert result['run']['mode'] == 'execute'
    assert result['run']['intent']['reason'] == '目标更像要完成具体动作'
    assert result['run']['status'] == 'created'
    assert result['run']['policyGate']['decision'] == 'auto_dispatch'
    assert dispatches

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    assert tasks[0]['state'] == 'Taizi'
    assert tasks[0]['templateParams']['mode'] == 'execute'
    assert tasks[0]['templateParams']['requestedMode'] == 'auto'


def test_shell_run_spec_requires_policy_approval_without_dispatch(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    dispatches = []

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatches.append((args, kwargs)))
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '运行 pytest 检查当前仓库并修复失败测试',
        'mode': 'execute',
        'deliverable': '补丁和验证结果',
    })

    assert result['ok'] is True
    run = result['run']
    assert run['status'] == 'waiting_policy_approval'
    assert run['policyGate']['decision'] == 'hold_for_policy'
    assert run['policyGate']['requiresApproval'] is True
    assert 'shell.execute' in run['toolPolicy']['permissions']
    assert dispatches == []

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    task = tasks[0]
    assert task['state'] == 'Menxia'
    assert task['org'] == '门下省'
    assert task['now'].startswith('Policy Gate')
    assert task['_scheduler']['lastDispatchStatus'] == 'held'
    assert task['_scheduler']['lastDispatchTrigger'] == 'policy-gate'
    assert task['_scheduler']['policyGateDecision'] == 'hold_for_policy'
    assert task['runSpec']['policyGate']['status'] == 'waiting_policy_approval'
    assert task['runSpec']['toolPolicy']['requiresApproval'] is True
    assert task['runSpec']['runGraph']['status'] == 'waiting_policy'
    assert task['runSpec']['runGraph']['summary']['blockedByPolicy'] is True


def test_auto_run_spec_infers_profile_fields_when_omitted(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: None)
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '加急修复当前仓库的前端构建失败并验证结果',
        'mode': 'auto',
    })

    assert result['ok'] is True
    assert result['run']['priority'] == 'high'
    assert result['run']['requestedPriority'] == 'auto'
    assert '代码补丁' in result['run']['deliverable']
    assert '高风险步骤' in result['run']['constraints']
    assert result['run']['profile']['priority']['source'] == 'inferred'
    assert result['run']['profile']['constraints']['source'] == 'inferred'

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    assert tasks[0]['priority'] == 'high'
    assert tasks[0]['templateParams']['profile']['priority']['value'] == 'high'


def test_preview_run_spec_has_no_task_side_effects(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    tasks_path = data / 'tasks_source.json'
    tasks_path.write_text('[]', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    result = srv.preview_run_spec({
        'goal': '加急修复当前仓库的前端构建失败并验证结果',
        'mode': 'auto',
    })

    assert result['ok'] is True
    assert result['run']['id'] == 'RUN-PREVIEW'
    assert result['run']['taskId'] == ''
    assert result['run']['status'] == 'preview'
    assert result['run']['mode'] == 'execute'
    assert result['run']['priority'] == 'high'
    assert '代码补丁' in result['run']['deliverable']
    assert json.loads(tasks_path.read_text(encoding='utf-8')) == []
    assert not data.joinpath('run_specs.json').exists()


def test_preview_run_spec_flags_vague_goal_with_one_question(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)

    result = srv.preview_run_spec({
        'goal': '帮我优化一下这个东西',
        'mode': 'auto',
    })

    assert result['ok'] is True
    clarification = result['run']['clarification']
    assert clarification['shouldAsk'] is True
    assert clarification['level'] in {'ambiguous', 'needs_detail'}
    assert clarification['primaryQuestion']
    assert clarification['safetyMode'] == 'interactive'
    assert result['run']['mode'] == 'interactive'
    assert result['run']['governance'][2]['stage'] == 'clarify'
    assert clarification['quickAdds']
    assert len(clarification['questions']) <= 2
    assert '缺少对象' in clarification['missing'] or '指代不清' in clarification['missing']
    assert result['run']['profile']['clarification']['shouldAsk'] is True


def test_auto_vague_run_spec_holds_for_clarification_without_dispatch(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    dispatches = []

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatches.append(args))
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '帮我优化一下这个东西',
        'mode': 'auto',
    })

    assert result['ok'] is True
    assert result['run']['mode'] == 'interactive'
    assert result['run']['status'] == 'waiting_clarification'
    assert result['run']['clarification']['shouldAsk'] is True
    assert dispatches == []

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    task = tasks[0]
    assert task['state'] == 'Menxia'
    assert task['_scheduler']['lastDispatchStatus'] == 'held'
    assert task['_scheduler']['lastDispatchTrigger'] == 'interaction-first'
    assert '等待最小补充' in task['now']


def test_plan_run_spec_holds_for_review_without_dispatch(tmp_path, monkeypatch):
    import server as srv

    data = tmp_path / 'data'
    data.mkdir()
    data.joinpath('tasks_source.json').write_text('[]', encoding='utf-8')
    dispatches = []

    monkeypatch.setattr(srv, 'DATA', data)
    monkeypatch.setattr(srv, '_ACTIVE_TASK_DATA_DIR', data)
    monkeypatch.setattr(srv, 'dispatch_for_state', lambda *args, **kwargs: dispatches.append(args))
    monkeypatch.setattr(srv, '_trigger_refresh', lambda: None)

    result = srv.create_run_spec({
        'goal': '先给出当前平台 Agent Control Plane 的改造方案，不要直接修改文件',
        'mode': 'plan',
        'deliverable': '改造方案',
    })

    assert result['ok'] is True
    assert result['run']['status'] == 'waiting_review'
    assert result['run']['governance'][-1]['stage'] == 'hold'
    assert dispatches == []

    tasks = json.loads(data.joinpath('tasks_source.json').read_text(encoding='utf-8'))
    task = tasks[0]
    assert task['state'] == 'Menxia'
    assert task['org'] == '门下省'
    assert task['_scheduler']['lastDispatchStatus'] == 'held'
    assert task['_scheduler']['lastDispatchTrigger'] == 'plan-first'
    assert task['runSpec']['mode'] == 'plan'
    assert '暂不自动交办执行' in task['flow_log'][-1]['remark']

    outbox_path = data / 'runtime_outbox.json'
    assert not outbox_path.exists() or json.loads(outbox_path.read_text(encoding='utf-8')) == []
