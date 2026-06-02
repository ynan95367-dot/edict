#!/usr/bin/env python3
"""
三省六部 · 看板本地 API 服务器
Port: 7891 (可通过 --port 修改)

Endpoints:
  GET  /                       → dashboard.html
  GET  /api/live-status        → data/live_status.json
  GET  /api/agent-config       → data/agent_config.json
  POST /api/set-model          → {agentId, model}
  GET  /api/model-change-log   → data/model_change_log.json
  GET  /api/last-result        → data/last_model_change_result.json
"""
import json, pathlib, subprocess, sys, threading, argparse, datetime, logging, re, os, socket, shutil, uuid, shlex, sqlite3, signal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, urlencode, parse_qs, quote
from urllib.request import Request, urlopen

# JWT 认证模块
from auth import init as auth_init, requires_auth, extract_token, verify_token, \
    is_enabled as auth_enabled, is_configured as auth_configured, \
    setup_password, verify_password, create_token

# 引入文件锁工具，确保与其他脚本并发安全
scripts_dir = str(pathlib.Path(__file__).parent.parent / 'scripts')
sys.path.insert(0, scripts_dir)
from file_lock import atomic_json_read, atomic_json_write, atomic_json_update
from utils import validate_url, read_json, now_iso, python_bin
import runtime_outbox as _runtime_outbox
from runtime_outbox import (
    enqueue_dispatch as _outbox_enqueue_dispatch,
    list_outbox as _outbox_list,
    claim_pending as _outbox_claim_pending,
    mark_done as _outbox_mark_done,
    mark_failed as _outbox_mark_failed,
    requeue_failed as _outbox_requeue_failed,
    archive_failed as _outbox_archive_failed,
    requeue_orphaned_running as _outbox_requeue_orphaned_running,
    compact_unfinished_duplicates as _outbox_compact_unfinished_duplicates,
    task_summary as _outbox_task_summary,
)
from court_discuss import (
    create_session as cd_create, advance_discussion as cd_advance,
    get_session as cd_get, conclude_session as cd_conclude,
    list_sessions as cd_list, destroy_session as cd_destroy,
    get_fate_event as cd_fate, OFFICIAL_PROFILES as CD_PROFILES,
)
try:
    from event_log import (
        append_event as _ledger_append_event,
        list_events as _ledger_list_events,
        event_to_activity_entries as _ledger_event_to_activity_entries,
    )
except Exception:  # pragma: no cover - dashboard can run without the ledger module
    _ledger_append_event = None
    _ledger_list_events = None
    _ledger_event_to_activity_entries = None

log = logging.getLogger('server')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s', datefmt='%H:%M:%S')

CHANNELS_DIR = pathlib.Path(__file__).parent.parent / 'edict' / 'backend' / 'app' / 'channels'
if str(CHANNELS_DIR.parent) not in sys.path:
    sys.path.insert(0, str(CHANNELS_DIR.parent))
from channels import get_channel, get_channel_info, CHANNELS as NOTIFICATION_CHANNELS

OCLAW_HOME = pathlib.Path.home() / '.openclaw'
OPENCODE_HOME = pathlib.Path(os.environ.get('OPENCODE_HOME', str(pathlib.Path.home() / '.local' / 'share' / 'opencode'))).expanduser()
MAX_REQUEST_BODY = 1 * 1024 * 1024  # 1 MB
ALLOWED_ORIGIN = None  # Set via --cors; None means restrict to localhost
_DASHBOARD_PORT = 7891  # Updated at startup from --port arg
_DEFAULT_ORIGINS = {
    'http://127.0.0.1:7891', 'http://localhost:7891',
    'http://127.0.0.1:5173', 'http://localhost:5173',  # Vite dev server
}
_SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9_\-\u4e00-\u9fff]+$')

BASE = pathlib.Path(__file__).parent
PROJECT_ROOT = BASE.parent
DIST = BASE / 'dist'          # React 构建产物 (npm run build)
DATA = BASE.parent / "data"
SCRIPTS = BASE.parent / 'scripts'
_ACTIVE_TASK_DATA_DIR = None
_REAL_THREAD = threading.Thread
_REFRESH_GENERATION = 0
_REFRESH_TIMER_LOCK = threading.Lock()
_REFRESH_DEBOUNCE_SEC = 1.5
_DISPATCH_WORKER_LOCK = threading.Lock()
_DISPATCH_WORKER_ACTIVE = False
_DISPATCH_WORKER_ID = f'dashboard-{os.getpid()}'
_ANSI_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def _append_runtime_event(kind, task_id='', agent_id='', payload=None, evidence=None, confidence='high', at=None, trace_id='', session_id=''):
    """Best-effort append to the task/agent event ledger."""
    if _ledger_append_event is None:
        return None
    try:
        return _ledger_append_event(
            kind,
            task_id=task_id,
            trace_id=trace_id,
            agent_id=agent_id,
            session_id=session_id,
            runtime=_agent_runtime() if '_agent_runtime' in globals() else os.environ.get('EDICT_RUNTIME', ''),
            source='dashboard',
            payload=payload or {},
            evidence=evidence or {},
            confidence=confidence,
            at=at,
        )
    except Exception as exc:
        log.debug(f'event ledger append failed ({kind}/{task_id}): {exc}')
        return None


def _ensure_trace_id(task):
    """Ensure JSON-mode tasks have a stable trace id for UI aggregation."""
    if not isinstance(task, dict):
        return ''
    trace_id = task.get('traceId') or task.get('trace_id') or ''
    if not trace_id:
        trace_id = f'trc_{uuid.uuid4().hex[:16]}'
    task['traceId'] = trace_id
    return trace_id

# 静态资源 MIME 类型
_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js':   'application/javascript; charset=utf-8',
    '.css':  'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.md':   'text/markdown; charset=utf-8',
    '.txt':  'text/plain; charset=utf-8',
    '.log':  'text/plain; charset=utf-8',
    '.csv':  'text/csv; charset=utf-8',
    '.png':  'image/png',
    '.jpg':  'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif':  'image/gif',
    '.webp': 'image/webp',
    '.svg':  'image/svg+xml',
    '.pdf':  'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    '.mp4':  'video/mp4',
    '.ico':  'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf':  'font/ttf',
    '.map':  'application/json',
}


def cors_headers(h):
    req_origin = h.headers.get('Origin', '')
    if ALLOWED_ORIGIN:
        origin = ALLOWED_ORIGIN
    elif req_origin in _DEFAULT_ORIGINS:
        origin = req_origin
    else:
        origin = f'http://127.0.0.1:{_DASHBOARD_PORT}'
    h.send_header('Access-Control-Allow-Origin', origin)
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type')


def _iter_task_data_dirs():
    """返回可用的任务数据目录候选（优先 workspace，其次本地 data）。"""
    dirs = [DATA]
    for p in sorted(OCLAW_HOME.glob('workspace-*/data')):
        if p.is_dir():
            dirs.append(p)
    return dirs


def _task_source_score(task_file: pathlib.Path):
    """给任务源打分：优先非 demo 任务，其次任务数，再按文件更新时间。"""
    try:
        tasks = atomic_json_read(task_file, [])
    except Exception:
        tasks = []
    if not isinstance(tasks, list):
        tasks = []
    non_demo = sum(1 for t in tasks if str((t or {}).get('id', '')) and not str((t or {}).get('id', '')).startswith('JJC-DEMO'))
    try:
        mtime = task_file.stat().st_mtime
    except Exception:
        mtime = 0
    return (1 if non_demo > 0 else 0, non_demo, len(tasks), mtime)


def get_task_data_dir():
    """自动选择当前任务数据目录，并缓存结果以保持一次服务期内稳定。"""
    global _ACTIVE_TASK_DATA_DIR
    if _ACTIVE_TASK_DATA_DIR and _ACTIVE_TASK_DATA_DIR.is_dir():
        try:
            _runtime_outbox.OUTBOX_FILE = _ACTIVE_TASK_DATA_DIR / 'runtime_outbox.json'
        except Exception:
            pass
        return _ACTIVE_TASK_DATA_DIR
    best_dir = DATA
    best_score = (-1, -1, -1, -1)
    for d in _iter_task_data_dirs():
        tf = d / 'tasks_source.json'
        if not tf.exists():
            continue
        score = _task_source_score(tf)
        if score > best_score:
            best_score = score
            best_dir = d
    _ACTIVE_TASK_DATA_DIR = best_dir
    try:
        _runtime_outbox.OUTBOX_FILE = _ACTIVE_TASK_DATA_DIR / 'runtime_outbox.json'
    except Exception:
        pass
    log.info(f'任务数据源: {_ACTIVE_TASK_DATA_DIR}')
    return _ACTIVE_TASK_DATA_DIR


def load_tasks():
    task_data_dir = get_task_data_dir()
    return atomic_json_read(task_data_dir / 'tasks_source.json', [])


def save_tasks(tasks):
    task_data_dir = get_task_data_dir()
    atomic_json_write(task_data_dir / 'tasks_source.json', tasks)
    _trigger_refresh()


_OUTPUT_EXTS = {
    '.md', '.txt', '.log', '.json', '.csv', '.html', '.pdf',
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg',
    '.docx', '.xlsx', '.pptx', '.mp4',
}
_SOURCE_EXTS = {
    '.py', '.pyi', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.cjs',
    '.json', '.md', '.css', '.scss', '.html', '.yml', '.yaml',
    '.toml', '.ini', '.sh', '.zsh', '.bash', '.ps1', '.txt',
    '.sql', '.go', '.rs', '.java', '.kt', '.swift', '.c', '.h',
    '.cpp', '.hpp', '.cs', '.rb', '.php',
}
_MAX_SOURCE_FILE_BYTES = 1_000_000


def _output_roots():
    return [
        {'key': 'docs', 'label': '文档', 'path': PROJECT_ROOT / 'docs'},
        {'key': 'examples', 'label': '示例', 'path': PROJECT_ROOT / 'examples'},
        {'key': 'outputs', 'label': '输出', 'path': PROJECT_ROOT / 'outputs'},
        {'key': 'reports', 'label': '报告', 'path': PROJECT_ROOT / 'reports'},
        {'key': 'artifacts', 'label': '产物', 'path': PROJECT_ROOT / 'artifacts'},
        {'key': 'exports', 'label': '导出', 'path': PROJECT_ROOT / 'exports'},
        {'key': 'dist', 'label': '构建产物', 'path': DIST},
    ]


def _resolve_project_output_path(output_path: str):
    if not output_path or output_path == '-':
        return None
    try:
        path = pathlib.Path(output_path).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path = path.resolve()
    except Exception:
        return None
    if not _is_within(path, PROJECT_ROOT):
        return None
    return path


def _task_output_roots():
    roots = []
    try:
        tasks = load_tasks()
    except Exception:
        tasks = []
    for task in tasks:
        path = _resolve_project_output_path((task or {}).get('output') or '')
        if not path:
            continue
        if not path.exists():
            continue
        root = path if path.is_dir() else path.parent
        if root.exists() and root.is_dir():
            roots.append(root)
    return roots


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_project_file(rel_path: str):
    safe = (rel_path or '').replace('\\', '/').lstrip('/')
    if not safe or '\x00' in safe:
        return None
    try:
        candidate = (PROJECT_ROOT / safe).resolve()
        project_root = PROJECT_ROOT.resolve()
    except Exception:
        return None
    if not _is_within(candidate, project_root):
        return None
    allowed_roots = [root['path'] for root in _output_roots() if root['path'].exists()]
    allowed_roots.extend(_task_output_roots())
    if not any(_is_within(candidate, root) for root in allowed_roots):
        return None
    if not candidate.is_file():
        return None
    return candidate


def _safe_source_path(path_value: str, allow_missing=False):
    raw = (path_value or '').strip()
    if not raw or '\x00' in raw:
        return None
    try:
        path = pathlib.Path(raw).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / raw.lstrip('/').replace('\\', '/')
        candidate = path.resolve()
        project_root = PROJECT_ROOT.resolve()
    except Exception:
        return None
    if not _is_within(candidate, project_root):
        return None
    if set(candidate.parts).intersection({'.git', 'node_modules', '__pycache__'}):
        return None
    if candidate.suffix.lower() not in _SOURCE_EXTS:
        return None
    if candidate.exists():
        if not candidate.is_file():
            return None
        try:
            if candidate.stat().st_size > _MAX_SOURCE_FILE_BYTES:
                return None
        except OSError:
            return None
    elif not allow_missing:
        return None
    return candidate


def _safe_source_file(path_value: str):
    return _safe_source_path(path_value, allow_missing=False)


def _rel_project_path(path: pathlib.Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except Exception:
        return str(path)


def _editor_command_for_file(source: pathlib.Path, line=0):
    line_no = max(1, _as_int(line) or 1) if '_as_int' in globals() else max(1, int(line or 1))
    line_target = f'{source}:{line_no}'
    custom = (os.environ.get('EDICT_EDITOR_CMD') or os.environ.get('OPENCLAW_EDITOR_CMD') or '').strip()
    if custom:
        parts = shlex.split(custom)
        if not parts:
            return None, ''
        values = {
            'file': str(source),
            'path': str(source),
            'line': str(line_no),
            'line_arg': line_target,
        }
        if any('{' in part and '}' in part for part in parts):
            return [part.format(**values) for part in parts], '自定义编辑器'
        return parts + [line_target], '自定义编辑器'

    for bin_name, label in (
        ('code', 'VS Code'),
        ('cursor', 'Cursor'),
        ('windsurf', 'Windsurf'),
    ):
        binary = shutil.which(bin_name)
        if binary:
            return [binary, '-g', line_target], label

    zed = shutil.which('zed')
    if zed:
        return [zed, line_target], 'Zed'

    if sys.platform == 'darwin':
        opener = shutil.which('open') or '/usr/bin/open'
        return [opener, str(source)], '系统默认应用'

    return None, ''


def _editor_opener_available():
    if (os.environ.get('EDICT_EDITOR_CMD') or os.environ.get('OPENCLAW_EDITOR_CMD') or '').strip():
        return True
    if any(shutil.which(name) for name in ('code', 'cursor', 'windsurf', 'zed')):
        return True
    return sys.platform == 'darwin'


def open_source_file(path_value: str, start_line=0):
    source = _safe_source_file(path_value)
    if not source:
        return {'ok': False, 'error': 'file not found or not allowed'}
    line_no = max(1, _as_int(start_line) or 1)
    cmd, label = _editor_command_for_file(source, line_no)
    if not cmd:
        return {
            'ok': False,
            'error': '未找到可用编辑器，请安装 code/cursor/windsurf/zed，或设置 EDICT_EDITOR_CMD',
        }
    try:
        subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return {'ok': False, 'error': f'打开失败: {exc}'}
    return {
        'ok': True,
        'message': f'已请求 {label} 打开 {source.name}:{line_no}',
        'path': _rel_project_path(source),
        'absolutePath': str(source),
        'line': line_no,
        'editor': label,
    }


def _git_run(args, timeout=5):
    try:
        return subprocess.run(
            ['git', *args],
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except Exception:
        return None


def get_worktree_checkpoint(limit=40):
    root_check = _git_run(['rev-parse', '--show-toplevel'])
    if not root_check or root_check.returncode != 0:
        return {'ok': False, 'available': False, 'error': 'git worktree not available'}
    branch = _git_run(['rev-parse', '--abbrev-ref', 'HEAD'])
    head = _git_run(['rev-parse', '--short', 'HEAD'])
    status = _git_run(['status', '--porcelain=v1', '-uno'])
    untracked = _git_run(['ls-files', '--others', '--exclude-standard'])
    files = []
    staged = unstaged = 0
    if status and status.returncode == 0:
        for line in status.stdout.splitlines():
            if not line:
                continue
            x = line[0]
            y = line[1] if len(line) > 1 else ' '
            path = line[3:] if len(line) > 3 else ''
            if ' -> ' in path:
                path = path.split(' -> ', 1)[1]
            if x != ' ':
                staged += 1
            if y != ' ':
                unstaged += 1
            files.append({'path': path, 'status': f'{x}{y}'.strip() or 'modified', 'kind': 'tracked'})
    if untracked and untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            if path:
                files.append({'path': path, 'status': '??', 'kind': 'untracked'})
    file_count = len(files)
    untracked_count = sum(1 for item in files if item.get('kind') == 'untracked')
    files = files[:max(1, int(limit or 40))]
    return {
        'ok': True,
        'available': True,
        'root': root_check.stdout.strip(),
        'branch': (branch.stdout.strip() if branch and branch.returncode == 0 else ''),
        'head': (head.stdout.strip() if head and head.returncode == 0 else ''),
        'dirty': bool(files),
        'stagedCount': staged,
        'unstagedCount': unstaged,
        'untrackedCount': untracked_count,
        'fileCount': file_count,
        'files': files,
        'generatedAt': now_iso(),
    }


def _patch_reviews_file():
    return DATA / 'patch_reviews.json'


def _read_patch_reviews():
    items = atomic_json_read(_patch_reviews_file(), [])
    return items if isinstance(items, list) else []


def list_patch_reviews(task_id=''):
    reviews = []
    for item in _read_patch_reviews():
        if not isinstance(item, dict):
            continue
        if task_id and item.get('taskId') != task_id:
            continue
        reviews.append(item)
    reviews.sort(key=lambda x: x.get('createdAt', ''))
    return reviews


def _patch_review_public(item):
    diff = item.get('diff') or ''
    return {
        'id': item.get('id', ''),
        'taskId': item.get('taskId', ''),
        'status': item.get('status', 'pending'),
        'title': item.get('title', ''),
        'paths': item.get('paths') if isinstance(item.get('paths'), list) else [],
        'stats': item.get('stats') if isinstance(item.get('stats'), dict) else {},
        'createdAt': item.get('createdAt', ''),
        'updatedAt': item.get('updatedAt', ''),
        'decidedAt': item.get('decidedAt', ''),
        'decidedBy': item.get('decidedBy', ''),
        'decisionReason': item.get('decisionReason', ''),
        'lastError': item.get('lastError', ''),
        'baseHead': item.get('baseHead', ''),
        'diffPreview': diff[:12000],
        'diffSize': len(diff),
    }


def _normalize_patch_paths(paths):
    out = []
    seen = set()
    for raw in paths or []:
        if not isinstance(raw, str):
            continue
        source = _safe_source_path(raw, allow_missing=True)
        if not source:
            continue
        rel = _rel_project_path(source)
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


def _task_file_change_paths(task_id):
    paths = []
    try:
        activity = (get_task_activity(task_id) or {}).get('activity') or []
    except Exception:
        activity = []
    for entry in activity:
        if entry.get('kind') != 'assistant':
            continue
        for tool_call in entry.get('tools') or []:
            name = (tool_call.get('name') or tool_call.get('tool') or '').lower()
            if name not in {'edit', 'write', 'patch', 'multiedit', 'delete', 'remove', 'unlink', 'rm'}:
                continue
            payload = _tool_payload_from_call(tool_call)
            path = _file_path_from_payload(payload)
            if path:
                paths.append(path)
    return _normalize_patch_paths(paths)


_SOURCE_PATH_RE = re.compile(
    r'(?<![\w./-])([A-Za-z0-9_./@+\-]+'
    r'\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|css|scss|html|yml|yaml|toml|ini|sh|zsh|bash|ps1|txt|sql|go|rs|java|kt|swift|c|h|cpp|hpp|cs|rb|php))'
)


def _extract_source_paths_from_text(text):
    if not text:
        return []
    return [m.group(1) for m in _SOURCE_PATH_RE.finditer(str(text))]


def _task_text_candidates(task, activity):
    values = []
    for key in ('title', 'now', 'block', 'output'):
        value = (task or {}).get(key)
        if value:
            values.append(value)
    for item in (task or {}).get('progress_log') or []:
        values.extend([item.get('text', ''), item.get('detail', '')])
        for todo in item.get('todos') or []:
            values.extend([todo.get('title', ''), todo.get('detail', '')])
    for todo in (task or {}).get('todos') or []:
        values.extend([todo.get('title', ''), todo.get('detail', '')])
    for flow in (task or {}).get('flow_log') or []:
        values.append(flow.get('remark', ''))
    for entry in activity or []:
        for key in ('text', 'detail', 'remark', 'output', 'command', 'path'):
            value = entry.get(key)
            if value:
                values.append(value)
        for tool in entry.get('tools') or []:
            values.append(tool.get('input_preview') or tool.get('inputPreview') or '')
            raw = tool.get('input')
            if isinstance(raw, dict):
                values.append(json.dumps(raw, ensure_ascii=False))
    return values


def _git_changed_source_paths():
    status = _git_run(['status', '--porcelain=v1', '--untracked-files=all'], timeout=10)
    if not status or status.returncode != 0:
        return []
    paths = []
    seen = set()
    for line in status.stdout.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].strip()
        if ' -> ' in raw:
            raw = raw.split(' -> ', 1)[1].strip()
        if raw.startswith('"') and raw.endswith('"'):
            continue
        for rel in _normalize_patch_paths([raw]):
            if rel in seen:
                continue
            if not (_git_path_is_tracked(rel) or (_safe_source_file(rel) and _git_path_is_untracked(rel))):
                continue
            seen.add(rel)
            paths.append(rel)
    return paths


def _task_mentioned_patch_paths(task, activity=None):
    changed = set(_git_changed_source_paths())
    if not changed:
        return []
    out = []
    seen = set()
    for value in _task_text_candidates(task, activity or []):
        raw_paths = [value] if isinstance(value, str) and pathlib.Path(value).suffix else []
        raw_paths.extend(_extract_source_paths_from_text(value))
        for rel in _normalize_patch_paths(raw_paths):
            if rel not in changed or rel in seen:
                continue
            seen.add(rel)
            out.append(rel)
    return out


def _git_path_is_tracked(rel_path):
    result = _git_run(['ls-files', '--error-unmatch', '--', rel_path], timeout=5)
    return bool(result and result.returncode == 0)


def _git_path_is_untracked(rel_path):
    result = _git_run(['ls-files', '--others', '--exclude-standard', '--', rel_path], timeout=5)
    if not result or result.returncode != 0:
        return False
    return any(line.strip() == rel_path for line in result.stdout.splitlines())


def _parse_patch_numstat(stdout, status='modified'):
    files = []
    insertions = deletions = 0
    for line in (stdout or '').splitlines():
        parts = line.split('\t')
        if len(parts) < 3:
            continue
        add_raw, del_raw, path = parts[0], parts[1], parts[2]
        add = int(add_raw) if add_raw.isdigit() else 0
        dele = int(del_raw) if del_raw.isdigit() else 0
        insertions += add
        deletions += dele
        file_status = status.get(path, 'modified') if isinstance(status, dict) else status
        files.append({'path': path, 'insertions': add, 'deletions': dele, 'status': file_status})
    return files, insertions, deletions


def _patch_status_from_git(code):
    code = (code or '').upper()
    if code.startswith('A'):
        return 'added'
    if code.startswith('D'):
        return 'deleted'
    if code.startswith('R'):
        return 'renamed'
    if code.startswith('C'):
        return 'copied'
    return 'modified'


def _git_diff_status_map(paths):
    result = _git_run(['diff', '--name-status', 'HEAD', '--', *paths], timeout=10)
    out = {}
    if not result or result.returncode != 0:
        return out
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        out[parts[-1]] = _patch_status_from_git(parts[0])
    return out


def _git_diff_for_paths(paths):
    if not paths:
        return '', {}
    tracked_paths = []
    untracked_paths = []
    for rel in paths:
        if _git_path_is_tracked(rel):
            tracked_paths.append(rel)
        elif _safe_source_file(rel) and _git_path_is_untracked(rel):
            untracked_paths.append(rel)

    diff_parts = []
    files = []
    insertions = deletions = 0

    if tracked_paths:
        diff = _git_run(['diff', '--binary', 'HEAD', '--', *tracked_paths], timeout=10)
        if not diff or diff.returncode != 0:
            err = diff.stderr.strip() if diff else 'git diff failed'
            raise RuntimeError(err or 'git diff failed')
        if diff.stdout:
            diff_parts.append(diff.stdout)
        stat = _git_run(['diff', '--numstat', 'HEAD', '--', *tracked_paths], timeout=10)
        if stat and stat.returncode == 0:
            parsed, add, dele = _parse_patch_numstat(stat.stdout, _git_diff_status_map(tracked_paths))
            files.extend(parsed)
            insertions += add
            deletions += dele

    for rel in untracked_paths:
        diff = _git_run(['diff', '--binary', '--no-index', '--', '/dev/null', rel], timeout=10)
        if not diff or diff.returncode not in (0, 1):
            err = diff.stderr.strip() if diff else 'git diff failed'
            raise RuntimeError(err or f'git diff failed for {rel}')
        if diff.stdout:
            diff_parts.append(diff.stdout)
        stat = _git_run(['diff', '--numstat', '--no-index', '--', '/dev/null', rel], timeout=10)
        if stat and stat.returncode in (0, 1):
            parsed, add, dele = _parse_patch_numstat(stat.stdout, status='added')
            files.extend(parsed)
            insertions += add
            deletions += dele

    diff_text = '\n'.join(part.rstrip() for part in diff_parts if part).strip()
    if diff_text:
        diff_text += '\n'
    return diff_text, {'files': files, 'insertions': insertions, 'deletions': deletions}


def create_patch_review(task_id, paths=None):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    try:
        activity = (get_task_activity(task_id) or {}).get('activity') or []
    except Exception:
        activity = []
    selected_paths = (
        _normalize_patch_paths(paths or [])
        or _task_file_change_paths(task_id)
        or _task_mentioned_patch_paths(task, activity)
    )
    if not selected_paths:
        return {'ok': False, 'error': '未发现任务相关文件修改事件或明确提到的工作区变更，无法生成 patch 审批'}
    try:
        diff, stats = _git_diff_for_paths(selected_paths)
    except Exception as exc:
        return {'ok': False, 'error': f'生成 diff 失败: {exc}'}
    if not diff.strip():
        return {'ok': False, 'error': '这些文件没有可审批的工作区变更'}
    head = _git_run(['rev-parse', '--short', 'HEAD'])
    ts = now_iso()
    review = {
        'id': f'patch_{uuid.uuid4().hex[:16]}',
        'taskId': task_id,
        'traceId': task.get('traceId') or task.get('trace_id') or '',
        'status': 'pending',
        'title': task.get('title', '') or task_id,
        'paths': selected_paths,
        'stats': stats,
        'diff': diff,
        'baseHead': head.stdout.strip() if head and head.returncode == 0 else '',
        'createdAt': ts,
        'updatedAt': ts,
        'decidedAt': '',
        'decidedBy': '',
        'decisionReason': '',
        'lastError': '',
    }

    def _append(items):
        items = items if isinstance(items, list) else []
        items.append(review)
        return items[-500:]

    atomic_json_update(_patch_reviews_file(), _append, [])
    _append_runtime_event('patch_review_created', task_id, '', {
        'patchId': review['id'],
        'paths': selected_paths,
        'status': 'pending',
        'remark': f'生成 Patch 审批：{len(selected_paths)} 个文件',
    }, trace_id=review.get('traceId', ''))
    return {'ok': True, 'review': _patch_review_public(review)}


def handle_patch_review_action(patch_id, action, reason=''):
    if action not in {'approve', 'reject'}:
        return {'ok': False, 'error': 'action must be approve or reject'}
    reviews = _read_patch_reviews()
    review = next((x for x in reviews if isinstance(x, dict) and x.get('id') == patch_id), None)
    if not review:
        return {'ok': False, 'error': f'patch review {patch_id} 不存在'}
    if review.get('status') != 'pending':
        return {'ok': False, 'error': f'patch review 已是 {review.get("status", "unknown")}'}

    if action == 'reject':
        try:
            result = subprocess.run(
                ['git', 'apply', '--reverse', '--whitespace=nowarn', '-'],
                cwd=str(PROJECT_ROOT),
                input=review.get('diff') or '',
                text=True,
                capture_output=True,
                timeout=15,
            )
        except Exception as exc:
            return {'ok': False, 'error': f'反向应用 patch 失败: {exc}'}
        if result.returncode != 0:
            err = (result.stderr or result.stdout or 'git apply failed').strip()[:500]

            def _mark_error(items):
                items = items if isinstance(items, list) else []
                for item in items:
                    if isinstance(item, dict) and item.get('id') == patch_id:
                        item['lastError'] = err
                        item['updatedAt'] = now_iso()
                return items

            atomic_json_update(_patch_reviews_file(), _mark_error, [])
            return {'ok': False, 'error': err}

    ts = now_iso()
    public = {}

    def _decide(items):
        nonlocal public
        items = items if isinstance(items, list) else []
        for item in items:
            if not isinstance(item, dict) or item.get('id') != patch_id:
                continue
            item['status'] = 'approved' if action == 'approve' else 'rejected'
            item['updatedAt'] = ts
            item['decidedAt'] = ts
            item['decidedBy'] = 'dashboard'
            item['decisionReason'] = reason[:300] if reason else ''
            item['lastError'] = ''
            public = _patch_review_public(item)
            break
        return items

    atomic_json_update(_patch_reviews_file(), _decide, [])
    _append_runtime_event('patch_review_decided', review.get('taskId', ''), '', {
        'patchId': patch_id,
        'action': action,
        'status': public.get('status', ''),
        'paths': review.get('paths') or [],
        'reason': reason,
        'remark': 'Patch 已准奏' if action == 'approve' else 'Patch 已驳回并尝试回滚',
    }, trace_id=review.get('traceId', ''))
    return {'ok': True, 'message': 'Patch 已准奏' if action == 'approve' else 'Patch 已驳回并回滚', 'review': public}


def read_source_file(path_value: str, start_line=0, end_line=0, context=4):
    source = _safe_source_file(path_value)
    if not source:
        return {'ok': False, 'error': 'file not found or not allowed'}
    try:
        raw_lines = source.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception as exc:
        return {'ok': False, 'error': f'读取失败: {exc}'}
    total = len(raw_lines)
    try:
        start = max(1, int(start_line or 1))
    except Exception:
        start = 1
    try:
        end = int(end_line or start)
    except Exception:
        end = start
    if end < start:
        end = start
    if start_line or end_line:
        view_start = max(1, start - max(0, int(context or 0)))
        view_end = min(total, end + max(0, int(context or 0)))
    else:
        view_start = 1
        view_end = min(total, 240)
        start = 0
        end = 0
    lines = [
        {'no': idx, 'text': raw_lines[idx - 1], 'highlight': bool(start and start <= idx <= end)}
        for idx in range(view_start, view_end + 1)
    ]
    return {
        'ok': True,
        'path': _rel_project_path(source),
        'absolutePath': str(source),
        'startLine': start,
        'endLine': end,
        'viewStart': view_start,
        'viewEnd': view_end,
        'totalLines': total,
        'lines': lines,
    }


def _file_kind(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext in {'.md', '.txt', '.log'}:
        return '文档'
    if ext in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}:
        return '图片'
    if ext in {'.pdf', '.docx', '.xlsx', '.pptx'}:
        return '办公文件'
    if ext in {'.html', '.css', '.js'}:
        return '网页产物'
    if ext in {'.json', '.csv'}:
        return '数据'
    if ext in {'.mp4'}:
        return '视频'
    return '文件'


def _size_label(size: int) -> str:
    units = ['B', 'KB', 'MB', 'GB']
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f'{value:.1f}{unit}' if unit != 'B' else f'{int(value)}B'
        value /= 1024
    return f'{size}B'


def _output_item(path: pathlib.Path, source='输出', task=None):
    try:
        resolved = path.resolve()
        rel = resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
        st = resolved.stat()
    except Exception:
        return None
    params = urlencode({'path': rel})
    item = {
        'path': rel,
        'absolutePath': str(resolved),
        'name': resolved.name,
        'ext': resolved.suffix.lower(),
        'kind': _file_kind(resolved),
        'source': source,
        'size': st.st_size,
        'sizeLabel': _size_label(st.st_size),
        'mtime': datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec='seconds'),
        'viewUrl': f'/api/output-file?{params}',
        'downloadUrl': f'/api/output-file?{params}&download=1',
    }
    if task:
        item['taskId'] = task.get('id', '')
        item['taskTitle'] = task.get('title', '')
    return item


def _task_group(task):
    return {
        'taskId': task.get('id', ''),
        'taskTitle': task.get('title', '') or task.get('id', ''),
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'updatedAt': task.get('updatedAt', '') or task.get('eta', ''),
        'outputText': '',
        'files': [],
    }


def _collect_files_under(path: pathlib.Path, source: str, task: dict):
    files = []
    if path.is_file() and path.suffix.lower() in _OUTPUT_EXTS:
        item = _output_item(path, source=source, task=task)
        if item:
            files.append(item)
        return files
    if not path.is_dir():
        return files
    for child in path.rglob('*'):
        if not child.is_file() or child.suffix.lower() not in _OUTPUT_EXTS:
            continue
        if set(child.parts).intersection({'node_modules', '.git', '__pycache__'}):
            continue
        item = _output_item(child, source=source, task=task)
        if item:
            files.append(item)
    return files


def list_output_files(limit=300):
    items = {}
    for root in _output_roots():
        root_path = root['path']
        if not root_path.exists() or not root_path.is_dir():
            continue
        for path in root_path.rglob('*'):
            if not path.is_file():
                continue
            parts = set(path.parts)
            if parts.intersection({'node_modules', '.git', '__pycache__'}):
                continue
            if path.suffix.lower() not in _OUTPUT_EXTS:
                continue
            item = _output_item(path, source=root['label'])
            if item:
                items[item['path']] = item

    task_groups = []
    assigned_paths = set()
    for task in load_tasks():
        output_path = (task or {}).get('output') or ''
        if not output_path or output_path == '-':
            continue
        group = _task_group(task)
        path = _resolve_project_output_path(output_path)
        if path and path.exists():
            for item in _collect_files_under(path, '任务产出', task):
                items[item['path']] = item
                group['files'].append(item)
                assigned_paths.add(item['path'])
        else:
            group['outputText'] = output_path

        task_id = task.get('id') or ''
        if task_id:
            for item in list(items.values()):
                if item['path'] in assigned_paths:
                    continue
                if task_id in item.get('path', '') or task_id in item.get('name', ''):
                    task_item = dict(item)
                    task_item['taskId'] = task_id
                    task_item['taskTitle'] = task.get('title', '')
                    task_item['source'] = '任务产出'
                    items[task_item['path']] = task_item
                    group['files'].append(task_item)
                    assigned_paths.add(task_item['path'])
        if group['files'] or group['outputText']:
            group['files'] = sorted(group['files'], key=lambda x: x.get('mtime', ''), reverse=True)
            task_groups.append(group)

    files = sorted(items.values(), key=lambda x: x.get('mtime', ''), reverse=True)
    unassigned = [item for item in files if item['path'] not in assigned_paths]
    if unassigned:
        task_groups.append({
            'taskId': '__unassigned__',
            'taskTitle': '未归属产物',
            'state': '',
            'org': '平台',
            'updatedAt': '',
            'outputText': '',
            'files': unassigned,
        })
    return {
        'ok': True,
        'root': str(PROJECT_ROOT.resolve()),
        'generatedAt': now_iso(),
        'count': len(files),
        'files': files[:limit],
        'groups': task_groups[:limit],
    }


def _refresh_watcher_alive(task_data_dir):
    """Best-effort check for the debounce watcher used by kanban updates."""
    pid_file = task_data_dir / '.refresh_watcher_pid'
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, 0)
        return True
    except Exception:
        try:
            pid_file.unlink()
        except Exception:
            pass
        return False


def _trigger_refresh():
    """Trigger live data refresh without spawning one process per mutation.

    If refresh_watcher.py is running, a signal-file touch is enough. Otherwise
    the dashboard falls back to an in-process debounce timer so a burst of
    task writes still becomes one refresh.
    """
    global _REFRESH_GENERATION
    task_data_dir = get_task_data_dir()
    signal_file = task_data_dir / '.refresh_pending'
    try:
        signal_file.touch(exist_ok=True)
    except Exception:
        pass

    if _refresh_watcher_alive(task_data_dir):
        return

    script = task_data_dir.parent / 'scripts' / 'refresh_live_data.py'
    if not script.exists():
        script = SCRIPTS / 'refresh_live_data.py'

    def _refresh():
        try:
            try:
                signal_file.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            subprocess.run([python_bin(), str(script)], timeout=30)
        except Exception as e:
            log.warning(f'refresh_live_data.py 触发失败: {e}')

    def _delayed_refresh(generation):
        import time as _time
        _time.sleep(_REFRESH_DEBOUNCE_SEC)
        with _REFRESH_TIMER_LOCK:
            if generation != _REFRESH_GENERATION:
                return
        _refresh()

    with _REFRESH_TIMER_LOCK:
        _REFRESH_GENERATION += 1
        generation = _REFRESH_GENERATION
    _REAL_THREAD(target=_delayed_refresh, args=(generation,), daemon=True).start()


def modify_tasks(modifier):
    """Atomically read-modify-write the tasks file.

    ``modifier(tasks)`` receives the current task list, mutates it in place
    (or returns a new list), and the result is persisted while the file lock
    is held.  This avoids the TOCTOU race inherent in separate
    ``load_tasks()`` / ``save_tasks()`` calls when background threads
    (dispatch callbacks, periodic scanner) and the HTTP handler mutate tasks
    concurrently.
    """
    task_data_dir = get_task_data_dir()
    path = task_data_dir / 'tasks_source.json'
    atomic_json_update(path, modifier, default=[])
    _trigger_refresh()


def modify_task(task_id, updater):
    """Atomically update a single task identified by *task_id*.

    ``updater(task)`` receives the task dict and should mutate it in place.
    Returns ``True`` if the task was found and updated, ``False`` otherwise.
    """
    found = [False]

    def _modifier(tasks):
        task = next((t for t in tasks if t.get('id') == task_id), None)
        if task is None:
            return tasks
        updater(task)
        task['updatedAt'] = now_iso()
        found[0] = True
        return tasks

    modify_tasks(_modifier)
    return found[0]


def handle_task_action(task_id, action, reason):
    """Stop/cancel/resume a task from the dashboard."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    old_state = task.get('state', '')
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'task-action-before-{action}')

    if action == 'stop':
        task['state'] = 'Blocked'
        task['block'] = reason or '皇上叫停'
        task['now'] = f'⏸️ 已暂停：{reason}'
    elif action == 'cancel':
        task['state'] = 'Cancelled'
        task['block'] = reason or '皇上取消'
        task['now'] = f'🚫 已取消：{reason}'
    elif action == 'resume':
        # Resume to previous active state or Doing
        task['state'] = task.get('_prev_state', 'Doing')
        task['block'] = '无'
        task['now'] = f'▶️ 已恢复执行'

    if action in ('stop', 'cancel'):
        task['_prev_state'] = old_state  # Save for resume

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '皇上',
        'to': task.get('org', ''),
        'remark': f'{"⏸️ 叫停" if action == "stop" else "🚫 取消" if action == "cancel" else "▶️ 恢复"}：{reason}'
    })

    if action == 'resume':
        _scheduler_mark_progress(task, f'恢复到 {task.get("state", "Doing")}')
    else:
        _scheduler_add_flow(task, f'皇上{action}：{reason or "无"}')

    task['updatedAt'] = now_iso()

    save_tasks(tasks)
    if action == 'resume' and task.get('state') not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, task.get('state'), trigger='resume')
    label = {'stop': '已叫停', 'cancel': '已取消', 'resume': '已恢复'}[action]
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_archive_task(task_id, archived, archive_all_done=False):
    """Archive or unarchive a task, or batch-archive all Done/Cancelled tasks."""
    tasks = load_tasks()
    if archive_all_done:
        count = 0
        for t in tasks:
            if t.get('state') in ('Done', 'Cancelled') and not t.get('archived'):
                t['archived'] = True
                t['archivedAt'] = now_iso()
                count += 1
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道旨意已归档', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    task['archived'] = archived
    if archived:
        task['archivedAt'] = now_iso()
    else:
        task.pop('archivedAt', None)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    label = '已归档' if archived else '已取消归档'
    return {'ok': True, 'message': f'{task_id} {label}'}


def handle_delete_archived(task_id=None, delete_all=False):
    """Permanently delete a single archived task or all archived tasks."""
    tasks = load_tasks()
    if delete_all:
        before = len(tasks)
        tasks = [t for t in tasks if not t.get('archived')]
        count = before - len(tasks)
        save_tasks(tasks)
        return {'ok': True, 'message': f'{count} 道已归档旨意已永久删除', 'count': count}
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if not task.get('archived'):
        return {'ok': False, 'error': f'任务 {task_id} 未归档，无法删除'}
    tasks = [t for t in tasks if t.get('id') != task_id]
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} 已永久删除'}


def update_task_todos(task_id, todos):
    """Update the todos list for a task."""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    task['todos'] = todos
    task['updatedAt'] = now_iso()
    save_tasks(tasks)
    return {'ok': True, 'message': f'{task_id} todos 已更新'}


def read_skill_content(agent_id, skill_name):
    """Read SKILL.md content for a specific skill."""
    # 输入校验：防止路径遍历
    if not _SAFE_NAME_RE.match(agent_id) or not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': '参数含非法字符'}
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    ag = next((a for a in agents if a.get('id') == agent_id), None)
    if not ag:
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    sk = next((s for s in ag.get('skills', []) if s.get('name') == skill_name), None)
    if not sk:
        return {'ok': False, 'error': f'技能 {skill_name} 不存在'}
    skill_path = pathlib.Path(sk.get('path', '')).resolve()
    # 路径遍历保护：确保路径在 OCLAW_HOME 或项目目录下
    allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
    if not any(str(skill_path).startswith(str(root)) for root in allowed_roots):
        return {'ok': False, 'error': '路径不在允许的目录范围内'}
    if not skill_path.exists():
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': '(SKILL.md 文件不存在)', 'path': str(skill_path)}
    try:
        content = skill_path.read_text()
        return {'ok': True, 'name': skill_name, 'agent': agent_id, 'content': content, 'path': str(skill_path)}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


def add_skill_to_agent(agent_id, skill_name, description, trigger=''):
    """Create a new skill for an agent with a standardised SKILL.md template."""
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skill_name 含非法字符: {skill_name}'}
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    desc_line = description or skill_name
    trigger_section = f'\n## 触发条件\n{trigger}\n' if trigger else ''
    template = (f'---\n'
                f'name: {skill_name}\n'
                f'description: {desc_line}\n'
                f'---\n\n'
                f'# {skill_name}\n\n'
                f'{desc_line}\n'
                f'{trigger_section}\n'
                f'## 输入\n\n'
                f'<!-- 说明此技能接收什么输入 -->\n\n'
                f'## 处理流程\n\n'
                f'1. 步骤一\n'
                f'2. 步骤二\n\n'
                f'## 输出规范\n\n'
                f'<!-- 说明产出物格式与交付要求 -->\n\n'
                f'## 注意事项\n\n'
                f'- (在此补充约束、限制或特殊规则)\n')
    skill_md.write_text(template)
    # Re-sync agent config
    try:
        subprocess.run([python_bin(), str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    return {'ok': True, 'message': f'技能 {skill_name} 已添加到 {agent_id}', 'path': str(skill_md)}


def add_remote_skill(agent_id, skill_name, source_url, description=''):
    """从远程 URL 或本地路径为 Agent 添加 skill SKILL.md 文件。
    
    支持的源：
    - HTTPS URLs: https://raw.githubusercontent.com/...
    - 本地路径: /path/to/SKILL.md 或 file:///path/to/SKILL.md
    """
    # 输入校验
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    if not source_url or not isinstance(source_url, str):
        return {'ok': False, 'error': 'sourceUrl 必须是有效的字符串'}
    
    source_url = source_url.strip()
    
    # 检查 Agent 是否存在
    cfg = read_json(DATA / 'agent_config.json', {})
    agents = cfg.get('agents', [])
    if not any(a.get('id') == agent_id for a in agents):
        return {'ok': False, 'error': f'Agent {agent_id} 不存在'}
    
    # 下载或读取文件内容
    try:
        if source_url.startswith('http://') or source_url.startswith('https://'):
            # HTTPS URL 校验
            if not validate_url(source_url, allowed_schemes=('https',)):
                return {'ok': False, 'error': 'URL 无效或不安全（仅支持 HTTPS）'}
            
            # 从 URL 下载，带超时保护
            req = Request(source_url, headers={'User-Agent': 'OpenClaw-SkillManager/1.0'})
            try:
                resp = urlopen(req, timeout=10)
                content = resp.read(10 * 1024 * 1024).decode('utf-8')  # 最多 10MB
                if len(content) > 10 * 1024 * 1024:
                    return {'ok': False, 'error': '文件过大（最大 10MB）'}
            except Exception as e:
                return {'ok': False, 'error': f'URL 无法访问: {str(e)[:100]}'}
        
        elif source_url.startswith('file://'):
            # file:// URL 格式
            local_path = pathlib.Path(source_url[7:]).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护：与本地路径分支一致，确保在允许范围内
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root)) for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        
        elif source_url.startswith('/') or source_url.startswith('.'):
            # 本地绝对或相对路径
            local_path = pathlib.Path(source_url).resolve()
            if not local_path.exists():
                return {'ok': False, 'error': f'本地文件不存在: {local_path}'}
            # 路径遍历防护
            allowed_roots = (OCLAW_HOME.resolve(), BASE.parent.resolve())
            if not any(str(local_path).startswith(str(root)) for root in allowed_roots):
                return {'ok': False, 'error': '路径不在允许的目录范围内'}
            content = local_path.read_text()
        
        else:
            return {'ok': False, 'error': '不支持的 URL 格式（仅支持 https://, file://, 或本地路径）'}
    except Exception as e:
        return {'ok': False, 'error': f'文件读取失败: {str(e)[:100]}'}
    
    # 基础验证：检查是否为 Markdown 且包含 YAML frontmatter
    if not content.startswith('---'):
        return {'ok': False, 'error': '文件格式无效（缺少 YAML frontmatter）'}
    
    # 验证 frontmatter 结构（先做字符串检查，再尝试 YAML 解析）
    parts = content.split('---', 2)
    if len(parts) < 3:
        return {'ok': False, 'error': '文件格式无效（YAML frontmatter 结构错误）'}
    if 'name:' not in content[:500]:
        return {'ok': False, 'error': '文件格式无效：frontmatter 缺少 name 字段'}
    try:
        import yaml
        yaml.safe_load(parts[1])  # 严格校验 YAML 语法
    except ImportError:
        pass  # PyYAML 未安装，跳过严格验证，字符串检查已通过
    except Exception as e:
        return {'ok': False, 'error': f'YAML 格式无效: {str(e)[:100]}'}
    
    # 创建本地目录
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    workspace.mkdir(parents=True, exist_ok=True)
    skill_md = workspace / 'SKILL.md'
    
    # 写入 SKILL.md
    skill_md.write_text(content)
    
    # 保存源信息到 .source.json
    source_info = {
        'skillName': skill_name,
        'sourceUrl': source_url,
        'description': description,
        'addedAt': now_iso(),
        'lastUpdated': now_iso(),
        'checksum': _compute_checksum(content),
        'status': 'valid',
    }
    source_json = workspace / '.source.json'
    source_json.write_text(json.dumps(source_info, ensure_ascii=False, indent=2))
    
    # Re-sync agent config
    try:
        subprocess.run([python_bin(), str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
    except Exception:
        pass
    
    return {
        'ok': True,
        'message': f'技能 {skill_name} 已从远程源添加到 {agent_id}',
        'skillName': skill_name,
        'agentId': agent_id,
        'source': source_url,
        'localPath': str(skill_md),
        'size': len(content),
        'addedAt': now_iso(),
    }


def get_remote_skills_list():
    """列表所有已添加的远程 skills 及其源信息"""
    remote_skills = []
    
    # 遍历所有 workspace
    for ws_dir in OCLAW_HOME.glob('workspace-*'):
        agent_id = ws_dir.name.replace('workspace-', '')
        skills_dir = ws_dir / 'skills'
        if not skills_dir.exists():
            continue
        
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_name = skill_dir.name
            source_json = skill_dir / '.source.json'
            skill_md = skill_dir / 'SKILL.md'
            
            if not source_json.exists():
                # 本地创建的 skill，跳过
                continue
            
            try:
                source_info = json.loads(source_json.read_text())
                # 检查 SKILL.md 是否存在
                status = 'valid' if skill_md.exists() else 'not-found'
                remote_skills.append({
                    'skillName': skill_name,
                    'agentId': agent_id,
                    'sourceUrl': source_info.get('sourceUrl', ''),
                    'description': source_info.get('description', ''),
                    'localPath': str(skill_md),
                    'addedAt': source_info.get('addedAt', ''),
                    'lastUpdated': source_info.get('lastUpdated', ''),
                    'status': status,
                })
            except Exception:
                pass
    
    return {
        'ok': True,
        'remoteSkills': remote_skills,
        'count': len(remote_skills),
        'listedAt': now_iso(),
    }


def update_remote_skill(agent_id, skill_name):
    """更新已添加的远程 skill 为最新版本（重新从源 URL 下载）"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    source_json = workspace / '.source.json'
    skill_md = workspace / 'SKILL.md'
    
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill（无 .source.json）'}
    
    try:
        source_info = json.loads(source_json.read_text())
        source_url = source_info.get('sourceUrl', '')
        if not source_url:
            return {'ok': False, 'error': '源 URL 不存在'}
        
        # 重新下载
        result = add_remote_skill(agent_id, skill_name, source_url, 
                                  source_info.get('description', ''))
        if result['ok']:
            result['message'] = f'技能已更新'
            source_info_updated = json.loads(source_json.read_text())
            result['newVersion'] = source_info_updated.get('checksum', 'unknown')
        return result
    except Exception as e:
        return {'ok': False, 'error': f'更新失败: {str(e)[:100]}'}


def remove_remote_skill(agent_id, skill_name):
    """移除已添加的远程 skill"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agentId 含非法字符: {agent_id}'}
    if not _SAFE_NAME_RE.match(skill_name):
        return {'ok': False, 'error': f'skillName 含非法字符: {skill_name}'}
    
    workspace = OCLAW_HOME / f'workspace-{agent_id}' / 'skills' / skill_name
    if not workspace.exists():
        return {'ok': False, 'error': f'技能不存在: {skill_name}'}
    
    # 检查是否为远程 skill
    source_json = workspace / '.source.json'
    if not source_json.exists():
        return {'ok': False, 'error': f'技能 {skill_name} 不是远程 skill，无法通过此 API 移除'}
    
    try:
        # 删除整个 skill 目录
        import shutil
        shutil.rmtree(workspace)
        
        # Re-sync agent config
        try:
            subprocess.run([python_bin(), str(SCRIPTS / 'sync_agent_config.py')], timeout=10)
        except Exception:
            pass
        
        return {'ok': True, 'message': f'技能 {skill_name} 已从 {agent_id} 移除'}
    except Exception as e:
        return {'ok': False, 'error': f'移除失败: {str(e)[:100]}'}


def _compute_checksum(content: str) -> str:
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def migrate_notification_config():
    """自动迁移旧配置 (feishu_webhook) 到新结构 (notification)"""
    cfg_path = DATA / 'morning_brief_config.json'
    cfg = read_json(cfg_path, {})
    if not cfg:
        return
    if 'notification' in cfg:
        return
    if 'feishu_webhook' not in cfg:
        return
    webhook = cfg.get('feishu_webhook', '').strip()
    cfg['notification'] = {
        'enabled': bool(webhook),
        'channel': 'feishu',
        'webhook': webhook
    }
    try:
        atomic_json_write(cfg_path, cfg)
        log.info('已自动迁移 feishu_webhook 到 notification 配置')
    except Exception as e:
        log.warning(f'迁移配置失败: {e}')


def push_notification():
    """通用消息推送 (支持多渠道)"""
    cfg = read_json(DATA / 'morning_brief_config.json', {})
    notification = cfg.get('notification', {})
    if not notification and cfg.get('feishu_webhook'):
        notification = {'enabled': True, 'channel': 'feishu', 'webhook': cfg['feishu_webhook']}
    if not notification.get('enabled', True):
        return
    channel_type = notification.get('channel', 'feishu')
    webhook = notification.get('webhook', '').strip()
    if not webhook:
        return
    channel_cls = get_channel(channel_type)
    if not channel_cls:
        log.warning(f'未知的通知渠道: {channel_type}')
        return
    if not channel_cls.validate_webhook(webhook):
        log.warning(f'{channel_cls.label} Webhook URL 不合法: {webhook}')
        return
    brief = read_json(DATA / 'morning_brief.json', {})
    date_str = brief.get('date', '')
    total = sum(len(v) for v in (brief.get('categories') or {}).values())
    if not total:
        return
    cat_lines = []
    for cat, items in (brief.get('categories') or {}).items():
        if items:
            cat_lines.append(f'  {cat}: {len(items)} 条')
    summary = '\n'.join(cat_lines)
    date_fmt = date_str[:4] + '年' + date_str[4:6] + '月' + date_str[6:] + '日' if len(date_str) == 8 else date_str
    title = f'📰 天下要闻 · {date_fmt}'
    content = f'共 **{total}** 条要闻已更新\n{summary}'
    url = f'http://127.0.0.1:{_DASHBOARD_PORT}'
    success = channel_cls.send(webhook, title, content, url)
    print(f'[{channel_cls.label}] 推送{"成功" if success else "失败"}')


def push_to_feishu():
    """Push morning brief link to Feishu via webhook. (已弃用，使用 push_notification)"""
    push_notification()


# 旨意标题最低要求
_MIN_TITLE_LEN = 6
_JUNK_TITLES = {
    '?', '？', '好', '好的', '是', '否', '不', '不是', '对', '了解', '收到',
    '嗯', '哦', '知道了', '开启了么', '可以', '不行', '行', 'ok', 'yes', 'no',
    '你去开启', '测试', '试试', '看看',
}


def handle_create_task(title, org='中书省', official='中书令', priority='normal', template_id='', params=None, target_dept='', auto_dispatch=True):
    """从看板创建新任务（圣旨模板下旨）。"""
    if not title or not title.strip():
        return {'ok': False, 'error': '任务标题不能为空'}
    title = title.strip()
    # 剥离 Conversation info 元数据
    title = re.split(r'\n*Conversation info\s*\(', title, maxsplit=1)[0].strip()
    title = re.split(r'\n*```', title, maxsplit=1)[0].strip()
    # 清理常见前缀: "传旨:" "下旨:" 等
    title = re.sub(r'^(传旨|下旨)[：:\uff1a]\s*', '', title)
    if len(title) > 100:
        title = title[:100] + '…'
    # 标题质量校验：防止闲聊被误建为旨意
    if len(title) < _MIN_TITLE_LEN:
        return {'ok': False, 'error': f'标题过短（{len(title)}<{_MIN_TITLE_LEN}字），不像是旨意'}
    if title.lower() in _JUNK_TITLES:
        return {'ok': False, 'error': f'「{title}」不是有效旨意，请输入具体工作指令'}
    # 生成 task id: JJC-YYYYMMDD-NNN
    today = datetime.datetime.now().strftime('%Y%m%d')
    tasks = load_tasks()
    today_ids = [t['id'] for t in tasks if t.get('id', '').startswith(f'JJC-{today}-')]
    seq = 1
    if today_ids:
        nums = [int(tid.split('-')[-1]) for tid in today_ids if tid.split('-')[-1].isdigit()]
        seq = max(nums) + 1 if nums else 1
    task_id = f'JJC-{today}-{seq:03d}'
    # 正确流程起点：皇上 -> 太子分拣
    # target_dept 记录模板建议的最终执行部门（仅供尚书省派发参考）
    initial_org = '太子'
    new_task = {
        'id': task_id,
        'title': title,
        'official': official,
        'org': initial_org,
        'state': 'Taizi',
        'now': '等待太子接旨分拣',
        'eta': '-',
        'block': '无',
        'output': '',
        'ac': '',
        'priority': priority,
        'templateId': template_id,
        'templateParams': params or {},
        'traceId': f'trc_{uuid.uuid4().hex[:16]}',
        'flow_log': [{
            'at': now_iso(),
            'from': '皇上',
            'to': initial_org,
            'remark': f'下旨：{title}'
        }],
        'updatedAt': now_iso(),
    }
    if target_dept:
        new_task['targetDept'] = target_dept

    _ensure_scheduler(new_task)
    _scheduler_snapshot(new_task, 'create-task-initial')
    _scheduler_mark_progress(new_task, '任务创建')

    tasks.insert(0, new_task)
    save_tasks(tasks)
    log.info(f'创建任务: {task_id} | {title[:40]}')

    if auto_dispatch:
        dispatch_for_state(task_id, new_task, 'Taizi', trigger='imperial-edict')
        return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，正在派发给太子'}

    return {'ok': True, 'taskId': task_id, 'message': f'旨意 {task_id} 已下达，等待人工推进'}


def _capabilities_file():
    return DATA / 'capabilities.json'


def _run_specs_file():
    return DATA / 'run_specs.json'


_PERMISSION_LABELS = {
    'agent.run': '调用 Agent',
    'workspace.read': '读工作区',
    'workspace.write': '写工作区',
    'shell.execute': '执行命令',
    'browser.control': '控制浏览器',
    'network.local': '访问本地服务',
    'network.web': '访问网络',
    'document.read': '读文档',
    'document.write': '写文档',
    'artifact.write': '沉淀产物',
    'policy.review': '治理审议',
}

_APPROVAL_PERMISSIONS = {'shell.execute'}


def _ordered_unique(items):
    seen = set()
    out = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _permission_labels(permissions):
    return [_PERMISSION_LABELS.get(item, item) for item in permissions or []]


def _capability_availability(capability):
    """Lightweight capability availability hints for the command center."""
    cap_id = capability.get('id', '')
    active_runtime = _agent_runtime()

    if cap_id == 'runtime.opencode':
        configured = bool(_resolve_opencode_bin() or (PROJECT_ROOT / 'opencode.json').exists())
        if configured and active_runtime == 'opencode':
            return {'status': 'ready', 'label': '当前运行时', 'reason': 'OpenCode 已作为当前 agent runtime'}
        if configured:
            return {'status': 'configured', 'label': '已配置', 'reason': 'OpenCode CLI 或配置已存在，可切换使用'}
        return {'status': 'missing', 'label': '待配置', 'reason': '未找到 opencode CLI 或 opencode.json'}

    if cap_id == 'runtime.openclaw':
        configured = bool(_resolve_openclaw_bin() or (OCLAW_HOME / 'openclaw.json').exists())
        if configured and active_runtime == 'openclaw':
            return {'status': 'ready', 'label': '当前运行时', 'reason': 'OpenClaw 已作为当前 agent runtime'}
        if configured:
            return {'status': 'configured', 'label': '已配置', 'reason': 'OpenClaw CLI 或配置已存在，可切换使用'}
        return {'status': 'missing', 'label': '待配置', 'reason': '未找到 openclaw CLI 或 ~/.openclaw/openclaw.json'}

    if cap_id == 'code.workspace':
        if (PROJECT_ROOT / '.git').exists():
            return {'status': 'ready', 'label': '可用', 'reason': '当前目录是可追踪代码工作区'}
        return {'status': 'configured', 'label': '工作区可读', 'reason': '未检测到 git 仓库，但工作区目录存在'}

    if cap_id in ('file.workspace', 'shell.command', 'artifact.outputs', 'governance.plan'):
        return {'status': 'ready', 'label': '可用', 'reason': '由本地看板和当前工作区提供'}

    if cap_id == 'browser.control':
        return {'status': 'unknown', 'label': '按任务连接', 'reason': '浏览器连接器由执行 agent 在任务中确认'}

    if cap_id == 'document.office':
        return {'status': 'unknown', 'label': '按任务连接', 'reason': '文档能力由执行 agent 或本地依赖在任务中确认'}

    return {'status': 'unknown', 'label': '未知', 'reason': '自定义能力未提供可用性探测'}


def _default_capabilities():
    return {
        'categories': [
            {'id': 'runtime', 'label': '运行时', 'description': '连接 OpenCode、OpenClaw、Codex 等执行环境'},
            {'id': 'code', 'label': '代码工作区', 'description': '读取、修改、测试、审查仓库代码'},
            {'id': 'file', 'label': '本地文件', 'description': '读取、生成、整理和链接本地文件'},
            {'id': 'shell', 'label': '命令执行', 'description': '执行终端命令、脚本、构建和服务启动'},
            {'id': 'browser', 'label': '浏览器', 'description': '打开网页、检查页面、抓取公开信息和验证 UI'},
            {'id': 'document', 'label': '办公文档', 'description': '处理 PDF、Word、PPT、表格等工作文件'},
            {'id': 'artifact', 'label': '产物沉淀', 'description': '汇总输出、报告、链接和复用资产'},
            {'id': 'governance', 'label': '治理链路', 'description': '规划、审议、审批、校验和归档'},
        ],
        'capabilities': [
            {
                'id': 'runtime.opencode',
                'name': 'OpenCode Runtime',
                'category': 'runtime',
                'description': '把任务交给 OpenCode agent 执行，并回收工具事件、输出和状态。',
                'adapters': ['opencode'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['agent', 'runtime', 'code'],
                'inputs': ['goal', 'workspace'],
                'outputs': ['events', 'patches', 'artifacts'],
                'permissions': ['agent.run', 'workspace.read', 'workspace.write', 'artifact.write'],
            },
            {
                'id': 'runtime.openclaw',
                'name': 'OpenClaw Runtime',
                'category': 'runtime',
                'description': '兼容 OpenClaw 风格的多 agent 派发和执行入口。',
                'adapters': ['openclaw'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['agent', 'runtime'],
                'inputs': ['goal', 'agent'],
                'outputs': ['events', 'artifacts'],
                'permissions': ['agent.run', 'workspace.read', 'workspace.write', 'artifact.write'],
            },
            {
                'id': 'code.workspace',
                'name': 'Workspace Code',
                'category': 'code',
                'description': '读取、修改、测试和审查当前工作区代码。',
                'adapters': ['opencode', 'codex'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['repo', 'patch', 'test'],
                'inputs': ['path', 'diff', 'goal'],
                'outputs': ['patch', 'testResult'],
                'permissions': ['workspace.read', 'workspace.write'],
            },
            {
                'id': 'file.workspace',
                'name': 'Local Files',
                'category': 'file',
                'description': '读取和生成工作区内的本地文件，并在输出中心按任务归档。',
                'adapters': ['dashboard', 'codex'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['file', 'output', 'link'],
                'inputs': ['path', 'content'],
                'outputs': ['fileLink', 'artifact'],
                'permissions': ['workspace.read', 'workspace.write', 'artifact.write'],
            },
            {
                'id': 'shell.command',
                'name': 'Shell Command',
                'category': 'shell',
                'description': '执行 Linux/macOS 命令、构建、测试、脚本和服务启动。',
                'adapters': ['opencode', 'codex'],
                'risk': 'high',
                'enabled': True,
                'tags': ['terminal', 'test', 'service'],
                'inputs': ['command', 'cwd'],
                'outputs': ['stdout', 'stderr', 'exitCode'],
                'permissions': ['shell.execute', 'workspace.read', 'workspace.write'],
                'requiresApproval': True,
            },
            {
                'id': 'browser.control',
                'name': 'Browser Control',
                'category': 'browser',
                'description': '打开和检查网页，验证本地 UI，采集页面状态。',
                'adapters': ['browser', 'playwright'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['web', 'ui', 'verify'],
                'inputs': ['url', 'action'],
                'outputs': ['screenshot', 'console', 'network'],
                'permissions': ['browser.control', 'network.local', 'network.web'],
            },
            {
                'id': 'document.office',
                'name': 'Office Documents',
                'category': 'document',
                'description': '创建、读取、修改和渲染 PDF、Word、PPT、Excel 文件。',
                'adapters': ['documents', 'presentations', 'spreadsheets'],
                'risk': 'medium',
                'enabled': True,
                'tags': ['pdf', 'docx', 'pptx', 'xlsx'],
                'inputs': ['file', 'instruction'],
                'outputs': ['document', 'preview', 'report'],
                'permissions': ['document.read', 'document.write', 'workspace.read', 'workspace.write'],
            },
            {
                'id': 'artifact.outputs',
                'name': 'Task Artifacts',
                'category': 'artifact',
                'description': '把每个任务的报告、文件、截图和补丁按任务聚合展示。',
                'adapters': ['dashboard'],
                'risk': 'low',
                'enabled': True,
                'tags': ['output', 'trace', 'memory'],
                'inputs': ['taskId', 'file'],
                'outputs': ['artifactIndex', 'taskOutput'],
                'permissions': ['artifact.write', 'workspace.read'],
            },
            {
                'id': 'governance.plan',
                'name': 'Plan & Review',
                'category': 'governance',
                'description': '先形成 RunSpec，再按风险进入太子分拣、中书成案、门下审议、尚书调度和刑部校验。',
                'adapters': ['edict'],
                'risk': 'low',
                'enabled': True,
                'tags': ['runspec', 'policy', 'review'],
                'inputs': ['goal', 'risk'],
                'outputs': ['runGraph', 'policyDecision'],
                'permissions': ['policy.review'],
            },
        ],
    }


def list_capabilities():
    """Return configured capabilities, falling back to a built-in registry."""
    default = _default_capabilities()
    data = atomic_json_read(_capabilities_file(), default)
    if not isinstance(data, dict):
        data = default
    categories = data.get('categories') if isinstance(data.get('categories'), list) else default['categories']
    capabilities = data.get('capabilities') if isinstance(data.get('capabilities'), list) else default['capabilities']
    category_labels = {c.get('id'): c.get('label', c.get('id', '')) for c in categories if isinstance(c, dict)}
    default_capabilities = {
        cap.get('id'): cap for cap in default['capabilities']
        if isinstance(cap, dict) and cap.get('id')
    }
    normalized = []
    for cap in capabilities:
        if not isinstance(cap, dict) or not cap.get('id'):
            continue
        default_cap = default_capabilities.get(cap.get('id'), {})
        item = dict(cap)
        item['enabled'] = bool(item.get('enabled', True))
        item['categoryLabel'] = category_labels.get(item.get('category'), item.get('category', ''))
        item.setdefault('tags', default_cap.get('tags', []))
        item.setdefault('adapters', default_cap.get('adapters', []))
        item.setdefault('inputs', default_cap.get('inputs', []))
        item.setdefault('outputs', default_cap.get('outputs', []))
        permissions = item.get('permissions') if isinstance(item.get('permissions'), list) else []
        if not permissions:
            permissions = default_cap.get('permissions', [])
        item['permissions'] = _ordered_unique(str(perm) for perm in permissions)
        item['permissionLabels'] = _permission_labels(item['permissions'])
        default_requires_approval = bool(default_cap.get('requiresApproval', False))
        item['requiresApproval'] = bool(
            item.get('requiresApproval', default_requires_approval)
            or item.get('risk') == 'high'
            or any(perm in _APPROVAL_PERMISSIONS for perm in item['permissions'])
        )
        item['availability'] = _capability_availability(item)
        normalized.append(item)
    return {'ok': True, 'generatedAt': now_iso(), 'categories': categories, 'capabilities': normalized}


def _enabled_capability_ids():
    return {cap['id'] for cap in list_capabilities().get('capabilities', []) if cap.get('enabled')}


def _capability_category(cap_id):
    for cap in list_capabilities().get('capabilities', []):
        if cap.get('id') == cap_id:
            return cap.get('category', '')
    return ''


def _capability_policies_for_run(capability_ids):
    capability_map = {cap.get('id'): cap for cap in list_capabilities().get('capabilities', [])}
    policies = []
    for cap_id in capability_ids:
        cap = capability_map.get(cap_id, {})
        permissions = cap.get('permissions') if isinstance(cap.get('permissions'), list) else []
        policies.append({
            'id': cap_id,
            'name': cap.get('name', cap_id),
            'category': cap.get('category', ''),
            'categoryLabel': cap.get('categoryLabel', ''),
            'risk': cap.get('risk', 'medium'),
            'permissions': permissions,
            'permissionLabels': cap.get('permissionLabels') or _permission_labels(permissions),
            'requiresApproval': bool(cap.get('requiresApproval')),
            'availability': cap.get('availability') or {'status': 'unknown', 'label': '未知', 'reason': ''},
        })
    return policies


def _tool_policy_for_run(capability_ids, risk_level, mode, policies=None):
    policies = policies or _capability_policies_for_run(capability_ids)
    permissions = _ordered_unique(perm for policy in policies for perm in policy.get('permissions', []))
    unavailable = []
    unknown = []
    for policy in policies:
        availability = policy.get('availability') or {}
        status = availability.get('status')
        if status == 'missing':
            unavailable.append({
                'id': policy.get('id'),
                'name': policy.get('name'),
                'reason': availability.get('reason', ''),
            })
        elif status == 'unknown':
            unknown.append({
                'id': policy.get('id'),
                'name': policy.get('name'),
                'reason': availability.get('reason', ''),
            })
    requires_approval = bool(
        risk_level == 'high'
        or any(policy.get('requiresApproval') for policy in policies)
        or mode in ('plan', 'interactive')
    )
    if unavailable:
        approval_reason = '存在待配置能力，执行前需要确认替代路径'
    elif risk_level == 'high':
        approval_reason = '高风险或命令执行任务需要人工确认'
    elif mode == 'plan':
        approval_reason = '方案模式只生成 RunSpec，等待审议后再执行'
    elif mode == 'interactive':
        approval_reason = '目标需要最小补充，确认后再执行'
    else:
        approval_reason = '可按 RunSpec 治理链路自动分发'
    return {
        'permissions': permissions,
        'permissionLabels': _permission_labels(permissions),
        'requiresApproval': requires_approval,
        'approvalReason': approval_reason,
        'unavailableCapabilities': unavailable,
        'unknownCapabilities': unknown,
    }


def _infer_required_capabilities(goal, explicit_ids=None):
    enabled = _enabled_capability_ids()
    explicit = [item for item in (explicit_ids or []) if item in enabled]
    if explicit:
        return _ordered_unique(explicit + ['governance.plan', 'artifact.outputs'])

    text = (goal or '').lower()
    ids = ['governance.plan', 'runtime.opencode']
    keyword_map = [
        ('code.workspace', ['代码', '仓库', 'bug', '测试', '前端', '后端', 'api', 'commit', 'pr', 'pull request', 'react', 'python']),
        ('shell.command', ['命令', '终端', 'linux', 'shell', '脚本', '执行', '运行', '启动', '安装', '构建', 'pytest', 'npm']),
        ('browser.control', ['网页', '浏览器', '网站', '打开', '抓取', '搜索', 'ui', '页面', 'playwright', 'localhost']),
        ('document.office', ['ppt', 'pptx', 'word', 'docx', 'pdf', 'excel', 'xlsx', '表格', '文档', '幻灯片']),
        ('file.workspace', ['文件', '目录', '保存', '读取', '生成', '输出', '报告', '文章', 'markdown', 'md']),
        ('artifact.outputs', ['输出', '报告', '产物', '截图', '链接', '归档', '沉淀']),
    ]
    for cap_id, words in keyword_map:
        if cap_id in enabled and any(word in text for word in words):
            ids.append(cap_id)
    if 'artifact.outputs' not in ids:
        ids.append('artifact.outputs')
    if len(ids) <= 3 and 'file.workspace' in enabled:
        ids.append('file.workspace')
    return [item for item in _ordered_unique(ids) if item in enabled]


def _risk_level_for_run(goal, capability_ids):
    text = (goal or '').lower()
    destructive = [
        '删除', '清空', '覆盖', '重置', '回滚', 'rm -rf', 'reset --hard', 'drop table',
        '生产环境', '密钥', 'token', '密码', '支付', '转账', '发送邮件', '发消息',
    ]
    if any(word in text for word in destructive):
        return 'high'
    if 'shell.command' in capability_ids:
        return 'high'
    if {'code.workspace', 'browser.control', 'document.office', 'file.workspace'} & set(capability_ids):
        return 'medium'
    return 'low'


def _infer_run_mode(goal, requested_mode='auto'):
    requested = (requested_mode or 'auto').strip().lower()
    if requested in ('plan', 'execute', 'interactive'):
        return requested, '用户手动指定'

    text = (goal or '').lower()
    interactive_words = [
        '先问我', '问我确认', '需要确认', '每一步确认', '边做边问', '不要擅自',
        '确认后', '先征求', '让我确认', '需要我确认',
    ]
    plan_words = [
        '先给计划', '先计划', '先思考', '先分析', '只分析', '只读', '不要执行',
        '不要修改', '别修改', '先不要改', '不要直接改', '不要直接修改', '先给方案',
        '给出方案', '改造计划', '升级规划', '调研', '评估', '建议', '方案',
    ]
    execute_words = [
        '开始', '启动', '修复', '修改', '实现', '动手', '完成', '生成',
        '创建', '接入', '优化', '重构', '跑一下', '执行', '部署', '验证',
    ]
    if any(word in text for word in interactive_words):
        return 'interactive', '目标里包含确认/边做边问信号'
    if any(word in text for word in plan_words) and not any(word in text for word in execute_words):
        return 'plan', '目标更像先要方案或分析'
    if any(word in text for word in plan_words) and any(word in text for word in ['不要执行', '不要修改', '只读', '只分析', '先不要改', '不要直接']):
        return 'plan', '目标明确限制直接执行'
    return 'execute', '目标更像要完成具体动作'


def _governance_for_risk(risk_level, mode='execute'):
    common = [
        {'stage': 'intake', 'dept': '太子', 'label': '意图分拣'},
        {'stage': 'plan', 'dept': '中书省', 'label': '成案'},
    ]
    if mode == 'plan':
        return common + [
            {'stage': 'review', 'dept': '门下省', 'label': 'RunSpec 审议'},
            {'stage': 'hold', 'dept': '皇上', 'label': '等待推进'},
        ]
    if mode == 'interactive':
        return common + [
            {'stage': 'clarify', 'dept': '门下省', 'label': '最小补充'},
            {'stage': 'confirm', 'dept': '皇上', 'label': '等待确认'},
            {'stage': 'execute', 'dept': '尚书省', 'label': '确认后派发'},
            {'stage': 'verify', 'dept': '刑部', 'label': '结果校验'},
            {'stage': 'archive', 'dept': '史馆', 'label': '归档沉淀'},
        ]
    if risk_level == 'high':
        return common + [
            {'stage': 'risk_review', 'dept': '门下省', 'label': '风险审议'},
            {'stage': 'approval', 'dept': '皇上', 'label': '人工确认'},
            {'stage': 'execute', 'dept': '尚书省', 'label': '沙箱调度'},
            {'stage': 'verify', 'dept': '刑部', 'label': '结果校验'},
            {'stage': 'archive', 'dept': '史馆', 'label': '归档沉淀'},
        ]
    if risk_level == 'medium':
        return common + [
            {'stage': 'review', 'dept': '门下省', 'label': '边界审议'},
            {'stage': 'execute', 'dept': '尚书省', 'label': '派发执行'},
            {'stage': 'verify', 'dept': '刑部', 'label': '结果校验'},
            {'stage': 'archive', 'dept': '史馆', 'label': '归档沉淀'},
        ]
    return common + [
        {'stage': 'execute', 'dept': '尚书省', 'label': '直接调度'},
        {'stage': 'archive', 'dept': '史馆', 'label': '归档沉淀'},
    ]


def _infer_run_kind(goal, capability_ids):
    text = (goal or '').lower()
    if 'document.office' in capability_ids:
        return 'document'
    if 'code.workspace' in capability_ids:
        return 'coding'
    if 'browser.control' in capability_ids:
        return 'web'
    if 'shell.command' in capability_ids:
        return 'system'
    if any(word in text for word in ['文章', '公众号', '报告', '文案']):
        return 'writing'
    return 'general'


def _infer_target_dept(goal, capability_ids):
    kind = _infer_run_kind(goal, capability_ids)
    if kind in ('coding', 'system'):
        return '兵部'
    if kind == 'document' or kind == 'writing':
        return '礼部'
    if kind == 'web':
        return '工部'
    return '尚书省'


def _infer_priority(goal, requested_priority='auto'):
    requested = (requested_priority or 'auto').strip().lower()
    if requested in ('low', 'normal', 'high'):
        return requested, 'user'

    text = (goal or '').lower()
    if any(word in text for word in ['紧急', '加急', '立刻', '马上', '尽快', '救火', '阻塞', '卡住', '失败']):
        return 'high', 'inferred'
    if any(word in text for word in ['不急', '低优先', '有空', '以后', '慢慢']):
        return 'low', 'inferred'
    return 'normal', 'inferred'


def _infer_deliverable(goal, run_kind, capability_ids, mode):
    text = (goal or '').lower()
    if mode == 'plan':
        return '可审议的方案、风险边界和下一步执行建议'
    if any(word in text for word in ['ppt', 'pptx', '幻灯片']):
        return '可打开的 PPT 文件和生成说明'
    if any(word in text for word in ['excel', 'xlsx', '表格']):
        return '可打开的表格文件和数据说明'
    if any(word in text for word in ['word', 'docx', '文档']):
        return '可打开的文档文件和修改说明'
    if any(word in text for word in ['文章', '公众号', '文案']):
        return '文章草稿、修改说明和可追溯输出文件'
    if run_kind in ('coding', 'system'):
        return '代码补丁、验证结果和关键运行证据'
    if run_kind == 'web':
        return '页面检查结果、截图和必要的修复补丁'
    if 'artifact.outputs' in capability_ids:
        return '结果摘要、关键证据和可打开的输出文件'
    return '结果摘要、关键证据和后续建议'


def _infer_constraints(goal, risk_level, mode):
    text = (goal or '').lower()
    items = []
    if any(word in text for word in ['不联网', '不要联网', '离线']):
        items.append('不联网')
    if any(word in text for word in ['只读', '只分析', '不要修改', '别修改', '不要直接改', '不要直接修改', '先不要改']):
        items.append('先只读分析，不直接改动文件')
    if any(word in text for word in ['需要确认', '问我确认', '让我确认', '边做边问', '确认后']):
        items.append('关键节点等待用户确认')
    if mode == 'plan' and not any('不直接' in item for item in items):
        items.append('先审议 RunSpec，再决定是否执行')
    if risk_level == 'high':
        items.append('高风险步骤需要保留证据并进入审议')
    if not items:
        items.append('按最小可行路径执行，保留关键证据')
    return '；'.join(_ordered_unique(items))


def _intent_clarification(goal, capability_ids, run_kind, mode, deliverable_input='', constraints_input=''):
    text = (goal or '').strip().lower()
    cn_chars = re.findall(r'[\u4e00-\u9fff]', text)
    action_words = [
        '优化', '修改', '修复', '改造', '处理', '看看', '检查', '分析', '整理',
        '生成', '创建', '接入', '升级', '实现', '做一下', '搞一下',
    ]
    object_words = [
        '页面', 'ui', 'ux', '模型', '配置', 'opencode', 'open code', '接口', 'api',
        '任务', '调度', '太子', '分发', '链路', '文件', '代码', '仓库', '文档',
        'ppt', '表格', '文章', '输出', '报告', '看板', 'agent', 'harness',
    ]
    delivery_words = [
        '报告', '补丁', '文件', '截图', '表格', 'ppt', '文档', '代码', '页面',
        '方案', '验证', '结果', '链接',
    ]
    vague_words = ['这个', '那个', '这里', '那里', '一下', '东西', '问题', '优化一下', '改一下']

    has_action = any(word in text for word in action_words)
    has_object = any(word in text for word in object_words)
    has_delivery = bool(deliverable_input) or any(word in text for word in delivery_words)
    is_short = len(cn_chars) < 10 and len(text) < 24
    is_generic_run = run_kind == 'general' and set(capability_ids) <= {'governance.plan', 'runtime.opencode', 'artifact.outputs', 'file.workspace'}

    missing = []
    questions = []
    score = 92
    if is_short:
        missing.append('目标过短')
        questions.append('你希望我具体处理哪个对象或页面？')
        score -= 26
    if has_action and not has_object:
        missing.append('缺少对象')
        questions.append('这次要处理的是 UI、调度逻辑、模型配置，还是某个具体文件？')
        score -= 22
    if is_generic_run and not has_object:
        missing.append('能力边界不明')
        questions.append('需要我调用哪些能力：代码、浏览器、文件、文档，还是只做方案？')
        score -= 18
    if mode == 'execute' and not has_delivery and not deliverable_input:
        missing.append('交付形式未说明')
        questions.append('你希望最终交付补丁、报告、截图，还是可打开的文件？')
        score -= 10
    if any(word in text for word in vague_words) and not has_object:
        missing.append('指代不清')
        score -= 12
    if constraints_input:
        score += 4
    if deliverable_input:
        score += 4

    missing = _ordered_unique(missing)
    questions = _ordered_unique(questions)
    score = max(35, min(98, score))
    if score < 58:
        level = 'ambiguous'
    elif score < 78:
        level = 'needs_detail'
    else:
        level = 'clear'
    should_ask = level != 'clear'
    primary_question = questions[0] if questions else ''
    if should_ask and not primary_question:
        primary_question = '补一句目标对象或期望交付物就够。'
    quick_adds = []
    if '缺少对象' in missing or '指代不清' in missing:
        quick_adds.extend([
            {'label': '按当前 UI 页面', 'append': '处理当前 UI 页面'},
            {'label': '按调度链路', 'append': '检查任务调度和分发链路'},
            {'label': '按模型配置', 'append': '检查模型配置入口'},
        ])
    if '能力边界不明' in missing:
        quick_adds.extend([
            {'label': '需要代码能力', 'append': '需要读取和修改当前仓库代码'},
            {'label': '需要浏览器验证', 'append': '需要打开本地页面验证'},
        ])
    if '交付形式未说明' in missing:
        quick_adds.extend([
            {'label': '交付补丁和验证', 'append': '最终交付代码补丁和验证结果'},
            {'label': '交付分析报告', 'append': '最终交付分析报告'},
        ])
    deduped_adds = []
    seen_adds = set()
    for item in quick_adds:
        key = item.get('append')
        if key and key not in seen_adds:
            seen_adds.add(key)
            deduped_adds.append(item)
    return {
        'level': level,
        'score': score,
        'shouldAsk': should_ask,
        'missing': missing,
        'questions': questions[:2],
        'primaryQuestion': primary_question,
        'quickAdds': deduped_adds[:4],
        'summary': '目标清楚，可以生成 RunSpec' if not should_ask else '目标还可以再补一句，避免派错能力',
    }


def list_run_specs(limit=100):
    specs = atomic_json_read(_run_specs_file(), [])
    if not isinstance(specs, list):
        specs = []
    return {'ok': True, 'count': len(specs), 'runs': specs[:max(1, min(int(limit or 100), 500))]}


def _prepare_run_spec(payload, run_id='RUN-PREVIEW', task_id='', created_at='', persisted=False):
    if not isinstance(payload, dict):
        return {'ok': False, 'error': 'payload must be an object'}
    goal = str(payload.get('goal') or payload.get('title') or '').strip()
    if not goal:
        return {'ok': False, 'error': 'goal required'}
    if len(goal) < _MIN_TITLE_LEN:
        return {'ok': False, 'error': f'目标过短（{len(goal)}<{_MIN_TITLE_LEN}字），请写清楚要完成什么'}

    requested_mode = str(payload.get('mode') or 'auto').strip() or 'auto'
    if requested_mode not in ('auto', 'plan', 'execute', 'interactive'):
        requested_mode = 'auto'
    requested_priority = str(payload.get('priority') or 'auto').strip() or 'auto'
    explicit_caps = payload.get('capabilityIds') or payload.get('capabilities') or []
    if not isinstance(explicit_caps, list):
        explicit_caps = []
    capability_ids = _infer_required_capabilities(goal, [str(item) for item in explicit_caps])
    risk_level = str(payload.get('riskLevel') or '').strip() or _risk_level_for_run(goal, capability_ids)
    if risk_level not in ('low', 'medium', 'high'):
        risk_level = _risk_level_for_run(goal, capability_ids)
    mode, intent_reason = _infer_run_mode(goal, requested_mode)
    target_dept = str(payload.get('targetDept') or '').strip() or _infer_target_dept(goal, capability_ids)
    run_kind = _infer_run_kind(goal, capability_ids)
    priority, priority_source = _infer_priority(goal, requested_priority)
    deliverable_input = str(payload.get('deliverable') or '').strip()
    constraints_input = str(payload.get('constraints') or '').strip()
    deliverable = deliverable_input or _infer_deliverable(goal, run_kind, capability_ids, mode)
    constraints = constraints_input or _infer_constraints(goal, risk_level, mode)
    clarification = _intent_clarification(goal, capability_ids, run_kind, mode, deliverable_input, constraints_input)
    if requested_mode == 'auto' and mode == 'execute' and clarification.get('shouldAsk'):
        mode = 'interactive'
        intent_reason = '目标还不够明确，先等待最小补充'
        clarification['safetyMode'] = 'interactive'
        clarification['summary'] = '目标还可以再补一句，已切到边做边问，避免误派发'
        deliverable = deliverable_input or _infer_deliverable(goal, run_kind, capability_ids, mode)
        constraints = constraints_input or _infer_constraints(goal, risk_level, mode)
    governance = _governance_for_risk(risk_level, mode)
    capability_policies = _capability_policies_for_run(capability_ids)
    tool_policy = _tool_policy_for_run(capability_ids, risk_level, mode, capability_policies)
    profile = {
        'deliverable': {'value': deliverable, 'source': 'user' if deliverable_input else 'inferred'},
        'constraints': {'value': constraints, 'source': 'user' if constraints_input else 'inferred'},
        'priority': {'value': priority, 'requested': requested_priority, 'source': priority_source},
        'targetDept': {'value': target_dept, 'source': 'user' if str(payload.get('targetDept') or '').strip() else 'inferred'},
        'clarification': clarification,
    }
    title = re.split(r'\n+', goal, maxsplit=1)[0].strip()
    if len(title) > 100:
        title = title[:100] + '…'
    created_at = created_at or now_iso()

    run = {
        'id': run_id,
        'taskId': task_id,
        'title': title,
        'goal': goal,
        'mode': mode,
        'requestedMode': requested_mode,
        'intent': {
            'requestedMode': requested_mode,
            'mode': mode,
            'reason': intent_reason,
            'clarification': clarification,
        },
        'clarification': clarification,
        'status': (
            'preview' if not persisted
            else 'waiting_review' if mode == 'plan'
            else 'waiting_clarification' if mode == 'interactive'
            else 'created'
        ),
        'runKind': run_kind,
        'targetDept': target_dept,
        'priority': priority,
        'requestedPriority': requested_priority,
        'requiredCapabilities': capability_ids,
        'capabilityPolicies': capability_policies,
        'toolPolicy': tool_policy,
        'riskLevel': risk_level,
        'governance': governance,
        'constraints': constraints,
        'deliverable': deliverable,
        'profile': profile,
        'createdAt': created_at,
        'updatedAt': created_at,
    }
    return {'ok': True, 'run': run}


def preview_run_spec(payload):
    """Return the backend's RunSpec interpretation without creating a task."""
    return _prepare_run_spec(payload, persisted=False)


def create_run_spec(payload):
    """Create a first-class RunSpec, then map it to the existing edict task flow."""
    run_id = f"RUN-{datetime.datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    prepared = _prepare_run_spec(payload, run_id=run_id, created_at=now_iso(), persisted=True)
    if not prepared.get('ok'):
        return prepared

    run = prepared['run']
    mode = run['mode']
    requested_mode = run.get('requestedMode', 'auto')
    intent_reason = (run.get('intent') or {}).get('reason', '')
    capability_ids = run.get('requiredCapabilities') or []
    risk_level = run.get('riskLevel', 'low')
    governance = run.get('governance') or []
    target_dept = run.get('targetDept', '')
    run_kind = run.get('runKind', 'general')
    priority = run.get('priority', 'normal')
    profile = run.get('profile') or {}

    params = {
        'runId': run_id,
        'mode': mode,
        'requestedMode': requested_mode,
        'intentReason': intent_reason,
        'goal': run['goal'],
        'constraints': run.get('constraints', ''),
        'deliverable': run.get('deliverable', ''),
        'requestedPriority': run.get('requestedPriority', 'auto'),
        'profile': profile,
        'requiredCapabilities': capability_ids,
        'riskLevel': risk_level,
        'governance': governance,
        'runKind': run_kind,
        'source': 'command_center',
    }
    hold_for_review = mode in ('plan', 'interactive')
    task_result = handle_create_task(
        run['title'],
        org='太子',
        official='太子',
        priority=priority,
        template_id='agent-control-plane',
        params=params,
        target_dept=target_dept,
        auto_dispatch=not hold_for_review,
    )
    if not task_result.get('ok'):
        return task_result

    task_id = task_result.get('taskId', '')
    run['taskId'] = task_id

    DATA.mkdir(parents=True, exist_ok=True)
    atomic_json_update(_run_specs_file(), lambda items: [run] + (items if isinstance(items, list) else []), [])

    def _attach(task):
        if hold_for_review:
            task['state'] = 'Menxia'
            task['org'] = '门下省'
            task['official'] = '侍中'
            if mode == 'interactive':
                task['now'] = 'Interaction-first：RunSpec 已生成，等待最小补充或确认'
                trigger = 'interaction-first'
                first_remark = f'Interaction-first：目标已整理为 RunSpec {run_id}，但需要补充后再派发'
                second_remark = 'RunSpec 等待最小补充，暂不自动派发执行'
            else:
                task['now'] = 'Plan-first：RunSpec 已生成，等待审议后再决定是否派发执行'
                trigger = 'plan-first'
                first_remark = f'Plan-first：目标已整理为 RunSpec {run_id}'
                second_remark = 'RunSpec 进入审议，暂不自动派发执行'
            task.setdefault('flow_log', []).extend([
                {
                    'at': now_iso(),
                    'from': '太子',
                    'to': '中书省',
                    'remark': first_remark,
                },
                {
                    'at': now_iso(),
                    'from': '中书省',
                    'to': '门下省',
                    'remark': second_remark,
                },
            ])
            sched = _ensure_scheduler(task)
            sched['lastDispatchStatus'] = 'held'
            sched['lastDispatchTrigger'] = trigger
            sched['lastDispatchError'] = ''
            sched.pop('activeDispatchId', None)
            sched.pop('activeDispatchState', None)
            sched.pop('activeDispatchStartedAt', None)
            _scheduler_snapshot(task, f'{trigger}-hold')
        task['runSpecId'] = run_id
        task['runSpec'] = {
            'id': run_id,
            'mode': mode,
            'requestedMode': requested_mode,
            'intentReason': intent_reason,
            'profile': profile,
            'riskLevel': risk_level,
            'requiredCapabilities': capability_ids,
            'governance': governance,
        }
        meta = task.setdefault('sourceMeta', {})
        if isinstance(meta, dict):
            meta['runSpecId'] = run_id
            meta['commandCenter'] = True

    if task_id:
        modify_task(task_id, _attach)
        _append_runtime_event(
            'run.spec.created',
            task_id=task_id,
            payload={
                'runId': run_id,
                'mode': mode,
                'requestedMode': requested_mode,
                'intentReason': intent_reason,
                'profile': profile,
                'riskLevel': risk_level,
                'requiredCapabilities': capability_ids,
                'dispatchPolicy': 'hold_for_review' if mode == 'plan' else 'hold_for_clarification' if mode == 'interactive' else 'auto_dispatch',
            },
            evidence={'source': 'command_center'},
        )
    return {'ok': True, 'run': run, 'taskId': task_id, 'message': task_result.get('message', '')}


def _todo_progress(task):
    todos = task.get('todos') or []
    total = len(todos)
    completed = sum(1 for td in todos if td.get('status') == 'completed')
    return completed, total


def handle_review_action(task_id, action, comment=''):
    """门下省御批：准奏/封驳。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    if task.get('state') not in ('Review', 'Menxia'):
        return {'ok': False, 'error': f'任务 {task_id} 当前状态为 {task.get("state")}，无法御批'}

    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'review-before-{action}')

    if action == 'approve':
        if task['state'] == 'Menxia':
            task['state'] = 'Assigned'
            task['now'] = '门下省准奏，移交尚书省派发'
            remark = f'✅ 准奏：{comment or "门下省审议通过"}'
            to_dept = '尚书省'
        else:  # Review
            completed, total = _todo_progress(task)
            if total > 0 and completed < total:
                return {'ok': False, 'error': f'子任务尚未全部完成（{completed}/{total}），不能直接准奏完结'}
            task['state'] = 'Done'
            task['now'] = '御批通过，任务完成'
            remark = f'✅ 御批准奏：{comment or "审查通过"}'
            to_dept = '皇上'
    elif action == 'reject':
        round_num = (task.get('review_round') or 0) + 1
        task['review_round'] = round_num
        task['state'] = 'Zhongshu'
        task['now'] = f'封驳退回中书省修订（第{round_num}轮）'
        remark = f'🚫 封驳：{comment or "需要修改"}'
        to_dept = '中书省'
    else:
        return {'ok': False, 'error': f'未知操作: {action}'}

    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '门下省' if task.get('state') != 'Done' else '皇上',
        'to': to_dept,
        'remark': remark
    })
    _scheduler_mark_progress(task, f'审议动作 {action} -> {task.get("state")}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    # 🚀 审批后自动派发对应 Agent
    new_state = task['state']
    if new_state not in ('Done',):
        dispatch_for_state(task_id, task, new_state)

    label = '已准奏' if action == 'approve' else '已封驳'
    dispatched = ' (已自动派发 Agent)' if new_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {label}{dispatched}'}


# ══ Agent 在线状态检测 ══

_AGENT_DEPTS = [
    {'id':'taizi',   'label':'太子',  'emoji':'🤴', 'role':'太子',     'rank':'储君'},
    {'id':'zhongshu','label':'中书省','emoji':'📜', 'role':'中书令',   'rank':'正一品'},
    {'id':'menxia',  'label':'门下省','emoji':'🔍', 'role':'侍中',     'rank':'正一品'},
    {'id':'shangshu','label':'尚书省','emoji':'📮', 'role':'尚书令',   'rank':'正一品'},
    {'id':'hubu',    'label':'户部',  'emoji':'💰', 'role':'户部尚书', 'rank':'正二品'},
    {'id':'libu',    'label':'礼部',  'emoji':'📝', 'role':'礼部尚书', 'rank':'正二品'},
    {'id':'bingbu',  'label':'兵部',  'emoji':'⚔️', 'role':'兵部尚书', 'rank':'正二品'},
    {'id':'xingbu',  'label':'刑部',  'emoji':'⚖️', 'role':'刑部尚书', 'rank':'正二品'},
    {'id':'gongbu',  'label':'工部',  'emoji':'🔧', 'role':'工部尚书', 'rank':'正二品'},
    {'id':'libu_hr', 'label':'吏部',  'emoji':'👔', 'role':'吏部尚书', 'rank':'正二品'},
    {'id':'zaochao', 'label':'钦天监','emoji':'📰', 'role':'朝报官',   'rank':'正三品'},
]


def _agent_runtime():
    """Return the active agent runtime: openclaw (default) or opencode."""
    raw = (os.environ.get('EDICT_AGENT_RUNTIME') or os.environ.get('EDICT_RUNTIME') or 'openclaw').strip().lower()
    normalized = raw.replace('-', '').replace('_', '').replace(' ', '')
    if normalized in ('opencode', 'opencold'):
        return 'opencode'
    return 'openclaw'


def _runtime_label():
    return 'OpenCode' if _agent_runtime() == 'opencode' else 'OpenClaw Gateway'


def _opencode_server_url():
    return os.environ.get('OPENCODE_SERVER_URL', 'http://127.0.0.1:4096').strip().rstrip('/')


def _opencode_model(agent_id=''):
    """Return the OpenCode model for an agent, falling back to the default.

    In OpenCode mode ``OPENCODE_MODEL`` is only the process default.  The
    dashboard model picker writes per-agent model choices into
    ``data/agent_config.json`` / ``opencode.json``; dispatch must honor those
    before falling back to the global environment value.
    """
    if agent_id:
        cfg = read_json(DATA / 'agent_config.json', {})
        for ag in cfg.get('agents', []) if isinstance(cfg, dict) else []:
            if ag.get('id') == agent_id and ag.get('model'):
                return str(ag.get('model')).strip()
        ocfg = read_json(BASE.parent / 'opencode.json', {})
        agent_cfg = (ocfg.get('agent') or {}).get(agent_id, {}) if isinstance(ocfg, dict) else {}
        if isinstance(agent_cfg, dict) and agent_cfg.get('model'):
            return str(agent_cfg.get('model')).strip()
    return (
        os.environ.get('OPENCODE_MODEL', '').strip()
        or os.environ.get('OPENCODE_DEFAULT_MODEL', '').strip()
        or read_json(BASE.parent / 'opencode.json', {}).get('model', '')
        or 'opencode/deepseek-v4-flash-free'
    )


def _resolve_opencode_bin():
    configured = os.environ.get('OPENCODE_BIN', '').strip()
    if configured:
        return configured
    return shutil.which('opencode')


def _check_opencode_probe():
    """Probe the OpenCode headless server."""
    base_url = _opencode_server_url()
    basic_alive = False
    for path in ('/doc', '/'):
        try:
            req = Request(f'{base_url}{path}', headers={'Accept': 'application/json'})
            resp = urlopen(req, timeout=3)
            if 200 <= resp.status < 500:
                basic_alive = True
                break
        except Exception:
            continue
    if not basic_alive:
        return False
    if (BASE.parent / 'opencode.json').exists():
        return {'taizi', 'zhongshu', 'shangshu'}.issubset(_opencode_agent_names())
    return True


def _opencode_agent_names():
    try:
        query = urlencode({'directory': str(BASE.parent)})
        req = Request(f'{_opencode_server_url()}/agent?{query}', headers={'Accept': 'application/json'})
        data = json.loads(urlopen(req, timeout=3).read().decode('utf-8'))
        if isinstance(data, list):
            return {str(item.get('name', '')) for item in data if item.get('name')}
    except Exception:
        pass
    return set()


def _opencode_session_probe(agent_id='taizi'):
    """Verify that the OpenCode server can create/delete a session.

    `/doc` and `/agent` can stay healthy while the session registry is stale.
    A create/delete probe catches the "Session not found" failure before a real
    dispatch spends retries on a broken server.
    """
    session_id = ''
    try:
        query = urlencode({'directory': str(BASE.parent)})
        body = json.dumps({
            'title': 'edict-session-probe',
            'agent': agent_id or 'taizi',
        }).encode('utf-8')
        req = Request(
            f'{_opencode_server_url()}/session?{query}',
            data=body,
            method='POST',
            headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
        )
        data = json.loads(urlopen(req, timeout=5).read().decode('utf-8'))
        session_id = str(data.get('id') or '')
        return session_id.startswith('ses_') or session_id.startswith('ses')
    except Exception as exc:
        log.warning(f'OpenCode session probe failed: {exc}')
        return False
    finally:
        if session_id:
            try:
                query = urlencode({'directory': str(BASE.parent)})
                req = Request(
                    f'{_opencode_server_url()}/session/{quote(session_id)}?{query}',
                    method='DELETE',
                    headers={'Accept': 'application/json'},
                )
                urlopen(req, timeout=3).read()
            except Exception:
                pass


def _clean_runtime_error(text, limit=500):
    cleaned = _ANSI_RE.sub('', str(text or ''))
    cleaned = ''.join(ch for ch in cleaned if ch == '\n' or ch == '\t' or ord(ch) >= 32)
    cleaned = cleaned.strip()
    return cleaned[:limit]


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _runtime_error_from_obj(obj):
    if not isinstance(obj, dict):
        return ''
    for container in (obj, _dict(obj.get('info')), _dict(obj.get('part')), _dict(obj.get('data'))):
        err = container.get('error')
        if not err:
            continue
        if isinstance(err, dict):
            detail = _dict(err.get('data'))
            return str(detail.get('message') or err.get('message') or err.get('name') or err)
        return str(err)
    part = _dict(obj.get('part'))
    state = _dict(part.get('state'))
    if state.get('error'):
        return str(state.get('error'))
    for key in ('message', 'reason'):
        val = obj.get(key)
        if isinstance(val, str) and val.strip() and obj.get('type') in {'error', 'failed'}:
            return val.strip()
    return ''


def _looks_like_runtime_event_line(text):
    if not isinstance(text, str):
        return False
    sample = text.strip()
    if not sample.startswith('{') or '"type"' not in sample:
        return False
    event_markers = (
        'step_start',
        'step-start',
        'step_finish',
        'step-finish',
        'message_updated',
        'part_updated',
        '"sessionID"',
        '"messageID"',
    )
    return any(marker in sample for marker in event_markers)


def _runtime_error_summary(text, default='runtime command failed', limit=500):
    cleaned = _clean_runtime_error(text, limit=20_000)
    if not cleaned:
        return default[:limit]
    json_line_count = 0
    event_line_count = 0
    non_json_lines = []
    for line in cleaned.splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except Exception:
            if _looks_like_runtime_event_line(raw):
                event_line_count += 1
                continue
            non_json_lines.append(raw)
            continue
        json_line_count += 1
        err = _runtime_error_from_obj(item)
        if err:
            return _clean_runtime_error(err, limit=limit)
    if (json_line_count or event_line_count) and not non_json_lines:
        return default[:limit]
    return _clean_runtime_error('\n'.join(non_json_lines) or cleaned, limit=limit)


def _run_capture_timeout(cmd, *, timeout, env=None, cwd=None):
    """Run a CLI command and reliably reap its process group on timeout."""
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name != 'nt'),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as exc:
        if os.name != 'nt':
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                pass
        else:
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            stdout, stderr = proc.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name != 'nt':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
            else:
                try:
                    proc.kill()
                except Exception:
                    pass
            stdout, stderr = proc.communicate()
        exc.output = stdout
        exc.stderr = stderr
        raise exc


def _is_opencode_session_not_found(error_text):
    return 'Session not found' in _clean_runtime_error(error_text, limit=1000)


def _restart_opencode_server():
    """Restart the project OpenCode server after a stale session failure."""
    opencode_bin = _resolve_opencode_bin()
    if not opencode_bin:
        return False
    parsed = urlparse(_opencode_server_url())
    host = parsed.hostname or '127.0.0.1'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if host not in ('127.0.0.1', 'localhost', '::1'):
        log.warning(f'OpenCode 自动重启跳过：非本机地址 {host}')
        return False
    try:
        subprocess.run(['screen', '-S', 'edict-opencode', '-X', 'quit'], capture_output=True, timeout=3)
    except Exception:
        pass
    try:
        pids = subprocess.run(
            ['lsof', f'-tiTCP:{port}', '-sTCP:LISTEN'],
            capture_output=True,
            text=True,
            timeout=3,
        )
        for raw_pid in pids.stdout.splitlines():
            pid = raw_pid.strip()
            if not pid.isdigit():
                continue
            cmd = subprocess.run(['ps', '-p', pid, '-o', 'command='], capture_output=True, text=True, timeout=3)
            if 'opencode' in (cmd.stdout or ''):
                try:
                    os.kill(int(pid), 15)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        import time as _time
        _time.sleep(1)
        log_dir = PROJECT_ROOT / 'logs'
        pid_dir = PROJECT_ROOT / '.pids'
        log_dir.mkdir(parents=True, exist_ok=True)
        pid_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / 'opencode.log', 'ab')
        proc = subprocess.Popen(
            [opencode_bin, 'serve', '--hostname', host, '--port', str(port)],
            cwd=str(PROJECT_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            (pid_dir / 'opencode.pid').write_text(str(proc.pid), encoding='utf-8')
        except Exception:
            pass
        for _ in range(20):
            if _check_opencode_probe() and _opencode_session_probe():
                return True
            _time.sleep(0.5)
    except Exception as exc:
        log.warning(f'OpenCode 自动重启失败: {exc}')
    return False


def _opencode_config_has_agent(agent_id):
    cfg_path = BASE.parent / 'opencode.json'
    prompt_path = BASE.parent / '.opencode' / 'prompts' / f'{agent_id}.md'
    if not cfg_path.exists() or not prompt_path.exists():
        return False
    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
        return agent_id in (cfg.get('agent') or {})
    except Exception:
        return False


def _get_opencode_agent_session_status(agent_id):
    """Best-effort OpenCode session activity status."""
    try:
        query = urlencode({'directory': str(BASE.parent)})
        req = Request(f'{_opencode_server_url()}/session?{query}', headers={'Accept': 'application/json'})
        data = json.loads(urlopen(req, timeout=3).read().decode('utf-8'))
    except Exception:
        return 0, 0, False

    if not isinstance(data, list):
        return 0, 0, False

    session_count = 0
    last_ts = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get('agent') and item.get('agent') != agent_id:
            continue
        session_count += 1
        ts = item.get('updatedAt') or item.get('time') or item.get('createdAt') or 0
        if isinstance(ts, str):
            try:
                dt = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
                ts = int(dt.timestamp() * 1000)
            except Exception:
                ts = 0
        elif isinstance(ts, (int, float)) and ts < 10_000_000_000:
            ts = int(ts * 1000)
        if isinstance(ts, (int, float)) and ts > last_ts:
            last_ts = int(ts)

    now_ms = int(datetime.datetime.now().timestamp() * 1000)
    is_busy = bool(last_ts and now_ms - last_ts <= 2 * 60 * 1000)
    return last_ts, session_count, is_busy


def _check_gateway_alive():
    """检测 Gateway 是否在运行。

    Windows 上不要依赖 pgrep；优先通过本地端口探测判断。
    """
    if _agent_runtime() == 'opencode':
        return _check_opencode_probe()
    if _check_gateway_probe():
        return True
    try:
        if os.name == 'nt':
            with socket.create_connection(('127.0.0.1', 18789), timeout=2):
                return True
            return False
        result = subprocess.run(['pgrep', '-f', 'openclaw-gateway'],
                                capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _check_gateway_probe():
    """通过 HTTP probe 检测 Gateway 是否响应。"""
    if _agent_runtime() == 'opencode':
        return _check_opencode_probe()
    for url in ('http://127.0.0.1:18789/', 'http://127.0.0.1:18789/healthz'):
        try:
            from urllib.request import urlopen
            resp = urlopen(url, timeout=3)
            if 200 <= resp.status < 500:
                return True
        except Exception:
            continue
    return False


def _get_agent_session_status(agent_id):
    """读取 Agent 的 sessions.json 获取活跃状态。
    返回: (last_active_ts_ms, session_count, is_busy)
    """
    if _agent_runtime() == 'opencode':
        return _get_opencode_agent_session_status(agent_id)
    sessions_file = OCLAW_HOME / 'agents' / agent_id / 'sessions' / 'sessions.json'
    if not sessions_file.exists():
        return 0, 0, False
    try:
        data = json.loads(sessions_file.read_text())
        if not isinstance(data, dict):
            return 0, 0, False
        session_count = len(data)
        last_ts = 0
        for v in data.values():
            ts = v.get('updatedAt', 0)
            if isinstance(ts, (int, float)) and ts > last_ts:
                last_ts = ts
        now_ms = int(datetime.datetime.now().timestamp() * 1000)
        age_ms = now_ms - last_ts if last_ts else 9999999999
        is_busy = age_ms <= 2 * 60 * 1000  # 2分钟内视为正在工作
        return last_ts, session_count, is_busy
    except Exception:
        return 0, 0, False


def _check_agent_process(agent_id):
    """检测是否有该 Agent 的独立运行进程。

    OpenCode 的 headless server 是共享服务，不会为每个 agent 常驻一个
    可被 pgrep 识别的进程；是否“正在工作”由最近 session 活动判断。
    """
    if _agent_runtime() == 'opencode':
        return False
    try:
        result = subprocess.run(
            ['pgrep', '-f', f'openclaw.*--agent.*{agent_id}'],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def _check_agent_workspace(agent_id):
    """检查 Agent 工作空间是否存在。"""
    if _agent_runtime() == 'opencode':
        return _opencode_config_has_agent(agent_id)
    ws = OCLAW_HOME / f'workspace-{agent_id}'
    return ws.is_dir()


def get_agents_status():
    """获取所有 Agent 的在线状态。
    返回各 Agent 的:
    - status: 'running' | 'idle' | 'offline' | 'unconfigured'
    - lastActive: 最后活跃时间
    - sessions: 会话数
    - hasWorkspace: 工作空间是否存在
    - processAlive: 是否有进程在运行
    """
    runtime = _agent_runtime()
    runtime_label = _runtime_label()
    opencode_names = _opencode_agent_names() if runtime == 'opencode' else set()
    gateway_alive = _check_gateway_alive()
    gateway_probe = _check_gateway_probe() if gateway_alive else False

    agents = []
    seen_ids = set()
    for dept in _AGENT_DEPTS:
        aid = dept['id']
        if aid in seen_ids:
            continue
        seen_ids.add(aid)

        has_workspace = _check_agent_workspace(aid) or (aid in opencode_names)
        last_ts, sess_count, is_busy = _get_agent_session_status(aid)
        process_alive = _check_agent_process(aid)

        # 状态判定
        if not has_workspace:
            status = 'unconfigured'
            status_label = '❌ 未配置'
        elif not gateway_alive:
            status = 'offline'
            status_label = f'🔴 {runtime_label} 离线'
        elif process_alive or is_busy:
            status = 'running'
            status_label = '🟢 运行中'
        elif last_ts > 0:
            now_ms = int(datetime.datetime.now().timestamp() * 1000)
            age_ms = now_ms - last_ts
            if age_ms <= 10 * 60 * 1000:  # 10分钟内
                status = 'idle'
                status_label = '🟡 待命'
            elif age_ms <= 3600 * 1000:  # 1小时内
                status = 'idle'
                status_label = '⚪ 空闲'
            else:
                status = 'idle'
                status_label = '⚪ 休眠'
        else:
            status = 'idle'
            status_label = '🟡 待命' if runtime == 'opencode' else '⚪ 无记录'

        # 格式化最后活跃时间
        last_active_str = None
        if last_ts > 0:
            try:
                last_active_str = datetime.datetime.fromtimestamp(
                    last_ts / 1000
                ).strftime('%m-%d %H:%M')
            except Exception:
                pass

        agents.append({
            'id': aid,
            'label': dept['label'],
            'emoji': dept['emoji'],
            'role': dept['role'],
            'status': status,
            'statusLabel': status_label,
            'lastActive': last_active_str,
            'lastActiveTs': last_ts,
            'sessions': sess_count,
            'hasWorkspace': has_workspace,
            'processAlive': process_alive,
        })

    return {
        'ok': True,
        'gateway': {
            'alive': gateway_alive,
            'probe': gateway_probe,
            'runtime': runtime,
            'label': runtime_label,
            'status': '🟢 运行中' if gateway_probe else (f'🟡 {runtime_label} 进程在但无响应' if gateway_alive else f'🔴 {runtime_label} 未启动'),
        },
        'agents': agents,
        'checkedAt': now_iso(),
    }


def wake_agent(agent_id, message=''):
    """唤醒指定 Agent，发送一条心跳/唤醒消息。"""
    if not _SAFE_NAME_RE.match(agent_id):
        return {'ok': False, 'error': f'agent_id 非法: {agent_id}'}
    if not _check_agent_workspace(agent_id):
        return {'ok': False, 'error': f'{agent_id} 工作空间不存在，请先配置'}
    if not _check_gateway_alive():
        return {'ok': False, 'error': f'{_runtime_label()} 未启动，请先启动运行时服务'}

    # agent_id 直接作为 runtime_id（openclaw agents list 中的注册名）
    runtime_id = agent_id
    msg = message or f'🔔 系统心跳检测 — 请回复 OK 确认在线。当前时间: {now_iso()}'
    runtime = _agent_runtime()
    if runtime == 'opencode':
        runtime_bin = _resolve_opencode_bin()
        if not runtime_bin:
            return {'ok': False, 'error': 'OpenCode CLI 未找到：请安装 opencode 或设置 OPENCODE_BIN'}
    else:
        runtime_bin = _resolve_openclaw_bin()
        if not runtime_bin:
            return {'ok': False, 'error': 'OpenClaw CLI 未找到：请安装 openclaw 或设置 OPENCLAW_BIN'}

    def do_wake():
        try:
            if runtime == 'opencode':
                cmd = [
                    runtime_bin, 'run',
                    '--attach', _opencode_server_url(),
                    '--dir', str(BASE.parent),
                    '--agent', runtime_id,
                    '--format', 'json',
                    '--title', f'wake-{runtime_id}',
                ]
                model = _opencode_model(agent_id)
                if model:
                    cmd.extend(['--model', model])
                cmd.append(msg)
            else:
                cmd = [runtime_bin, 'agent', '--agent', runtime_id, '-m', msg, '--timeout', '120']
            log.info(f'🔔 唤醒 {agent_id}...')
            # 带重试（最多2次）
            for attempt in range(1, 3):
                _append_runtime_event('agent_message_sent', '', agent_id, {
                    'from': 'dashboard',
                    'to': agent_id,
                    'message': msg,
                    'type': 'wake',
                    'status': 'started',
                    'attempt': attempt,
                })
                result = _run_capture_timeout(cmd, timeout=130)
                if result.returncode == 0:
                    log.info(f'✅ {agent_id} 已唤醒')
                    _append_runtime_event('agent_message_done', '', agent_id, {
                        'from': agent_id,
                        'to': 'dashboard',
                        'summary': 'wake command accepted',
                        'status': 'success',
                        'attempt': attempt,
                    })
                    return
                err_msg = _runtime_error_summary(
                    result.stderr if result.stderr else result.stdout,
                    default='wake command failed',
                    limit=200,
                )
                log.warning(f'⚠️ {agent_id} 唤醒失败(第{attempt}次): {err_msg}')
                if attempt < 2:
                    import time
                    time.sleep(5)
            log.error(f'❌ {agent_id} 唤醒最终失败')
            _append_runtime_event('agent_message_failed', '', agent_id, {
                'from': 'dashboard',
                'to': agent_id,
                'error': err_msg if 'err_msg' in locals() else 'wake failed after retries',
                'status': 'failed',
            }, confidence='low')
        except subprocess.TimeoutExpired as exc:
            timeout_error = _runtime_error_summary(
                getattr(exc, 'stderr', '') or getattr(exc, 'output', ''),
                default=f'{_runtime_label()} 唤醒超时（{agent_id}）',
                limit=300,
            )
            log.error(f'❌ {agent_id} 唤醒超时(130s)')
            _append_runtime_event('agent_message_failed', '', agent_id, {
                'from': 'dashboard',
                'to': agent_id,
                'error': timeout_error,
                'status': 'timeout',
            }, confidence='low')
        except Exception as e:
            log.warning(f'⚠️ {agent_id} 唤醒异常: {e}')
            _append_runtime_event('agent_message_failed', '', agent_id, {
                'from': 'dashboard',
                'to': agent_id,
                'error': str(e)[:200],
                'status': 'error',
            }, confidence='low')
    threading.Thread(target=do_wake, daemon=True).start()

    return {'ok': True, 'message': f'{agent_id} 唤醒指令已发出，约10-30秒后生效'}


# ══ Agent 实时活动读取 ══

# 状态 → agent_id 映射
_STATE_AGENT_MAP = {
    'Taizi': 'taizi',
    'Zhongshu': 'zhongshu',
    'Menxia': 'menxia',
    'Assigned': 'shangshu',
    'Doing': None,         # 六部，需从 org 推断
    'Review': 'shangshu',
    'Next': None,          # 待执行，从 org 推断
    'Pending': 'zhongshu', # 待处理，默认中书省
}
_ORG_AGENT_MAP = {
    '礼部': 'libu', '户部': 'hubu', '兵部': 'bingbu',
    '刑部': 'xingbu', '工部': 'gongbu', '吏部': 'libu_hr',
    '中书省': 'zhongshu', '门下省': 'menxia', '尚书省': 'shangshu',
}
_BASE_AGENT_IDS = {
    agent_id for agent_id in list(_STATE_AGENT_MAP.values()) + list(_ORG_AGENT_MAP.values())
    if agent_id
}
_ACTIVITY_UI_LIMIT = 240

_TERMINAL_STATES = {'Done', 'Cancelled'}


def _known_agent_ids():
    ids = set(_BASE_AGENT_IDS)
    cfg = read_json(DATA / 'agent_config.json', {})
    for agent in cfg.get('agents', []) if isinstance(cfg, dict) else []:
        if isinstance(agent, dict) and agent.get('id'):
            ids.add(str(agent.get('id')))
    return ids


def _normalize_related_agents(values):
    known = _known_agent_ids()
    return sorted({str(v) for v in values if str(v) in known})


def _expected_agent_for_task(task, state=None):
    """Resolve the deterministic runtime owner for a task state."""
    state = state or task.get('state', '')
    if state == 'PendingConfirm':
        pending = task.get('pending_confirm') or {}
        if pending.get('confirm_by'):
            return pending.get('confirm_by')
    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        org = task.get('targetDept') or task.get('org', '')
        agent_id = _ORG_AGENT_MAP.get(org)
    return agent_id


def _parse_iso(ts):
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _ensure_scheduler(task):
    _ensure_trace_id(task)
    sched = task.setdefault('_scheduler', {})
    if not isinstance(sched, dict):
        sched = {}
        task['_scheduler'] = sched
    sched.setdefault('enabled', True)
    sched.setdefault('stallThresholdSec', 600)
    sched.setdefault('maxRetry', 2)
    sched.setdefault('retryCount', 0)
    sched.setdefault('escalationLevel', 0)
    sched.setdefault('autoRollback', True)
    if not sched.get('lastProgressAt'):
        sched['lastProgressAt'] = task.get('updatedAt') or now_iso()
    if 'stallSince' not in sched:
        sched['stallSince'] = None
    if 'lastDispatchStatus' not in sched:
        sched['lastDispatchStatus'] = 'idle'
    if 'snapshot' not in sched:
        sched['snapshot'] = {
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'now': task.get('now', ''),
            'savedAt': now_iso(),
            'note': 'init',
        }
    return sched


def _scheduler_add_flow(task, remark, to=''):
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': '太子调度',
        'to': to or task.get('org', ''),
        'remark': f'🧭 {remark}'
    })


def _scheduler_snapshot(task, note=''):
    sched = _ensure_scheduler(task)
    sched['snapshot'] = {
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'now': task.get('now', ''),
        'savedAt': now_iso(),
        'note': note or 'snapshot',
    }


def _scheduler_mark_progress(task, note=''):
    sched = _ensure_scheduler(task)
    sched['lastProgressAt'] = now_iso()
    sched['stallSince'] = None
    sched['retryCount'] = 0
    sched['escalationLevel'] = 0
    sched['rollbackCount'] = 0
    sched['lastEscalatedAt'] = None
    if note:
        _scheduler_add_flow(task, f'进展确认：{note}')


def _resolve_openclaw_bin():
    """Return the OpenClaw CLI path used by dashboard dispatch.

    On Windows, npm-installed CLIs are commonly exposed as .cmd shims.  Using
    shutil.which lets Python resolve that shim before subprocess runs.
    """
    configured = os.environ.get('OPENCLAW_BIN', '').strip()
    if configured:
        return configured
    return shutil.which('openclaw')


def _update_task_scheduler(task_id, updater):
    """Atomically update a task's scheduler state.

    Uses ``modify_task`` to hold the file lock for the entire
    read-modify-write cycle, preventing concurrent dispatch threads and
    the periodic scanner from clobbering each other's writes.
    """
    def _apply(task):
        sched = _ensure_scheduler(task)
        updater(task, sched)

    return modify_task(task_id, _apply)


def _scheduler_dispatch_is_current(task, sched, dispatch_id, dispatch_state, agent_id):
    """Return True only while this background dispatch is still the active one."""
    return (
        sched.get('activeDispatchId') == dispatch_id
        and sched.get('activeDispatchState') == dispatch_state
        and sched.get('lastDispatchAgent') == agent_id
        and task.get('state') == dispatch_state
    )


def _duration_text(seconds):
    try:
        sec = max(0, int(seconds or 0))
    except Exception:
        sec = 0
    if sec < 60:
        return f'{sec}秒'
    if sec < 3600:
        return f'{sec // 60}分{sec % 60}秒'
    if sec < 86400:
        h, rem = divmod(sec, 3600)
        return f'{h}小时{rem // 60}分'
    d, rem = divmod(sec, 86400)
    return f'{d}天{rem // 3600}小时'


def _outbox_item_age(item, now_dt=None):
    created = _parse_iso(item.get('createdAt') or item.get('updatedAt'))
    if not created:
        return 0
    now_dt = now_dt or datetime.datetime.now(datetime.timezone.utc)
    return max(0, int((now_dt - created).total_seconds()))


def _public_outbox_item(item, task_map=None, now_dt=None):
    task_map = task_map or {}
    task = task_map.get(item.get('taskId', ''), {})
    age_sec = _outbox_item_age(item, now_dt)
    return {
        'id': item.get('id', ''),
        'kind': item.get('kind', ''),
        'status': item.get('status', 'unknown'),
        'taskId': item.get('taskId', ''),
        'taskTitle': task.get('title', ''),
        'taskState': task.get('state', item.get('state', '')),
        'state': item.get('state', ''),
        'agentId': item.get('agentId', ''),
        'trigger': item.get('trigger', ''),
        'traceId': item.get('traceId', ''),
        'attempts': int(item.get('attempts') or 0),
        'maxAttempts': int(item.get('maxAttempts') or 0),
        'createdAt': item.get('createdAt', ''),
        'updatedAt': item.get('updatedAt', ''),
        'claimedAt': item.get('claimedAt', ''),
        'finishedAt': item.get('finishedAt', ''),
        'lastError': _runtime_error_summary(
            item.get('lastError', ''),
            default='运行时返回了事件流，未给出明确错误',
            limit=300,
        ) if item.get('lastError') else '',
        'result': item.get('result') if isinstance(item.get('result'), dict) else {},
        'ageSec': age_sec,
        'ageText': _duration_text(age_sec),
    }


def get_runtime_outbox_health(limit=8):
    """Summarize local durable runtime queue for the dashboard."""
    try:
        limit = max(1, min(int(limit or 8), 50))
    except Exception:
        limit = 8
    items = _outbox_list(limit=1000)
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    counts = {}
    for item in items:
        status = item.get('status', 'unknown')
        counts[status] = counts.get(status, 0) + 1

    unfinished = [x for x in items if x.get('status') in {'pending', 'running'}]
    failed = [x for x in items if x.get('status') == 'failed']
    oldest_pending = min(
        (_outbox_item_age(x, now_dt) for x in unfinished),
        default=0,
    )
    task_map = {t.get('id', ''): t for t in load_tasks()}
    failed_sorted = sorted(failed, key=lambda x: x.get('updatedAt') or x.get('createdAt') or '', reverse=True)
    active_sorted = sorted(unfinished, key=lambda x: x.get('createdAt') or x.get('updatedAt') or '')
    dead_letters = failed_sorted[:limit]
    latest = max(items, key=lambda x: x.get('updatedAt') or x.get('createdAt') or '', default={})
    return {
        'ok': True,
        'checkedAt': now_iso(),
        'worker': {
            'active': bool(_DISPATCH_WORKER_ACTIVE),
            'workerId': _DISPATCH_WORKER_ID,
        },
        'counts': counts,
        'total': len(items),
        'pending': counts.get('pending', 0),
        'running': counts.get('running', 0),
        'failed': counts.get('failed', 0),
        'archived': counts.get('archived', 0),
        'done': counts.get('done', 0),
        'oldestPendingAgeSec': oldest_pending,
        'oldestPendingAgeText': _duration_text(oldest_pending),
        'latest': _public_outbox_item(latest, task_map, now_dt) if latest else {},
        'activeItems': [_public_outbox_item(x, task_map, now_dt) for x in active_sorted[:limit]],
        'deadLetters': [_public_outbox_item(x, task_map, now_dt) for x in dead_letters],
        'deadLetterWindow': {
            'total': len(failed_sorted),
            'returned': len(dead_letters),
            'truncated': len(failed_sorted) > len(dead_letters),
        },
    }


def handle_runtime_outbox_retry(item_id, reason=''):
    if not item_id:
        return {'ok': False, 'error': 'itemId required'}
    result = _outbox_requeue_failed(item_id, reason or 'manual retry from dashboard')
    if not result.get('ok'):
        return result
    item = result.get('item') or {}
    _append_runtime_event('outbox_requeued', item.get('taskId', ''), item.get('agentId', ''), {
        'outboxId': item.get('id', ''),
        'kind': item.get('kind', ''),
        'trigger': item.get('trigger', ''),
        'reason': reason or 'manual retry from dashboard',
        'remark': '失败派发已重新入队',
    }, trace_id=item.get('traceId', ''))
    _kick_dispatch_worker()
    return {'ok': True, 'message': '已重新入队', 'item': _public_outbox_item(item, {item.get('taskId', ''): next((t for t in load_tasks() if t.get('id') == item.get('taskId')), {})})}


def handle_runtime_outbox_archive(item_id='', archive_all_failed=False, task_id='', reason=''):
    if not item_id and not archive_all_failed and not task_id:
        return {'ok': False, 'error': 'itemId, taskId or archiveAllFailed required'}
    result = _outbox_archive_failed(
        item_id,
        task_id=task_id if not item_id else '',
        reason=reason or 'dashboard dead-letter archive',
    )
    if not result.get('ok'):
        return result
    count = int(result.get('count') or 0)
    first = (result.get('items') or [{}])[0] if isinstance(result.get('items'), list) else {}
    _append_runtime_event('outbox_archived', first.get('taskId', '') or task_id, first.get('agentId', ''), {
        'outboxId': item_id,
        'taskId': task_id,
        'archiveAllFailed': bool(archive_all_failed),
        'count': count,
        'reason': reason or 'dashboard dead-letter archive',
        'remark': f'已归档 {count} 条失败派发',
    }, trace_id=first.get('traceId', ''))
    return {'ok': True, 'message': f'已归档 {count} 条失败派发', 'count': count}


def get_scheduler_state(task_id):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    trace_id = _ensure_trace_id(task)
    last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    stalled_sec = 0
    if last_progress:
        stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))
    return {
        'ok': True,
        'taskId': task_id,
        'state': task.get('state', ''),
        'org': task.get('org', ''),
        'traceId': trace_id,
        'expectedAgent': _expected_agent_for_task(task),
        'outbox': _outbox_task_summary(task_id),
        'scheduler': sched,
        'stalledSec': stalled_sec,
        'checkedAt': now_iso(),
    }


def handle_scheduler_retry(task_id, reason=''):
    # Pre-check before acquiring lock (avoids holding lock for error paths)
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES or state == 'Blocked':
        return {'ok': False, 'error': f'任务 {task_id} 当前状态 {state} 不支持重试'}

    result = {'retryCount': 0, 'state': state}

    def _apply(task):
        cur = task.get('state', '')
        if cur in _TERMINAL_STATES or cur == 'Blocked':
            return  # state changed between pre-check and lock; skip
        sched = _ensure_scheduler(task)
        sched['retryCount'] = int(sched.get('retryCount') or 0) + 1
        sched['lastRetryAt'] = now_iso()
        sched['lastDispatchTrigger'] = 'taizi-retry'
        _scheduler_add_flow(task, f'触发重试第{sched["retryCount"]}次：{reason or "超时未推进"}')
        result['retryCount'] = sched['retryCount']
        result['state'] = cur

    modify_task(task_id, _apply)

    dispatch_for_state(task_id, task, result['state'], trigger='taizi-retry')
    return {'ok': True, 'message': f'{task_id} 已触发重试派发', 'retryCount': result['retryCount']}


def handle_scheduler_escalate(task_id, reason=''):
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    state = task.get('state', '')
    if state in _TERMINAL_STATES:
        return {'ok': False, 'error': f'任务 {task_id} 已结束，无需升级'}

    sched = _ensure_scheduler(task)
    current_level = int(sched.get('escalationLevel') or 0)
    next_level = min(current_level + 1, 2)
    target = 'menxia' if next_level == 1 else 'shangshu'
    target_label = '门下省' if next_level == 1 else '尚书省'

    sched['escalationLevel'] = next_level
    sched['lastEscalatedAt'] = now_iso()
    _scheduler_add_flow(task, f'升级到{target_label}协调：{reason or "任务停滞"}', to=target_label)
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    msg = (
        f'🧭 太子调度升级通知\n'
        f'任务ID: {task_id}\n'
        f'当前状态: {state}\n'
        f'停滞处理: 请你介入协调推进\n'
        f'原因: {reason or "任务超过阈值未推进"}\n'
        f'⚠️ 看板已有任务，请勿重复创建。'
    )
    wake_agent(target, msg)

    return {'ok': True, 'message': f'{task_id} 已升级至{target_label}', 'escalationLevel': next_level}


def handle_scheduler_rollback(task_id, reason=''):
    # Pre-check before acquiring lock
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    sched = _ensure_scheduler(task)
    snapshot = sched.get('snapshot') or {}
    snap_state = snapshot.get('state')
    if not snap_state:
        return {'ok': False, 'error': f'任务 {task_id} 无可用回滚快照'}

    result = {'snap_state': snap_state}

    def _apply(task):
        sched = _ensure_scheduler(task)
        snapshot = sched.get('snapshot') or {}
        s_state = snapshot.get('state')
        if not s_state:
            return  # snapshot cleared between pre-check and lock
        old_state = task.get('state', '')
        task['state'] = s_state
        task['org'] = snapshot.get('org', task.get('org', ''))
        task['now'] = f'↩️ 太子调度自动回滚：{reason or "恢复到上个稳定节点"}'
        task['block'] = '无'
        sched['retryCount'] = 0
        sched['escalationLevel'] = 0
        sched['stallSince'] = None
        sched['lastProgressAt'] = now_iso()
        _scheduler_add_flow(task, f'执行回滚：{old_state} → {s_state}，原因：{reason or "停滞恢复"}')
        result['snap_state'] = s_state

    modify_task(task_id, _apply)

    if result['snap_state'] not in _TERMINAL_STATES:
        dispatch_for_state(task_id, task, result['snap_state'], trigger='taizi-rollback')

    return {'ok': True, 'message': f'{task_id} 已回滚到 {result["snap_state"]}'}


def handle_scheduler_scan(threshold_sec=600):
    """Periodic stall scanner — runs in a background thread.

    Uses ``modify_tasks`` to hold the file lock during the mutation phase,
    preventing concurrent dispatch callbacks and HTTP handlers from
    clobbering each other's writes (fixes TOCTOU race between the old
    ``load_tasks()`` / ``save_tasks()`` pair).

    Side-effects (dispatch, escalation wake) are executed *after* the lock
    is released so they don't block other writers.
    """
    threshold_sec = max(60, int(threshold_sec or 600))
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    # Collect dispatch/escalation work to execute after the lock is released
    pending_handoffs = []
    pending_retries = []
    pending_escalates = []
    pending_rollbacks = []
    actions = []

    def _scan(tasks):
        changed = False
        for task in tasks:
            task_id = task.get('id', '')
            state = task.get('state', '')
            if not task_id or state in _TERMINAL_STATES or task.get('archived'):
                continue
            if state == 'Blocked':
                continue

            sched = _ensure_scheduler(task)
            task_threshold = int(sched.get('stallThresholdSec') or threshold_sec)
            last_progress = _parse_iso(sched.get('lastProgressAt') or task.get('updatedAt'))
            if not last_progress:
                continue
            stalled_sec = max(0, int((now_dt - last_progress).total_seconds()))

            expected_agent = _expected_agent_for_task(task, state)
            last_status = sched.get('lastDispatchStatus')
            last_agent = sched.get('lastDispatchAgent')
            last_state = sched.get('lastDispatchState')
            if (
                expected_agent
                and not sched.get('activeDispatchId')
                and last_status != 'queued'
                and stalled_sec < task_threshold
                and (last_agent != expected_agent or last_state != state)
            ):
                sched['lastDispatchTrigger'] = 'state-handoff-scan'
                _scheduler_add_flow(
                    task,
                    f'检测到状态移交未派发，准备派发 {state} → {expected_agent}',
                    to=_STATE_LABELS.get(state, state),
                )
                pending_handoffs.append((task_id, state))
                actions.append({
                    'taskId': task_id,
                    'action': 'handoff',
                    'to': expected_agent,
                    'state': state,
                })
                changed = True
                continue

            if stalled_sec < task_threshold:
                continue

            if not sched.get('stallSince'):
                sched['stallSince'] = now_iso()
                changed = True

            retry_count = int(sched.get('retryCount') or 0)
            max_retry = max(0, int(sched.get('maxRetry') or 1))
            level = int(sched.get('escalationLevel') or 0)

            if retry_count < max_retry:
                sched['retryCount'] = retry_count + 1
                sched['lastRetryAt'] = now_iso()
                sched['lastDispatchTrigger'] = 'taizi-scan-retry'
                _scheduler_add_flow(task, f'停滞{stalled_sec}秒，触发自动重试第{sched["retryCount"]}次')
                pending_retries.append((task_id, state))
                actions.append({'taskId': task_id, 'action': 'retry', 'stalledSec': stalled_sec})
                changed = True
                continue

            if level < 2:
                next_level = level + 1
                target = 'menxia' if next_level == 1 else 'shangshu'
                target_label = '门下省' if next_level == 1 else '尚书省'
                sched['escalationLevel'] = next_level
                sched['lastEscalatedAt'] = now_iso()
                _scheduler_add_flow(task, f'停滞{stalled_sec}秒，升级至{target_label}协调', to=target_label)
                pending_escalates.append((task_id, state, target, target_label, stalled_sec))
                actions.append({'taskId': task_id, 'action': 'escalate', 'to': target_label, 'stalledSec': stalled_sec})
                changed = True
                continue

            if sched.get('autoRollback', True):
                rollback_count = int(sched.get('rollbackCount') or 0)
                max_rollback = int(sched.get('maxRollback') or 3)
                snapshot = sched.get('snapshot') or {}
                snap_state = snapshot.get('state')
                if rollback_count >= max_rollback:
                    if state != 'Blocked':
                        task['state'] = 'Blocked'
                        task['now'] = f'🚫 连续回滚{rollback_count}次仍无法推进，已自动挂起'
                        task['block'] = f'连续停滞且回滚{rollback_count}次均失败，需人工介入'
                        sched['stallSince'] = None
                        _scheduler_add_flow(task, f'连续回滚{rollback_count}次，自动挂起等待人工介入')
                        actions.append({'taskId': task_id, 'action': 'blocked', 'reason': f'max rollback {rollback_count}'})
                        changed = True
                elif snap_state and snap_state != state:
                    old_state = state
                    task['state'] = snap_state
                    task['org'] = snapshot.get('org', task.get('org', ''))
                    task['now'] = '↩️ 太子调度自动回滚到稳定节点'
                    task['block'] = '无'
                    sched['retryCount'] = 0
                    sched['escalationLevel'] = 0
                    sched['rollbackCount'] = rollback_count + 1
                    sched['stallSince'] = None
                    sched['lastProgressAt'] = now_iso()
                    _scheduler_add_flow(task, f'连续停滞，自动回滚：{old_state} → {snap_state}（第{rollback_count + 1}次）')
                    pending_rollbacks.append((task_id, snap_state))
                    actions.append({'taskId': task_id, 'action': 'rollback', 'toState': snap_state})
                    changed = True

        return tasks  # always return — atomic_json_update requires it

    modify_tasks(_scan)

    # --- Side-effects: dispatch & escalation (outside the file lock) ---

    # Re-read tasks for dispatch context (the task objects from _scan are
    # no longer held under the lock, but dispatch only needs id + state +
    # title which are immutable at this point).
    tasks = load_tasks()

    for task_id, state in pending_handoffs:
        handoff_task = next((t for t in tasks if t.get('id') == task_id), None)
        if handoff_task:
            dispatch_for_state(task_id, handoff_task, state, trigger='state-handoff-scan')

    for task_id, state in pending_retries:
        retry_task = next((t for t in tasks if t.get('id') == task_id), None)
        if retry_task:
            dispatch_for_state(task_id, retry_task, state, trigger='taizi-scan-retry')

    for task_id, state, target, target_label, stalled_sec in pending_escalates:
        msg = (
            f'🧭 太子调度升级通知\n'
            f'任务ID: {task_id}\n'
            f'当前状态: {state}\n'
            f'已停滞: {stalled_sec} 秒\n'
            f'请立即介入协调推进\n'
            f'⚠️ 看板已有任务，请勿重复创建。'
        )
        wake_agent(target, msg)

    for task_id, state in pending_rollbacks:
        rollback_task = next((t for t in tasks if t.get('id') == task_id), None)
        if rollback_task and state not in _TERMINAL_STATES:
            dispatch_for_state(task_id, rollback_task, state, trigger='taizi-auto-rollback')

    return {
        'ok': True,
        'thresholdSec': threshold_sec,
        'actions': actions,
        'count': len(actions),
        'checkedAt': now_iso(),
    }


def _startup_recover_queued_dispatches():
    """服务启动后扫描 lastDispatchStatus=queued 的任务，重新派发。
    解决：kill -9 重启导致派发线程中断、任务永久卡住的问题。"""
    compacted = _outbox_compact_unfinished_duplicates('dashboard startup duplicate cleanup')
    if compacted.get('count'):
        log.info(f'🔄 启动恢复: 压缩 {compacted["count"]} 个重复 outbox')
    requeued = _outbox_requeue_orphaned_running(_DISPATCH_WORKER_ID, 'dashboard startup recovery')
    if requeued.get('count'):
        log.info(f'🔄 启动恢复: 回收 {requeued["count"]} 个 orphaned running outbox')
    if _outbox_list(status='pending', limit=1) or _outbox_list(status='running', limit=1):
        log.info('🔄 启动恢复: 发现 runtime outbox 未完成项，启动 worker')
        _kick_dispatch_worker()

    tasks = load_tasks()
    recovered = 0
    for task in tasks:
        task_id = task.get('id', '')
        state = task.get('state', '')
        if not task_id or state in _TERMINAL_STATES or task.get('archived'):
            continue
        sched = task.get('_scheduler') or {}
        if sched.get('lastDispatchStatus') == 'queued':
            active_id = sched.get('activeDispatchId', '')
            outbox_items = _outbox_list(task_id=task_id, limit=50)
            if active_id and any(
                item.get('id') == active_id and item.get('status') in {'pending', 'running'}
                for item in outbox_items
            ):
                continue
            log.info(f'🔄 启动恢复: {task_id} 状态={state} 上次派发未完成，重新派发')
            sched['lastDispatchTrigger'] = 'startup-recovery'
            dispatch_for_state(task_id, task, state, trigger='startup-recovery')
            recovered += 1
    if recovered:
        log.info(f'✅ 启动恢复完成: 重新派发 {recovered} 个任务')
    else:
        log.info(f'✅ 启动恢复: 无需恢复')


def handle_repair_flow_order():
    """修复历史任务中首条流转为“皇上->中书省”的错序问题。"""
    tasks = load_tasks()
    fixed = 0
    fixed_ids = []

    for task in tasks:
        task_id = task.get('id', '')
        if not task_id.startswith('JJC-'):
            continue
        flow_log = task.get('flow_log') or []
        if not flow_log:
            continue

        first = flow_log[0]
        if first.get('from') != '皇上' or first.get('to') != '中书省':
            continue

        first['to'] = '太子'
        remark = first.get('remark', '')
        if isinstance(remark, str) and remark.startswith('下旨：'):
            first['remark'] = remark

        if task.get('state') == 'Zhongshu' and task.get('org') == '中书省' and len(flow_log) == 1:
            task['state'] = 'Taizi'
            task['org'] = '太子'
            task['now'] = '等待太子接旨分拣'

        task['updatedAt'] = now_iso()
        fixed += 1
        fixed_ids.append(task_id)

    if fixed:
        save_tasks(tasks)

    return {
        'ok': True,
        'count': fixed,
        'taskIds': fixed_ids[:80],
        'more': max(0, fixed - 80),
        'checkedAt': now_iso(),
    }


def _collect_message_text(msg):
    """收集消息中的可检索文本，用于 task_id/关键词过滤。"""
    msg = _dict(msg)
    parts = []
    for c in _list(msg.get('content')):
        if not isinstance(c, dict):
            continue
        ctype = c.get('type')
        if ctype == 'text' and c.get('text'):
            parts.append(str(c.get('text', '')))
        elif ctype == 'thinking' and c.get('thinking'):
            parts.append(str(c.get('thinking', '')))
        elif ctype == 'tool_use':
            parts.append(json.dumps(c.get('input', {}), ensure_ascii=False))
    details = _dict(msg.get('details'))
    for key in ('output', 'stdout', 'stderr', 'message'):
        val = details.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
    return ''.join(parts)


def _parse_activity_entry(item):
    """将 session jsonl 的 message 统一解析成看板活动条目。"""
    item = _dict(item)
    msg = _dict(item.get('message'))
    role = str(msg.get('role', '')).strip().lower()
    ts = item.get('timestamp', '')

    if role == 'assistant':
        text = ''
        thinking = ''
        tool_calls = []
        for c in _list(msg.get('content')):
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'text' and c.get('text') and not text:
                text = str(c.get('text', '')).strip()
            elif c.get('type') == 'thinking' and c.get('thinking') and not thinking:
                thinking = str(c.get('thinking', '')).strip()[:200]
            elif c.get('type') == 'tool_use':
                tool_calls.append({
                    'name': c.get('name', ''),
                    'input_preview': json.dumps(c.get('input', {}), ensure_ascii=False)[:100]
                })
        if not (text or thinking or tool_calls):
            return None
        entry = {'at': ts, 'kind': 'assistant'}
        if text:
            entry['text'] = text[:300]
        if thinking:
            entry['thinking'] = thinking
        if tool_calls:
            entry['tools'] = tool_calls
        return entry

    if role in ('toolresult', 'tool_result'):
        details = _dict(msg.get('details'))
        code = details.get('exitCode')
        if code is None:
            code = details.get('code', details.get('status'))
        output = ''
        for c in _list(msg.get('content')):
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'text' and c.get('text'):
                output = str(c.get('text', '')).strip()[:200]
                break
        if not output:
            for key in ('output', 'stdout', 'stderr', 'message'):
                val = details.get(key)
                if isinstance(val, str) and val.strip():
                    output = val.strip()[:200]
                    break

        entry = {
            'at': ts,
            'kind': 'tool_result',
            'tool': msg.get('toolName', msg.get('name', '')),
            'exitCode': code,
            'output': output,
        }
        duration_ms = details.get('durationMs')
        if isinstance(duration_ms, (int, float)):
            entry['durationMs'] = int(duration_ms)
        return entry

    if role == 'user':
        text = ''
        for c in _list(msg.get('content')):
            if not isinstance(c, dict):
                continue
            if c.get('type') == 'text' and c.get('text'):
                text = str(c.get('text', '')).strip()
                break
        if not text:
            return None
        return {'at': ts, 'kind': 'user', 'text': text[:200]}

    return None


def _opencode_ts_to_iso(value):
    try:
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000
        return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except Exception:
        return ''


def _opencode_part_time(part):
    part = _dict(part)
    state_time = _dict(_dict(part.get('state')).get('time'))
    part_time = _dict(part.get('time'))
    return (
        state_time.get('start')
        or part_time.get('start')
        or state_time.get('end')
        or part_time.get('end')
        or 0
    )


def _read_opencode_json(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _opencode_storage():
    return OPENCODE_HOME / 'storage'


def _opencode_db_path():
    return OPENCODE_HOME / 'opencode.db'


def _opencode_db_query(sql, args=()):
    db_path = _opencode_db_path()
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=0.5)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute(sql, tuple(args or ()))]
        finally:
            conn.close()
    except Exception as exc:
        log.debug(f'OpenCode DB 读取失败: {exc}')
        return []


def _opencode_db_json(value):
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _opencode_db_session_row(row):
    session = {
        'id': row.get('id', ''),
        'projectID': row.get('project_id', ''),
        'directory': row.get('directory') or row.get('path') or '',
        'path': row.get('path') or '',
        'title': row.get('title') or '',
        'agent': row.get('agent') or '',
        'model': _opencode_db_json(row.get('model')) or row.get('model') or '',
        'projectWorktree': row.get('project_worktree') or '',
        'time': {
            'created': row.get('time_created') or 0,
            'updated': row.get('time_updated') or row.get('time_created') or 0,
        },
        'source': 'opencode-db',
    }
    return session


def _opencode_db_message_row(row):
    message = _opencode_db_json(row.get('data'))
    message.setdefault('id', row.get('id', ''))
    message.setdefault('sessionID', row.get('session_id', ''))
    if not isinstance(message.get('time'), dict):
        message['time'] = {}
    message['time'].setdefault('created', row.get('time_created') or 0)
    message['time'].setdefault('updated', row.get('time_updated') or row.get('time_created') or 0)
    message.setdefault('source', 'opencode-db')
    return message


def _opencode_db_part_row(row):
    part = _opencode_db_json(row.get('data'))
    part.setdefault('id', row.get('id', ''))
    part.setdefault('messageID', row.get('message_id', ''))
    part.setdefault('sessionID', row.get('session_id', ''))
    if not isinstance(part.get('time'), dict):
        part['time'] = {}
    part['time'].setdefault('start', row.get('time_created') or 0)
    part['time'].setdefault('end', row.get('time_updated') or row.get('time_created') or 0)
    part.setdefault('source', 'opencode-db')
    return part


def _opencode_db_session_by_id(session_id):
    sid = _safe_opencode_id(session_id, 'ses_')
    if not sid:
        return None
    rows = _opencode_db_query(
        """
        SELECT s.*, p.worktree AS project_worktree
        FROM session s
        LEFT JOIN project p ON p.id = s.project_id
        WHERE s.id = ?
        LIMIT 1
        """,
        (sid,),
    )
    return _opencode_db_session_row(rows[0]) if rows else None


def _opencode_db_messages_for_session(session_id):
    sid = _safe_opencode_id(session_id, 'ses_')
    if not sid:
        return []
    rows = _opencode_db_query(
        """
        SELECT id, session_id, time_created, time_updated, data
        FROM message
        WHERE session_id = ?
        ORDER BY time_created, id
        """,
        (sid,),
    )
    return [_opencode_db_message_row(row) for row in rows]


def _opencode_db_parts_for_message(message):
    msg_id = _safe_opencode_id((message or {}).get('id', ''), 'msg_')
    if not msg_id:
        return []
    rows = _opencode_db_query(
        """
        SELECT id, message_id, session_id, time_created, time_updated, data
        FROM part
        WHERE message_id = ?
        ORDER BY time_created, id
        """,
        (msg_id,),
    )
    parts = [_opencode_db_part_row(row) for row in rows]
    parts.sort(key=_opencode_part_time)
    return parts


def _opencode_db_recent_sessions(limit=100):
    rows = _opencode_db_query(
        """
        SELECT s.*, p.worktree AS project_worktree
        FROM session s
        LEFT JOIN project p ON p.id = s.project_id
        ORDER BY s.time_updated DESC
        LIMIT ?
        """,
        (max(1, int(limit or 100)),),
    )
    return [_opencode_db_session_row(row) for row in rows]


def _safe_opencode_id(value, prefix):
    raw = str(value or '').strip()
    if not raw.startswith(prefix):
        return ''
    if not re.match(r'^[A-Za-z0-9_-]+$', raw):
        return ''
    return raw


def _opencode_session_path(session_id):
    sid = _safe_opencode_id(session_id, 'ses_')
    if not sid:
        return None
    storage = _opencode_storage()
    for path in (storage / 'session').glob(f'*/{sid}.json'):
        return path
    return None


def _opencode_session_by_id(session_id):
    path = _opencode_session_path(session_id)
    if path:
        session = _read_opencode_json(path)
        if isinstance(session, dict):
            session.setdefault('source', 'opencode-json')
            return session
    return _opencode_db_session_by_id(session_id)


def _opencode_messages_for_session(session_id):
    sid = _safe_opencode_id(session_id, 'ses_')
    if not sid:
        return []
    msg_dir = _opencode_storage() / 'message' / sid
    messages = []
    if msg_dir.exists():
        for msg_path in sorted(msg_dir.glob('*.json')):
            msg = _read_opencode_json(msg_path)
            if isinstance(msg, dict):
                msg.setdefault('source', 'opencode-json')
                messages.append(msg)
    return messages or _opencode_db_messages_for_session(sid)


def _opencode_parts_for_message(message):
    message = _dict(message)
    msg_id = _safe_opencode_id(message.get('id', ''), 'msg_')
    if not msg_id:
        return []
    part_dir = _opencode_storage() / 'part' / msg_id
    parts = []
    if part_dir.exists():
        for path in sorted(part_dir.glob('*.json')):
            part = _read_opencode_json(path)
            if isinstance(part, dict):
                part.setdefault('source', 'opencode-json')
                parts.append(part)
    if not parts:
        parts = _opencode_db_parts_for_message(message)
    parts.sort(key=_opencode_part_time)
    return parts


def _opencode_session_belongs_to_project(session):
    if not isinstance(session, dict):
        return False
    for key in ('directory', 'path', 'projectWorktree', 'worktree'):
        directory = str(session.get(key) or '')
        if not directory:
            continue
        try:
            if pathlib.Path(directory).resolve() == PROJECT_ROOT.resolve():
                return True
        except Exception:
            continue
    return not any(session.get(key) for key in ('directory', 'path', 'projectWorktree', 'worktree'))


def _opencode_part_search_text(part):
    if not isinstance(part, dict):
        return ''
    values = [part.get('type', ''), part.get('tool', '')]
    state = _dict(part.get('state'))
    for key in ('title', 'output', 'error'):
        if state.get(key):
            values.append(str(state.get(key))[:2000])
    raw_input = state.get('input')
    if raw_input:
        try:
            values.append(json.dumps(raw_input, ensure_ascii=False)[:2000])
        except Exception:
            values.append(str(raw_input)[:2000])
    if part.get('text'):
        values.append(str(part.get('text'))[:2000])
    return ' '.join(values)


def _opencode_message_search_text(message, include_parts=False):
    message = _dict(message)
    summary = _dict(message.get('summary'))
    values = [
        str(message.get('id') or ''),
        str(message.get('agent') or ''),
        str(summary.get('title') or ''),
    ]
    if include_parts:
        values.extend(_opencode_part_search_text(part) for part in _opencode_parts_for_message(message))
    return ' '.join(v for v in values if v)


def _opencode_session_candidates(agent_id='', task_id=None, keywords=None, limit_sessions=5):
    storage = _opencode_storage()
    sessions_root = storage / 'session'
    candidates = []
    seen_sessions = set()

    json_sessions = []
    if sessions_root.exists():
        for path in sessions_root.glob('*/*.json'):
            session = _read_opencode_json(path)
            if isinstance(session, dict):
                session.setdefault('id', session.get('id') or path.stem)
                session.setdefault('source', 'opencode-json')
                json_sessions.append(session)

    for session in json_sessions + _opencode_db_recent_sessions(limit=200):
        sid = session.get('id') or ''
        if sid in seen_sessions:
            continue
        seen_sessions.add(sid)
        if not _opencode_session_belongs_to_project(session):
            continue

        messages = _opencode_messages_for_session(sid)
        if agent_id and messages and not any(m.get('agent') == agent_id for m in messages):
            continue

        haystack = ' '.join([
            str(session.get('title') or ''),
            sid,
            ' '.join(_opencode_message_search_text(m, include_parts=bool(task_id or keywords)) for m in messages),
        ]).lower()
        if task_id and task_id.lower() not in haystack:
            continue
        if keywords:
            hits = sum(1 for kw in keywords if str(kw).lower() in haystack)
            if hits < min(2, len(keywords)):
                continue

        session_time = _dict(session.get('time'))
        updated = (session_time.get('updated') or session_time.get('created') or 0)
        candidates.append((int(updated or 0), session, messages))

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[:limit_sessions]


def _opencode_session_id_from_output(stdout):
    for line in (stdout or '').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        sid = item.get('sessionID') or (item.get('part') or {}).get('sessionID')
        if sid:
            return sid
    return ''


def _opencode_session_error(session_id):
    if not session_id:
        return ''
    try:
        req = Request(f'{_opencode_server_url()}/session/{session_id}/message', headers={'Accept': 'application/json'})
        data = json.loads(urlopen(req, timeout=5).read().decode('utf-8'))
    except Exception as exc:
        return f'OpenCode session 结果读取失败: {exc}'
    if not isinstance(data, list):
        return ''
    for item in data:
        info = item.get('info') if isinstance(item, dict) else {}
        if not isinstance(info, dict):
            continue
        err = info.get('error')
        if not err:
            continue
        if isinstance(err, dict):
            detail = err.get('data') if isinstance(err.get('data'), dict) else {}
            msg = detail.get('message') or err.get('message') or err.get('name')
            model = info.get('modelID') or ''
            provider = info.get('providerID') or ''
            prefix = f'{provider}/{model}: ' if provider or model else ''
            return (prefix + str(msg or err))[:500]
        return str(err)[:500]
    return ''


def _display_project_text(value):
    text = '' if value is None else str(value)
    try:
        root = PROJECT_ROOT.resolve().as_posix()
        text = text.replace(root + '/', '').replace(root, '.')
    except Exception:
        pass
    return text


def _opencode_input_preview(value):
    if not value:
        return ''
    try:
        if isinstance(value, dict):
            if value.get('description'):
                return _display_project_text(value.get('description'))[:120]
            if value.get('command'):
                return _display_project_text(value.get('command'))[:120]
        return _display_project_text(json.dumps(value, ensure_ascii=False))[:120]
    except Exception:
        return _display_project_text(value)[:120]


def _opencode_tool_context(raw_input):
    payload = raw_input if isinstance(raw_input, dict) else {}
    path = _file_path_from_payload(payload) if '_file_path_from_payload' in globals() else ''
    command = _command_from_payload(payload) if '_command_from_payload' in globals() else ''
    start_line, end_line = _line_range_from_payload(payload) if '_line_range_from_payload' in globals() else (0, 0)
    return path, command, start_line, end_line


def _parse_opencode_parts(message, limit_per_message=20):
    message = _dict(message)
    parts = _opencode_parts_for_message(message)
    if not parts:
        summary = _dict(message.get('summary')).get('title') or ''
        role = str(message.get('role') or '').lower()
        at = _opencode_ts_to_iso(_dict(message.get('time')).get('created'))
        if summary and role == 'user':
            return [{'at': at, 'kind': 'user', 'text': summary[:200], 'agent': message.get('agent', '')}]
        if summary:
            return [{'at': at, 'kind': 'assistant', 'text': summary[:300], 'agent': message.get('agent', '')}]
        return []

    entries = []
    agent = message.get('agent', '')
    role = str(message.get('role') or '').lower()
    for part in parts[:limit_per_message]:
        if not isinstance(part, dict):
            continue
        ptype = part.get('type')
        at = _opencode_ts_to_iso(_opencode_part_time(part) or _dict(message.get('time')).get('created'))
        if ptype == 'text' and part.get('text'):
            entries.append({
                'at': at,
                'kind': 'user' if role == 'user' else 'assistant',
                'text': str(part.get('text', '')).strip()[:300],
                'agent': agent,
                'source': 'opencode-storage',
                'eventKind': 'opencode_text',
            })
        elif ptype == 'tool':
            state = _dict(part.get('state'))
            tool_name = part.get('tool') or state.get('tool') or ''
            status = state.get('status', '')
            raw_input = state.get('input')
            path, command, start_line, end_line = _opencode_tool_context(raw_input)
            tool_call = {
                'name': tool_name,
                'input_preview': _opencode_input_preview(raw_input),
                'callId': part.get('callID', ''),
            }
            if isinstance(raw_input, dict):
                tool_call['input'] = raw_input
            if path:
                tool_call['path'] = path
            if command:
                tool_call['command'] = command
            tool_entry = {
                'at': at,
                'kind': 'assistant',
                'agent': agent,
                'tools': [tool_call],
                'source': 'opencode-storage',
                'eventKind': 'opencode_tool_call',
                'messageId': message.get('id', ''),
                'sessionId': message.get('sessionID', ''),
            }
            entries.append(tool_entry)

            output = state.get('output') or state.get('error') or state.get('title') or ''
            metadata = _dict(state.get('metadata'))
            if not output and isinstance(metadata, dict):
                output = metadata.get('output') or metadata.get('preview') or metadata.get('description') or ''
            exit_code = metadata.get('exit')
            if exit_code is None and status and status != 'completed':
                exit_code = 1
            entries.append({
                'at': _opencode_ts_to_iso((_dict(state.get('time')).get('end')) or _opencode_part_time(part)),
                'kind': 'tool_result',
                'agent': agent,
                'tool': tool_name,
                    'output': _display_project_text(output).strip()[:250],
                'exitCode': exit_code,
                'path': path,
                'command': command,
                'startLine': start_line,
                'endLine': end_line,
                'status': status,
                'source': 'opencode-storage',
                'eventKind': 'opencode_tool_result',
                'messageId': message.get('id', ''),
                'sessionId': message.get('sessionID', ''),
            })
        elif ptype == 'patch':
            tools = []
            for raw_path in _list(part.get('files')):
                source = _safe_source_path(str(raw_path), allow_missing=True)
                rel = _rel_project_path(source) if source else str(raw_path)
                tools.append({
                    'name': 'patch',
                    'path': rel,
                    'input_preview': f"patch {rel}",
                    'input': {
                        'filePath': rel,
                        'hash': part.get('hash', ''),
                    },
                })
            if tools:
                entries.append({
                    'at': at,
                    'kind': 'assistant',
                    'agent': agent,
                    'tools': tools,
                    'source': 'opencode-storage',
                    'eventKind': 'opencode_patch',
                    'messageId': message.get('id', ''),
                    'sessionId': message.get('sessionID', ''),
                })
        elif ptype == 'step-finish':
            tokens = _dict(part.get('tokens'))
            total_tokens = 0
            for val in tokens.values():
                if isinstance(val, dict):
                    total_tokens += sum(v for v in val.values() if isinstance(v, (int, float)))
                elif isinstance(val, (int, float)):
                    total_tokens += val
            if total_tokens or part.get('cost'):
                entries.append({
                    'at': at,
                    'kind': 'progress',
                    'agent': agent,
                    'text': f"模型步骤完成: {part.get('reason', '') or 'finished'}",
                    'tokens': int(total_tokens) if total_tokens else 0,
                    'cost': part.get('cost') or 0,
                    'source': 'opencode-storage',
                    'eventKind': 'opencode_step_finish',
                })
    return entries


def get_opencode_session_activity(session_id, agent_id='', limit=80):
    session = _opencode_session_by_id(session_id)
    if not session or not _opencode_session_belongs_to_project(session):
        return []
    entries = []
    for message in sorted(_opencode_messages_for_session(session_id), key=lambda m: (_dict(_dict(m).get('time')).get('created') or 0)):
        if not isinstance(message, dict):
            continue
        if agent_id and message.get('agent') and message.get('agent') != agent_id:
            continue
        entries.extend(_parse_opencode_parts(message, limit_per_message=40))
    entries.sort(key=lambda e: e.get('at', ''))
    return entries[-limit:]


def get_opencode_agent_activity(agent_id, limit=30, task_id=None, keywords=None):
    entries = []
    candidates = _opencode_session_candidates(agent_id, task_id=task_id, keywords=keywords, limit_sessions=5 if (task_id or keywords) else 1)
    for _updated, _session, messages in candidates:
        for message in sorted(messages, key=lambda m: (_dict(_dict(m).get('time')).get('created') or 0)):
            if not isinstance(message, dict):
                continue
            if agent_id and message.get('agent') and message.get('agent') != agent_id:
                continue
            entries.extend(_parse_opencode_parts(message))
    entries.sort(key=lambda e: e.get('at', ''))
    return entries[-limit:]


def _task_opencode_session_ids(task, ledger_events):
    out = []
    seen = set()

    def add(value):
        sid = _safe_opencode_id(value, 'ses_')
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)

    sched = (task or {}).get('_scheduler') or {}
    add(sched.get('lastDispatchSession'))
    add(sched.get('activeDispatchSession'))
    for event in ledger_events or []:
        if not isinstance(event, dict):
            continue
        add(event.get('sessionId'))
        payload = _dict(event.get('payload'))
        add(payload.get('sessionId'))
        result = _dict(payload.get('result'))
        add(result.get('sessionId'))
    return out


def get_agent_activity(agent_id, limit=30, task_id=None):
    """从 Agent 的 session jsonl 读取最近活动。
    如果 task_id 不为空，只返回提及该 task_id 的相关条目。
    """
    if _agent_runtime() == 'opencode':
        return get_opencode_agent_activity(agent_id, limit=limit, task_id=task_id)

    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    # 扫描所有 jsonl（按修改时间倒序），优先最新
    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    entries = []
    # 如果需要按 task_id 过滤，可能需要扫描多个文件
    files_to_scan = jsonl_files[:3] if task_id else jsonl_files[:1]

    for session_file in files_to_scan:
        try:
            lines = session_file.read_text(errors='ignore').splitlines()
        except Exception:
            continue

        # 正向扫描以保持时间顺序；如果有 task_id，收集提及 task_id 的条目
        for ln in lines:
            try:
                item = json.loads(ln)
            except Exception:
                continue
            msg = item.get('message') or {}
            all_text = _collect_message_text(msg)

            # task_id 过滤：只保留提及 task_id 的条目
            if task_id and task_id not in all_text:
                continue
            entry = _parse_activity_entry(item)
            if entry:
                entries.append(entry)

            if len(entries) >= limit:
                break
        if len(entries) >= limit:
            break

    # 只保留最后 limit 条
    return entries[-limit:]


def _extract_keywords(title):
    """从任务标题中提取有意义的关键词（用于 session 内容匹配）。"""
    stop = {'的', '了', '在', '是', '有', '和', '与', '或', '一个', '一篇', '关于', '进行',
            '写', '做', '请', '把', '给', '用', '要', '需要', '面向', '风格', '包含',
            '出', '个', '不', '可以', '应该', '如何', '怎么', '什么', '这个', '那个'}
    # 提取英文词
    en_words = re.findall(r'[a-zA-Z][\w.-]{1,}', title)
    # 提取 2-4 字中文词组（更短的颗粒度）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', title)
    all_words = en_words + cn_words
    kws = [w for w in all_words if w not in stop and len(w) >= 2]
    # 去重保序
    seen = set()
    unique = []
    for w in kws:
        if w.lower() not in seen:
            seen.add(w.lower())
            unique.append(w)
    return unique[:8]  # 最多 8 个关键词


def get_agent_activity_by_keywords(agent_id, keywords, limit=20):
    """从 agent session 中按关键词匹配获取活动条目。
    找到包含关键词的 session 文件，只读该文件的活动。
    """
    if _agent_runtime() == 'opencode':
        return get_opencode_agent_activity(agent_id, limit=limit, keywords=keywords)

    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'), key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 找到包含关键词的 session 文件
    target_file = None
    for sf in jsonl_files[:5]:
        try:
            content = sf.read_text(errors='ignore')
        except Exception:
            continue
        hits = sum(1 for kw in keywords if kw.lower() in content.lower())
        if hits >= min(2, len(keywords)):
            target_file = sf
            break

    if not target_file:
        return []

    # 解析 session 文件，按 user 消息分割为对话段
    # 找到包含关键词的对话段，只返回该段的活动
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 第一遍：找到关键词匹配的 user 消息位置
    user_msg_indices = []  # (line_index, user_text)
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            text = ''
            for c in msg.get('content', []):
                if c.get('type') == 'text' and c.get('text'):
                    text += c['text']
            user_msg_indices.append((i, text))

    # 找到与关键词匹配度最高的 user 消息
    best_idx = -1
    best_hits = 0
    for line_idx, utext in user_msg_indices:
        hits = sum(1 for kw in keywords if kw.lower() in utext.lower())
        if hits > best_hits:
            best_hits = hits
            best_idx = line_idx

    # 确定对话段的行范围：从匹配的 user 消息到下一个 user 消息之前
    if best_idx >= 0 and best_hits >= min(2, len(keywords)):
        # 找下一个 user 消息的位置
        next_user_idx = len(lines)
        for line_idx, _ in user_msg_indices:
            if line_idx > best_idx:
                next_user_idx = line_idx
                break
        start_line = best_idx
        end_line = next_user_idx
    else:
        # 没找到匹配的对话段，返回空
        return []

    # 第二遍：只解析对话段内的行
    entries = []
    for ln in lines[start_line:end_line]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def get_agent_latest_segment(agent_id, limit=20):
    """获取 Agent 最新一轮对话段（最后一条 user 消息起的所有内容）。
    用于活跃任务没有精确匹配时，展示 Agent 的实时工作状态。
    """
    if _agent_runtime() == 'opencode':
        return get_opencode_agent_activity(agent_id, limit=limit)

    sessions_dir = OCLAW_HOME / 'agents' / agent_id / 'sessions'
    if not sessions_dir.exists():
        return []

    jsonl_files = sorted(sessions_dir.glob('*.jsonl'),
                         key=lambda f: f.stat().st_mtime, reverse=True)
    if not jsonl_files:
        return []

    # 读取最新的 session 文件
    target_file = jsonl_files[0]
    try:
        lines = target_file.read_text(errors='ignore').splitlines()
    except Exception:
        return []

    # 找到最后一条 user 消息的行号
    last_user_idx = -1
    for i, ln in enumerate(lines):
        try:
            item = json.loads(ln)
        except Exception:
            continue
        msg = item.get('message') or {}
        if msg.get('role') == 'user':
            last_user_idx = i

    if last_user_idx < 0:
        return []

    # 从最后一条 user 消息开始，解析到文件末尾
    entries = []
    for ln in lines[last_user_idx:]:
        try:
            item = json.loads(ln)
        except Exception:
            continue
        entry = _parse_activity_entry(item)
        if entry:
            entries.append(entry)

    return entries[-limit:]


def _compute_phase_durations(flow_log):
    """从 flow_log 计算每个阶段的停留时长。"""
    if not flow_log or len(flow_log) < 1:
        return []
    phases = []
    for i, fl in enumerate(flow_log):
        start_at = fl.get('at', '')
        to_dept = fl.get('to', '')
        remark = fl.get('remark', '')
        # 下一阶段的起始时间就是本阶段的结束时间
        if i + 1 < len(flow_log):
            end_at = flow_log[i + 1].get('at', '')
            ongoing = False
        else:
            end_at = now_iso()
            ongoing = True
        # 计算时长
        dur_sec = 0
        try:
            from_dt = datetime.datetime.fromisoformat(start_at.replace('Z', '+00:00'))
            to_dt = datetime.datetime.fromisoformat(end_at.replace('Z', '+00:00'))
            dur_sec = max(0, int((to_dt - from_dt).total_seconds()))
        except Exception:
            pass
        # 人类可读时长
        if dur_sec < 60:
            dur_text = f'{dur_sec}秒'
        elif dur_sec < 3600:
            dur_text = f'{dur_sec // 60}分{dur_sec % 60}秒'
        elif dur_sec < 86400:
            h, rem = divmod(dur_sec, 3600)
            dur_text = f'{h}小时{rem // 60}分'
        else:
            d, rem = divmod(dur_sec, 86400)
            dur_text = f'{d}天{rem // 3600}小时'
        phases.append({
            'phase': to_dept,
            'from': start_at,
            'to': end_at,
            'durationSec': dur_sec,
            'durationText': dur_text,
            'ongoing': ongoing,
            'remark': remark,
        })
    return phases


def _compute_todos_summary(todos):
    """计算 todos 完成率汇总。"""
    if not todos:
        return None
    total = len(todos)
    completed = sum(1 for t in todos if t.get('status') == 'completed')
    in_progress = sum(1 for t in todos if t.get('status') == 'in-progress')
    not_started = total - completed - in_progress
    percent = round(completed / total * 100) if total else 0
    return {
        'total': total,
        'completed': completed,
        'inProgress': in_progress,
        'notStarted': not_started,
        'percent': percent,
    }


def _compute_todos_diff(prev_todos, curr_todos):
    """计算两个 todos 快照之间的差异。"""
    prev_map = {str(t.get('id', '')): t for t in (prev_todos or [])}
    curr_map = {str(t.get('id', '')): t for t in (curr_todos or [])}
    changed, added, removed = [], [], []
    for tid, ct in curr_map.items():
        if tid in prev_map:
            pt = prev_map[tid]
            if pt.get('status') != ct.get('status'):
                changed.append({
                    'id': tid, 'title': ct.get('title', ''),
                    'from': pt.get('status', ''), 'to': ct.get('status', ''),
                })
        else:
            added.append({'id': tid, 'title': ct.get('title', '')})
    for tid, pt in prev_map.items():
        if tid not in curr_map:
            removed.append({'id': tid, 'title': pt.get('title', '')})
    if not changed and not added and not removed:
        return None
    return {'changed': changed, 'added': added, 'removed': removed}


def _activity_key(entry):
    entry = _dict(entry)
    kind = entry.get('kind', '')
    at = entry.get('at', '')
    if kind == 'flow':
        return (kind, at, entry.get('from', ''), entry.get('to', ''), entry.get('remark', ''))
    if kind == 'progress':
        return (kind, at, entry.get('agent', ''), entry.get('text', ''))
    if kind == 'todos':
        items = entry.get('items') or []
        todo_sig = tuple((str(i.get('id', '')), i.get('status', ''), i.get('title', '')) for i in items)
        return (kind, at, entry.get('agent', ''), todo_sig)
    if kind == 'tool_result':
        return (
            kind,
            at,
            entry.get('agent', ''),
            entry.get('tool', ''),
            entry.get('path', ''),
            entry.get('command', '')[:160],
            entry.get('output', '')[:120],
        )
    if kind == 'assistant':
        tools = entry.get('tools') or []
        tool_sig = tuple(
            (
                str(t.get('name') or t.get('tool') or ''),
                str(t.get('path') or ''),
                str(t.get('command') or '')[:160],
                str(t.get('input_preview') or t.get('inputPreview') or '')[:120],
            )
            for t in tools
        )
        return (kind, at, entry.get('agent', ''), entry.get('text', ''), entry.get('eventKind', ''), tool_sig)
    return (kind, at, entry.get('agent', ''), entry.get('text', '') or entry.get('eventKind', ''))


def _activity_has_visible_content(entry):
    entry = _dict(entry)
    kind = entry.get('kind', '')
    if kind in {'flow', 'progress', 'todos', 'user'}:
        return True
    if kind == 'assistant':
        return bool(entry.get('text') or entry.get('thinking') or entry.get('tools'))
    if kind == 'tool_result':
        return bool(entry.get('output') or entry.get('tool') or entry.get('command') or entry.get('path'))
    return bool(entry.get('text') or entry.get('remark') or entry.get('output') or entry.get('eventKind'))


def _compact_activity_for_ui(activity, limit=_ACTIVITY_UI_LIMIT):
    items = [item for item in activity if isinstance(item, dict) and _activity_has_visible_content(item)]
    total = len(items)
    if total <= limit:
        return items, {'total': total, 'returned': total, 'truncated': False}

    pinned = [item for item in items if item.get('kind') in {'flow', 'progress', 'todos'}]
    selected = []
    seen = set()
    # Keep a bounded amount of high-signal state/progress evidence, then recent details.
    for item in pinned[:80] + items[-limit:]:
        key = _activity_key(item)
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
    selected.sort(key=lambda x: x.get('at', ''))
    if len(selected) > limit:
        selected = selected[-limit:]
    return selected, {'total': total, 'returned': len(selected), 'truncated': True}


def _parse_activity_dt(value):
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            # OpenClaw/OpenCode JSONL can use millisecond timestamps.
            if value > 10_000_000_000:
                value = value / 1000
            return datetime.datetime.fromtimestamp(value, datetime.timezone.utc)
        return datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def _build_state_evidence(task, events, activity):
    state = task.get('state', '')
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    latest_event = events[-1] if events else None
    latest_activity_dt = None
    for entry in activity:
        dt = _parse_activity_dt(entry.get('at'))
        if dt and (latest_activity_dt is None or dt > latest_activity_dt):
            latest_activity_dt = dt

    latest_event_dt = _parse_activity_dt(latest_event.get('at')) if latest_event else None
    if latest_event_dt:
        age_sec = max(0, int((now_utc - latest_event_dt).total_seconds()))
    elif latest_activity_dt:
        age_sec = max(0, int((now_utc - latest_activity_dt).total_seconds()))
    else:
        age_sec = None

    failure_kinds = {'dispatch_failed', 'dispatch_gateway_offline', 'dispatch_missing_cli', 'agent_message_failed', 'state_rejected'}
    if state in ('Done', 'Cancelled'):
        confidence = 'complete'
        label = '流程已收口'
    elif latest_event and latest_event.get('kind') in failure_kinds:
        confidence = 'low'
        label = '最近事件为异常'
    elif latest_event_dt and age_sec is not None and age_sec <= 300:
        confidence = 'high'
        label = '5分钟内有运行证据'
    elif latest_event_dt and age_sec is not None and age_sec <= 1800:
        confidence = 'medium'
        label = '30分钟内有运行证据'
    elif latest_activity_dt and age_sec is not None and age_sec <= 1800:
        confidence = 'medium'
        label = '仅有旧活动日志证据'
    else:
        confidence = 'low'
        label = '缺少近期运行证据'

    sources = []
    if events:
        sources.append('event-ledger')
    if any(a.get('source') != 'event-ledger' for a in activity):
        sources.append('task/session')

    return {
        'confidence': confidence,
        'label': label,
        'eventCount': len(events),
        'latestEventKind': latest_event.get('kind', '') if latest_event else '',
        'latestEventAt': latest_event.get('at', '') if latest_event else '',
        'lastObservedAt': (
            (latest_event_dt or latest_activity_dt).isoformat(timespec='seconds').replace('+00:00', 'Z')
            if (latest_event_dt or latest_activity_dt) else ''
        ),
        'ageSec': age_sec,
        'sources': sources,
    }


def _build_trace_summary(trace_id, events, activity, outbox_summary):
    kinds = {}
    sources = {}
    for event in events:
        kind = event.get('kind', 'event')
        kinds[kind] = kinds.get(kind, 0) + 1
        src = event.get('source') or 'event-ledger'
        sources[src] = sources.get(src, 0) + 1
    for entry in activity:
        src = entry.get('source') or 'task/session'
        sources[src] = sources.get(src, 0) + 1
    latest_at = ''
    for item in list(events) + list(activity):
        at = item.get('at') or item.get('updatedAt') or ''
        if at and at > latest_at:
            latest_at = at
    return {
        'traceId': trace_id,
        'eventKinds': kinds,
        'sources': sources,
        'outbox': outbox_summary,
        'latestAt': latest_at,
    }


def _safe_preview(value, limit=220):
    text = _display_project_text(value)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _tool_payload_from_call(tool_call):
    payload = {}
    if isinstance(tool_call, dict):
        for key in ('path', 'filePath', 'filepath', 'command', 'cmd'):
            val = tool_call.get(key)
            if val and key not in payload:
                payload[key] = val
        raw = tool_call.get('input')
        if isinstance(raw, dict):
            payload.update(raw)
        preview = tool_call.get('input_preview') or tool_call.get('inputPreview') or ''
        if preview:
            payload['_preview'] = preview
            try:
                parsed = json.loads(preview)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except Exception:
                pass
    return payload


def _file_path_from_payload(payload):
    for key in ('filePath', 'filepath', 'path', 'file', 'target', 'source'):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            source = _safe_source_path(val.strip(), allow_missing=True)
            if source:
                return _rel_project_path(source)
            try:
                if pathlib.Path(val.strip()).is_absolute():
                    continue
            except Exception:
                pass
            if pathlib.Path(val.strip()).suffix.lower() in _SOURCE_EXTS:
                return val.strip()
    preview = payload.get('_preview') or ''
    m = re.search(r'([A-Za-z0-9_./\-]+\.(?:py|ts|tsx|js|jsx|json|md|css|html|yml|yaml|toml|sh|txt))(?![A-Za-z0-9_])', preview)
    return m.group(1) if m else ''


def _as_int(value):
    try:
        if value in (None, ''):
            return 0
        return int(value)
    except Exception:
        return 0


def _first_int(payload, keys):
    for key in keys:
        if key not in payload:
            continue
        val = payload.get(key)
        if isinstance(val, dict):
            val = val.get('line') or val.get('lineNumber')
        out = _as_int(val)
        if out:
            return out
    return 0


def _line_range_from_payload(payload):
    start = _first_int(payload, ('startLine', 'start_line', 'lineStart', 'fromLine', 'line', 'lineNumber', 'offset'))
    end = _first_int(payload, ('endLine', 'end_line', 'lineEnd', 'toLine'))
    limit = _first_int(payload, ('limit', 'lineCount', 'lines'))
    range_obj = payload.get('range')
    if isinstance(range_obj, dict):
        start = start or _first_int(range_obj, ('start', 'from'))
        end = end or _first_int(range_obj, ('end', 'to'))
    preview = payload.get('_preview') or ''
    if not start:
        m = re.search(r'(?:line|lines|offset)[^\d]{0,12}(\d+)', preview, re.I)
        if m:
            start = _as_int(m.group(1))
    if start and not end and limit:
        end = start + max(0, limit - 1)
    if start and not end:
        end = start
    if end and start and end < start:
        end = start
    return start, end


def _command_from_payload(payload):
    for key in ('command', 'cmd', 'script'):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return _display_project_text(val.strip())
    return _safe_preview(payload.get('_preview') or '', 220)


def _coding_event(kind, title, *, at='', agent='', detail='', path='', command='', status='', source='', meta=None, start_line=0, end_line=0):
    meta_payload = dict(meta or {})
    if start_line:
        meta_payload['startLine'] = start_line
    if end_line:
        meta_payload['endLine'] = end_line
    source_url = ''
    if path and kind in {'file.read', 'file.change'}:
        source_path = _safe_source_file(path)
        if source_path:
            source_url = f"/api/source-file?{urlencode({'path': _rel_project_path(source_path), 'start': start_line, 'end': end_line})}"
    return {
        'kind': kind,
        'title': title,
        'at': at or '',
        'agent': agent or '',
        'detail': _safe_preview(detail, 500),
        'path': path or '',
        'command': command or '',
        'status': status or '',
        'source': source or '',
        'startLine': start_line or 0,
        'endLine': end_line or 0,
        'sourceUrl': source_url,
        'meta': meta_payload,
    }


def _classify_tool_event(activity_entry, tool_call):
    tool_name = (tool_call.get('name') or tool_call.get('tool') or '').lower()
    payload = _tool_payload_from_call(tool_call)
    path = _file_path_from_payload(payload)
    start_line, end_line = _line_range_from_payload(payload)
    command = _command_from_payload(payload)
    at = activity_entry.get('at', '')
    agent = activity_entry.get('agent', '')
    preview = payload.get('_preview') or command or path

    if tool_name in {'read', 'view', 'open'}:
        return _coding_event('file.read', f'读取文件 {path or ""}'.strip(), at=at, agent=agent, path=path, detail=preview, source=activity_entry.get('source', ''), meta=payload, start_line=start_line, end_line=end_line)
    if tool_name in {'edit', 'write', 'patch', 'multiedit', 'delete', 'remove', 'unlink', 'rm'}:
        return _coding_event('file.change', f'修改文件 {path or ""}'.strip(), at=at, agent=agent, path=path, detail=preview, source=activity_entry.get('source', ''), meta=payload, start_line=start_line, end_line=end_line)
    if tool_name in {'grep', 'glob', 'list', 'ls', 'search'}:
        return _coding_event('tool.search', tool_name or '搜索', at=at, agent=agent, path=path, detail=preview, source=activity_entry.get('source', ''))
    if tool_name in {'bash', 'shell', 'terminal'}:
        is_test = bool(re.search(r'\b(pytest|npm test|pnpm test|yarn test|vitest|playwright|tsc|mypy|ruff)\b', command))
        return _coding_event('test.run' if is_test else 'shell.run', command or '运行命令', at=at, agent=agent, command=command, detail=preview, source=activity_entry.get('source', ''))
    return _coding_event('tool.call', tool_name or '工具调用', at=at, agent=agent, detail=preview, source=activity_entry.get('source', ''))


def _task_output_group(task_id):
    try:
        groups = list_output_files().get('groups') or []
    except Exception:
        return None
    for group in groups:
        if group.get('taskId') == task_id:
            return group
    return None


def get_task_coding_session(task_id):
    """Return a normalized Coding Session view for one task.

    This is the bridge between Edict's governance data and an IDE-style
    execution cockpit. It intentionally derives from existing flow/todo/session
    data first, so it is useful before the future VS Code extension exists.
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    activity_data = get_task_activity(task_id)
    activity = activity_data.get('activity') or []
    trace_id = activity_data.get('traceId') or task.get('traceId') or task.get('trace_id') or task_id
    events = []
    files = {}
    commands = []
    tests = []

    for td in task.get('todos') or []:
        events.append(_coding_event(
            'todo.item',
            td.get('title') or f"Todo {td.get('id', '')}",
            at=task.get('updatedAt', ''),
            status=td.get('status', ''),
            detail=td.get('detail', ''),
            meta={'id': td.get('id', '')},
        ))

    for entry in activity:
        kind = entry.get('kind')
        if kind == 'assistant':
            for tool_call in entry.get('tools') or []:
                event = _classify_tool_event(entry, tool_call)
                events.append(event)
                if event['path'] and event['kind'] in {'file.read', 'file.change'}:
                    files.setdefault(event['path'], {
                        'path': event['path'],
                        'reads': 0,
                        'changes': 0,
                        'outputs': 0,
                        'latestAt': event['at'],
                        'lastStartLine': 0,
                        'lastEndLine': 0,
                        'sourceUrl': event.get('sourceUrl', ''),
                    })
                    if event['kind'] == 'file.read':
                        files[event['path']]['reads'] += 1
                    if event['kind'] == 'file.change':
                        files[event['path']]['changes'] += 1
                    if event.get('startLine'):
                        files[event['path']]['lastStartLine'] = event.get('startLine')
                    if event.get('endLine'):
                        files[event['path']]['lastEndLine'] = event.get('endLine')
                    if event.get('sourceUrl'):
                        files[event['path']]['sourceUrl'] = event.get('sourceUrl')
                    files[event['path']]['latestAt'] = max(files[event['path']]['latestAt'], event['at'])
                if event['kind'] == 'shell.run':
                    commands.append(event)
                if event['kind'] == 'test.run':
                    tests.append(event)
        elif kind == 'tool_result':
            ok = entry.get('exitCode') in (None, 0)
            tool_name = entry.get('tool') or 'tool'
            output = entry.get('output') or ''
            event_kind = 'test.result' if re.search(r'(passed|failed|pytest|test|tsc|vitest)', output, re.I) else 'tool.result'
            event = _coding_event(
                event_kind,
                f'{tool_name} {"成功" if ok else "失败"}',
                at=entry.get('at', ''),
                agent=entry.get('agent', ''),
                detail=output,
                path=entry.get('path', ''),
                command=entry.get('command', ''),
                status='pass' if ok else 'fail',
                source=entry.get('source', ''),
                meta={
                    'tool': tool_name,
                    'eventKind': entry.get('eventKind', ''),
                    'sessionId': entry.get('sessionId', ''),
                    'messageId': entry.get('messageId', ''),
                    'exitCode': entry.get('exitCode'),
                },
                start_line=entry.get('startLine') or 0,
                end_line=entry.get('endLine') or 0,
            )
            events.append(event)
            if event.get('path') and event_kind in {'file.read', 'file.change'}:
                files.setdefault(event['path'], {
                    'path': event['path'],
                    'reads': 0,
                    'changes': 0,
                    'outputs': 0,
                    'latestAt': event['at'],
                    'lastStartLine': event.get('startLine') or 0,
                    'lastEndLine': event.get('endLine') or 0,
                    'sourceUrl': '',
                })
                files[event['path']]['latestAt'] = max(files[event['path']]['latestAt'], event['at'])
            if event_kind == 'test.result':
                tests.append(event)
        elif kind == 'progress':
            events.append(_coding_event('message.progress', '进展更新', at=entry.get('at', ''), agent=entry.get('agent', ''), detail=entry.get('text', ''), source=entry.get('source', '')))
        elif kind == 'flow':
            title = f"{entry.get('from') or '系统'} → {entry.get('to') or ''}".strip()
            events.append(_coding_event('governance.flow', title, at=entry.get('at', ''), detail=entry.get('remark', ''), source=entry.get('source', 'flow_log')))

    output_group = _task_output_group(task_id)
    outputs = []
    if output_group:
        if output_group.get('outputText'):
            event = _coding_event('output.note', '输出说明', detail=output_group.get('outputText', ''), source='task.output')
            outputs.append(event)
            events.append(event)
        for item in output_group.get('files') or []:
            path = item.get('path', '')
            event = _coding_event('output.file', item.get('name') or path, at=item.get('mtime', ''), path=path, detail=item.get('source', ''), status='ready', source='output-files', meta=item)
            outputs.append(event)
            events.append(event)
            if path:
                files.setdefault(path, {
                    'path': path,
                    'reads': 0,
                    'changes': 0,
                    'outputs': 0,
                    'latestAt': item.get('mtime', ''),
                    'lastStartLine': 0,
                    'lastEndLine': 0,
                    'sourceUrl': '',
                })
                files[path]['outputs'] += 1
                files[path]['latestAt'] = max(files[path]['latestAt'], item.get('mtime', ''))

    for path in _task_mentioned_patch_paths(task, activity):
        event = _coding_event(
            'file.change',
            f'工作区变更 {path}',
            at=task.get('updatedAt', '') or activity_data.get('stateEvidence', {}).get('lastObservedAt', ''),
            path=path,
            detail='任务进展提到该文件，且工作区存在未审批变更',
            status='inferred',
            source='worktree-mentioned',
            meta={'inferred': True},
        )
        if not _safe_source_file(path):
            event['sourceUrl'] = ''
        events.append(event)
        files.setdefault(path, {
            'path': path,
            'reads': 0,
            'changes': 0,
            'outputs': 0,
            'latestAt': event.get('at', ''),
            'lastStartLine': 0,
            'lastEndLine': 0,
            'sourceUrl': event.get('sourceUrl', ''),
        })
        files[path]['changes'] += 1
        if event.get('sourceUrl'):
            files[path]['sourceUrl'] = event.get('sourceUrl')
        files[path]['latestAt'] = max(files[path]['latestAt'], event.get('at', ''))

    checkpoint = get_worktree_checkpoint()
    patch_reviews = [_patch_review_public(item) for item in list_patch_reviews(task_id)]
    patch_counts = {}
    for review in patch_reviews:
        status = review.get('status', 'unknown')
        patch_counts[status] = patch_counts.get(status, 0) + 1
    events.sort(key=lambda e: e.get('at') or '')
    summary = {
        'todoTotal': len(task.get('todos') or []),
        'todoDone': sum(1 for td in task.get('todos') or [] if td.get('status') == 'completed'),
        'fileCount': len(files),
        'commandCount': len(commands),
        'testCount': len(tests),
        'outputCount': len(outputs),
        'eventCount': len(events),
        'hasPatchReview': True,
        'pendingPatchCount': patch_counts.get('pending', 0),
        'approvedPatchCount': patch_counts.get('approved', 0),
        'rejectedPatchCount': patch_counts.get('rejected', 0),
        'hasSourcePreview': any(e.get('sourceUrl') for e in events),
        'hasWorktreeCheckpoint': bool(checkpoint.get('ok')),
        'confidence': activity_data.get('stateEvidence', {}).get('confidence', ''),
    }
    return {
        'ok': True,
        'taskId': task_id,
        'sessionId': trace_id,
        'runtime': _agent_runtime(),
        'task': {
            'title': task.get('title', ''),
            'state': task.get('state', ''),
            'org': task.get('org', ''),
            'updatedAt': task.get('updatedAt', ''),
        },
        'summary': summary,
        'files': sorted(files.values(), key=lambda x: x.get('latestAt', ''), reverse=True),
        'commands': commands[-20:],
        'tests': tests[-20:],
        'outputs': outputs,
        'events': events[-120:],
        'patchReviews': patch_reviews[-20:],
        'checkpoint': checkpoint,
        'missingLayers': [item for item in (
            '等待 Agent 上报文件修改事件' if not any(e['kind'] == 'file.change' for e in events) else '',
            '等待 Agent 上报文件读写事件' if not summary['fileCount'] else '',
            '外部编辑器未配置' if not _editor_opener_available() else '',
            'worktree checkpoint 不可用' if not summary['hasWorktreeCheckpoint'] else '',
        ) if item],
    }


def get_task_activity(task_id):
    """获取任务的实时进展数据。
    数据来源：
    1. 任务自身的 now / todos / flow_log 字段（由 Agent 通过 progress 命令主动上报）
    2. Agent session JSONL 中的对话日志（thinking / tool_result / user，用于展示思考过程）

    增强字段:
    - taskMeta: 任务元信息 (title/state/org/output/block/priority/reviewRound/archived)
    - phaseDurations: 各阶段停留时长
    - todosSummary: todos 完成率汇总
    - resourceSummary: Agent 资源消耗汇总 (tokens/cost/elapsed)
    - activity 条目中 progress/todos 保留 state/org 快照
    - activity 中 todos 条目含 diff 字段
    """
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}

    state = task.get('state', '')
    org = task.get('org', '')
    now_text = task.get('now', '')
    todos = task.get('todos', [])
    updated_at = task.get('updatedAt', '')
    trace_id = _ensure_trace_id(task)
    outbox_summary = _outbox_task_summary(task_id)
    output_group = _task_output_group(task_id)

    # ── 任务元信息 ──
    task_meta = {
        'title': task.get('title', ''),
        'state': state,
        'org': org,
        'output': task.get('output', ''),
        'block': task.get('block', ''),
        'priority': task.get('priority', 'normal'),
        'reviewRound': task.get('review_round', 0),
        'archived': task.get('archived', False),
        'traceId': trace_id,
    }

    # 当前负责 Agent（兼容旧逻辑）
    agent_id = _STATE_AGENT_MAP.get(state)
    if agent_id is None and state in ('Doing', 'Next'):
        agent_id = _ORG_AGENT_MAP.get(org)

    # ── 构建活动条目列表（flow_log + progress_log）──
    activity = []
    flow_log = task.get('flow_log', [])

    # 1. flow_log 转为活动条目
    for fl in flow_log:
        activity.append({
            'at': fl.get('at', ''),
            'kind': 'flow',
            'from': fl.get('from', ''),
            'to': fl.get('to', ''),
            'remark': fl.get('remark', ''),
        })

    progress_log = task.get('progress_log', [])
    related_agents = set()

    # 资源消耗累加
    total_tokens = 0
    total_cost = 0.0
    total_elapsed = 0
    has_resource_data = False

    # 用于 todos diff 计算
    prev_todos_snapshot = None

    if progress_log:
        # 2. 多 Agent 实时进展日志（每条 progress 都保留自己的 todo 快照）
        for pl in progress_log:
            p_at = pl.get('at', '')
            p_agent = pl.get('agent', '')
            p_text = pl.get('text', '')
            p_todos = pl.get('todos', [])
            p_state = pl.get('state', '')
            p_org = pl.get('org', '')
            if p_agent:
                related_agents.add(p_agent)
            # 累加资源消耗
            if pl.get('tokens'):
                total_tokens += pl['tokens']
                has_resource_data = True
            if pl.get('cost'):
                total_cost += pl['cost']
                has_resource_data = True
            if pl.get('elapsed'):
                total_elapsed += pl['elapsed']
                has_resource_data = True
            if p_text:
                entry = {
                    'at': p_at,
                    'kind': 'progress',
                    'text': p_text,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 单条资源数据
                if pl.get('tokens'):
                    entry['tokens'] = pl['tokens']
                if pl.get('cost'):
                    entry['cost'] = pl['cost']
                if pl.get('elapsed'):
                    entry['elapsed'] = pl['elapsed']
                activity.append(entry)
            if p_todos:
                todos_entry = {
                    'at': p_at,
                    'kind': 'todos',
                    'items': p_todos,
                    'agent': p_agent,
                    'agentLabel': pl.get('agentLabel', ''),
                    'state': p_state,
                    'org': p_org,
                }
                # 计算 diff
                diff = _compute_todos_diff(prev_todos_snapshot, p_todos)
                if diff:
                    todos_entry['diff'] = diff
                activity.append(todos_entry)
                prev_todos_snapshot = p_todos

        # 仅当无法通过状态确定 Agent 时，才回退到最后一次上报的 Agent
        if not agent_id:
            last_pl = progress_log[-1]
            if last_pl.get('agent'):
                agent_id = last_pl.get('agent')
    else:
        # 兼容旧数据：仅使用 now/todos
        if now_text:
            activity.append({
                'at': updated_at,
                'kind': 'progress',
                'text': now_text,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })
        if todos:
            activity.append({
                'at': updated_at,
                'kind': 'todos',
                'items': todos,
                'agent': agent_id or '',
                'state': state,
                'org': org,
            })

    # 按时间排序，保证流转/进展穿插正确
    activity.sort(key=lambda x: x.get('at', ''))

    if agent_id:
        related_agents.add(agent_id)

    # ── 融合 Agent Session 活动（thinking / tool_result / user）──
    # 从 session JSONL 中提取 Agent 的思考过程和工具调用记录
    try:
        session_entries = []
        # 活跃任务：尝试按 task_id 精确匹配
        if state not in ('Done', 'Cancelled'):
            if agent_id:
                entries = get_agent_activity(agent_id, limit=30, task_id=task_id)
                session_entries.extend(entries)
            # 也从其他相关 Agent 获取
            for ra in related_agents:
                if ra != agent_id:
                    entries = get_agent_activity(ra, limit=20, task_id=task_id)
                    session_entries.extend(entries)
        else:
            # 已完成任务：基于关键词匹配
            title = task.get('title', '')
            keywords = _extract_keywords(title)
            if keywords:
                agents_to_scan = list(related_agents) if related_agents else ([agent_id] if agent_id else [])
                for ra in agents_to_scan[:5]:
                    entries = get_agent_activity_by_keywords(ra, keywords, limit=15)
                    session_entries.extend(entries)
        # 去重（通过 at+kind 去重避免重复）
        existing_keys = {(a.get('at', ''), a.get('kind', '')) for a in activity}
        for se in session_entries:
            key = (se.get('at', ''), se.get('kind', ''))
            if key not in existing_keys:
                activity.append(se)
                existing_keys.add(key)
        # 重新排序
        activity.sort(key=lambda x: x.get('at', ''))
    except Exception as e:
        log.warning(f'Session JSONL 融合失败 (task={task_id}): {e}')

    # ── 融合事件账本（调度、Agent 通讯、工具调用、状态证据）──
    ledger_events = []
    if _ledger_list_events and _ledger_event_to_activity_entries:
        try:
            ledger_events = _ledger_list_events(task_id=task_id, limit=200)
            existing_keys = {_activity_key(a) for a in activity}
            for event in ledger_events:
                for entry in _ledger_event_to_activity_entries(event):
                    key = _activity_key(entry)
                    if key in existing_keys:
                        # 保留旧条目，但补上证据字段，方便前端/调试判断来源。
                        for old in activity:
                            if _activity_key(old) == key:
                                old.setdefault('eventId', entry.get('eventId', ''))
                                old.setdefault('eventKind', entry.get('eventKind', ''))
                                old.setdefault('source', 'task/session+event-ledger')
                                old.setdefault('confidence', entry.get('confidence', ''))
                                break
                        continue
                    activity.append(entry)
                    existing_keys.add(key)
            activity.sort(key=lambda x: x.get('at', ''))
            for event in ledger_events:
                if not isinstance(event, dict):
                    continue
                aid = event.get('agentId') or _dict(event.get('payload')).get('to') or ''
                if aid:
                    related_agents.add(aid)
        except Exception as e:
            log.warning(f'事件账本融合失败 (task={task_id}): {e}')

    # ── 融合 OpenCode 精确 session 工具事件 ──
    # 派发事件里的 sessionId 比关键词匹配可靠得多，用它补回 read/edit/bash 等工具证据。
    if _agent_runtime() == 'opencode':
        try:
            session_entries = []
            for session_id in _task_opencode_session_ids(task, ledger_events):
                session_entries.extend(get_opencode_session_activity(session_id, limit=120))
            if session_entries:
                existing_keys = {_activity_key(a) for a in activity}
                for entry in session_entries:
                    key = _activity_key(entry)
                    if key in existing_keys:
                        continue
                    activity.append(entry)
                    existing_keys.add(key)
                    if entry.get('agent'):
                        related_agents.add(entry.get('agent'))
                activity.sort(key=lambda x: x.get('at', ''))
        except Exception as e:
            log.warning(f'OpenCode session 融合失败 (task={task_id}): {e}')

    full_activity = activity
    activity, activity_window = _compact_activity_for_ui(full_activity)

    # ── 阶段耗时统计 ──
    phase_durations = _compute_phase_durations(flow_log)

    # ── Todos 汇总 ──
    todos_summary = _compute_todos_summary(todos)

    # ── 总耗时（首条 flow_log 到最后一条/当前） ──
    total_duration = None
    if flow_log:
        try:
            first_at = datetime.datetime.fromisoformat(flow_log[0].get('at', '').replace('Z', '+00:00'))
            if state in ('Done', 'Cancelled') and len(flow_log) >= 2:
                last_at = datetime.datetime.fromisoformat(flow_log[-1].get('at', '').replace('Z', '+00:00'))
            else:
                last_at = datetime.datetime.now(datetime.timezone.utc)
            dur = max(0, int((last_at - first_at).total_seconds()))
            if dur < 60:
                total_duration = f'{dur}秒'
            elif dur < 3600:
                total_duration = f'{dur // 60}分{dur % 60}秒'
            elif dur < 86400:
                h, rem = divmod(dur, 3600)
                total_duration = f'{h}小时{rem // 60}分'
            else:
                d, rem = divmod(dur, 86400)
                total_duration = f'{d}天{rem // 3600}小时'
        except Exception:
            pass

    last_active = None
    if updated_at:
        try:
            dt = _parse_iso(updated_at)
            if dt:
                last_active = dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')
            else:
                last_active = updated_at[:19].replace('T', ' ')
        except Exception:
            last_active = updated_at[:19].replace('T', ' ')

    result = {
        'ok': True,
        'taskId': task_id,
        'traceId': trace_id,
        'taskMeta': task_meta,
        'agentId': agent_id,
        'agentLabel': _STATE_LABELS.get(state, state),
        'lastActive': last_active,
        'activity': activity,
        'activityWindow': activity_window,
        'activitySource': 'progress+session+event-ledger' if ledger_events else 'progress+session',
        'relatedAgents': _normalize_related_agents(related_agents),
        'outputGroup': output_group,
        'phaseDurations': phase_durations,
        'totalDuration': total_duration,
        'stateEvidence': _build_state_evidence(task, ledger_events, full_activity),
        'traceSummary': _build_trace_summary(trace_id, ledger_events, full_activity, outbox_summary),
    }
    if todos_summary:
        result['todosSummary'] = todos_summary
    if has_resource_data:
        result['resourceSummary'] = {
            'totalTokens': total_tokens,
            'totalCost': round(total_cost, 4),
            'totalElapsedSec': total_elapsed,
        }
    return result


# 状态推进顺序（手动推进用）
_STATE_FLOW = {
    'Pending':  ('Taizi', '皇上', '太子', '待处理旨意转交太子分拣'),
    'Taizi':    ('Zhongshu', '太子', '中书省', '太子分拣完毕，转中书省起草'),
    'Zhongshu': ('Menxia', '中书省', '门下省', '中书省方案提交门下省审议'),
    'Menxia':   ('Assigned', '门下省', '尚书省', '门下省准奏，转尚书省派发'),
    'Assigned': ('Doing', '尚书省', '六部', '尚书省开始派发执行'),
    'Next':     ('Doing', '尚书省', '六部', '待执行任务开始执行'),
    'Doing':    ('Review', '六部', '尚书省', '各部完成，进入汇总'),
    'Review':   ('Done', '尚书省', '太子', '全流程完成，回奏太子转报皇上'),
}
_STATE_LABELS = {
    'Pending': '待处理', 'Taizi': '太子', 'Zhongshu': '中书省', 'Menxia': '门下省',
    'Assigned': '尚书省', 'Next': '待执行', 'Doing': '执行中', 'Review': '审查', 'Done': '完成',
}


def _build_dispatch_payload(task_id, task, new_state, agent_id, trigger):
    title = task.get('title', '(无标题)')
    target_dept = task.get('targetDept', '')
    trace_id = _ensure_trace_id(task)
    msgs = {
        'taizi': (
            f'📜 皇上旨意需要你处理\n'
            f'任务ID: {task_id}\n'
            f'Trace: {trace_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'如需任务详情，只能执行：python3 scripts/kanban_update.py show {task_id}；不要读取 kanban/{task_id}.json 或 data/kanban.json。\n'
            f'必须使用英文状态枚举，禁止写“处理中”等中文状态。\n'
            f'请按顺序执行：\n'
            f'1) python3 scripts/kanban_update.py progress {task_id} "太子正在整理旨意并转交中书省" "整理旨意🔄|转交中书省|中书省起草"\n'
            f'2) python3 scripts/kanban_update.py state {task_id} Zhongshu "太子已分拣，转中书省起草"\n'
            f'3) python3 scripts/kanban_update.py flow {task_id} "太子" "中书省" "📋 旨意传达：请中书省起草执行方案"\n'
            f'4) 调用 zhongshu subagent 起草方案。'
        ),
        'zhongshu': (
            f'📜 旨意已到中书省，请起草方案\n'
            f'任务ID: {task_id}\n'
            f'Trace: {trace_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务记录，请勿重复创建。直接用 kanban_update.py state 更新状态。\n'
            f'如需任务详情，先执行：python3 scripts/kanban_update.py show {task_id}；不要猜测单任务 JSON 路径。\n'
            f'请立即起草执行方案，走完完整三省流程（中书起草→门下审议→尚书派发→六部执行）。'
        ),
        'menxia': (
            f'📋 中书省方案提交审议\n'
            f'任务ID: {task_id}\n'
            f'Trace: {trace_id}\n'
            f'旨意: {title}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'如需任务详情，先执行：python3 scripts/kanban_update.py show {task_id}；不要猜测单任务 JSON 路径。\n'
            f'请审议中书省方案，给出准奏或封驳意见。'
        ),
        'shangshu': (
            f'📮 门下省已准奏，请派发执行\n'
            f'任务ID: {task_id}\n'
            f'Trace: {trace_id}\n'
            f'旨意: {title}\n'
            f'{"建议派发部门: " + target_dept if target_dept else ""}\n'
            f'⚠️ 看板已有此任务，请勿重复创建。\n'
            f'如需任务详情，先执行：python3 scripts/kanban_update.py show {task_id}；不要读取 kanban/{task_id}.json 或 data/kanban.json。\n'
            f'请先更新看板：python3 scripts/kanban_update.py progress {task_id} "尚书省正在分析方案并准备派发六部" "确认方案🔄|选择部门|派发六部|汇总回奏"\n'
            f'然后执行：python3 scripts/kanban_update.py state {task_id} Doing "尚书省派发任务给六部"\n'
            f'再执行：python3 scripts/kanban_update.py flow {task_id} "尚书省" "六部" "📮 尚书省派发六部执行"\n'
            f'最后调用需要的六部 subagent 执行并汇总。'
        ),
    }
    message = msgs.get(agent_id, (
        f'📌 请处理任务\n'
        f'任务ID: {task_id}\n'
        f'Trace: {trace_id}\n'
        f'旨意: {title}\n'
        f'⚠️ 看板已有此任务，请勿重复创建。直接用 kanban_update.py 更新状态。\n'
        f'如需任务详情，先执行：python3 scripts/kanban_update.py show {task_id}；不要猜测单任务 JSON 路径。\n'
        f'目标仓库若是外部目录，请用 bash 的 ls/find/rg/sed 查看；不要用 read 工具直接读取目录路径。\n'
        f'如果任务当前已经是 Doing，不要再执行 state {task_id} Doing；请直接 progress/flow/todo。\n'
        f'本次派发必须有明确收口：完成就先把相关 todo 标为 completed，再执行 done；不能完成就执行 block 并写明阻塞原因。'
    ))
    return {
        'message': message,
        'title': title,
        'targetDept': target_dept,
        'traceId': trace_id,
    }


def _kick_dispatch_worker():
    global _DISPATCH_WORKER_ACTIVE
    with _DISPATCH_WORKER_LOCK:
        if _DISPATCH_WORKER_ACTIVE:
            return
        _DISPATCH_WORKER_ACTIVE = True
    threading.Thread(target=_dispatch_outbox_worker, daemon=True).start()


def _dispatch_outbox_worker():
    global _DISPATCH_WORKER_ACTIVE
    try:
        while True:
            items = _outbox_claim_pending(
                worker_id=_DISPATCH_WORKER_ID,
                kinds={'handoff', 'dispatch'},
                limit=1,
            )
            if not items:
                break
            for item in items:
                if item.get('kind') == 'handoff':
                    _process_handoff_outbox_item(item)
                else:
                    _execute_dispatch_outbox_item(item)
    finally:
        with _DISPATCH_WORKER_LOCK:
            _DISPATCH_WORKER_ACTIVE = False
        if _outbox_list(status='pending', limit=1):
            _kick_dispatch_worker()


def _process_handoff_outbox_item(item):
    task_id = item.get('taskId', '')
    state = item.get('state', '')
    agent_id = item.get('agentId', '')
    trigger = item.get('trigger') or 'handoff-outbox'
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        _outbox_mark_failed(item.get('id', ''), f'task {task_id} not found')
        return
    if task.get('state') != state:
        _outbox_mark_done(item.get('id', ''), {'stale': True, 'currentState': task.get('state', '')})
        return
    expected = _expected_agent_for_task(task, state)
    if expected != agent_id:
        _outbox_mark_done(item.get('id', ''), {'stale': True, 'expectedAgent': expected})
        return
    sched = _ensure_scheduler(task)
    if (
        sched.get('activeDispatchId')
        or (
            sched.get('lastDispatchAgent') == expected
            and sched.get('lastDispatchState') == state
            and sched.get('lastDispatchStatus') in {'queued', 'success', 'progress'}
        )
    ):
        _outbox_mark_done(item.get('id', ''), {'alreadyDispatched': True})
        return
    dispatch_for_state(task_id, task, state, trigger=trigger)
    _outbox_mark_done(item.get('id', ''), {'dispatched': True, 'agentId': expected})


def _execute_dispatch_outbox_item(item):
    task_id = item.get('taskId', '')
    new_state = item.get('state', '')
    agent_id = item.get('agentId', '')
    trigger = item.get('trigger') or 'outbox-dispatch'
    dispatch_id = item.get('id', '')
    payload = item.get('payload') or {}
    trace_id = item.get('traceId') or payload.get('traceId') or ''

    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        _outbox_mark_failed(dispatch_id, f'task {task_id} not found')
        return
    task_trace_id = _ensure_trace_id(task)
    trace_id = trace_id or task_trace_id or task_id
    title = payload.get('title') or task.get('title', '(无标题)')
    msg = payload.get('message') or _build_dispatch_payload(task_id, task, new_state, agent_id, trigger)['message']
    runtime = _agent_runtime()
    runtime_label = _runtime_label()
    dispatch_env = os.environ.copy()
    dispatch_env.update({
        'EDICT_TASK_ID': task_id,
        'EDICT_TRACE_ID': trace_id,
        'EDICT_DISPATCH_ID': dispatch_id,
        'EDICT_AGENT_ID': agent_id,
        'EDICT_DISPATCH_STATE': new_state,
    })

    def _update_if_current(status, error='', session_id='', flow_remark=''):
        stale = {'value': False, 'state': '', 'activeDispatchId': ''}

        def _apply(t, s):
            if not _scheduler_dispatch_is_current(t, s, dispatch_id, new_state, agent_id):
                stale['value'] = True
                stale['state'] = t.get('state', '')
                stale['activeDispatchId'] = s.get('activeDispatchId', '')
                return
            update = {
                'lastDispatchAt': now_iso(),
                'lastDispatchStatus': status,
                'lastDispatchAgent': agent_id,
                'lastDispatchState': new_state,
                'lastDispatchTrigger': trigger,
                'lastDispatchError': error,
            }
            if session_id:
                update['lastDispatchSession'] = session_id
            s.update(update)
            s.pop('activeDispatchId', None)
            s.pop('activeDispatchState', None)
            s.pop('activeDispatchStartedAt', None)
            if flow_remark:
                _scheduler_add_flow(t, flow_remark, to=t.get('org', ''))

        updated = _update_task_scheduler(task_id, _apply)
        if stale['value']:
            log.info(
                f'ℹ️ {task_id} 忽略过期派发结果 → {agent_id} '
                f'(dispatch={dispatch_id[:8]}, state={stale["state"]}, status={status})'
            )
            _append_runtime_event('dispatch_stale_result_ignored', task_id, agent_id, {
                'from': runtime_label,
                'to': agent_id,
                'trigger': trigger,
                'status': status,
                'dispatchId': dispatch_id,
                'activeDispatchId': stale['activeDispatchId'],
                'expectedState': new_state,
                'currentState': stale['state'],
                'error': error,
                'sessionId': session_id,
                'remark': '过期派发结果已忽略',
            }, confidence='high', trace_id=trace_id)
            _outbox_mark_done(dispatch_id, {'stale': True, 'status': status})
            return False
        return bool(updated)

    try:
        import time as _time
        gw_alive = False
        for gw_attempt in range(3):
            if _check_gateway_alive():
                gw_alive = True
                break
            if gw_attempt < 2:
                _time.sleep(5 * (gw_attempt + 1))
        if not gw_alive:
            log.warning(f'⚠️ {task_id} 自动派发跳过: {runtime_label} 未启动（重试3次仍不可达）')
            _update_if_current('gateway-offline')
            _append_runtime_event('dispatch_gateway_offline', task_id, agent_id, {
                'from': runtime_label,
                'to': agent_id,
                'trigger': trigger,
                'status': 'gateway-offline',
                'dispatchId': dispatch_id,
                'remark': f'{runtime_label} 未启动，派发跳过',
            }, confidence='low', trace_id=trace_id)
            _outbox_mark_failed(dispatch_id, 'gateway offline', {'status': 'gateway-offline'})
            return
        if runtime == 'opencode':
            opencode_bin = _resolve_opencode_bin()
            if not opencode_bin:
                err = 'OpenCode CLI 未找到：请确认已安装 opencode 并加入 PATH；可设置 OPENCODE_BIN 指向 opencode 可执行文件'
                status = 'opencode-missing'
                flow_msg = f'派发异常：OpenCode CLI 未找到（{trigger}）'
                log.warning(f'⚠️ {task_id} 自动派发异常: {err}')
                _update_if_current(status, error=err, flow_remark=flow_msg)
                _append_runtime_event('dispatch_missing_cli', task_id, agent_id, {
                    'from': runtime_label,
                    'to': agent_id,
                    'trigger': trigger,
                    'status': status,
                    'dispatchId': dispatch_id,
                    'remark': flow_msg,
                    'error': err,
                }, confidence='low', trace_id=trace_id)
                _outbox_mark_failed(dispatch_id, err, {'status': status})
                return
            cmd = [
                opencode_bin, 'run',
                '--attach', _opencode_server_url(),
                '--dir', str(BASE.parent),
                '--agent', agent_id,
                '--format', 'json',
                '--title', f'{task_id} [{trace_id}] {title}'[:120],
            ]
            model = _opencode_model(agent_id)
            if model:
                cmd.extend(['--model', model])
            cmd.append(msg)
        else:
            agent_cfg = read_json(DATA / 'agent_config.json', {})
            channel = (agent_cfg.get('dispatchChannel') or '').strip()
            openclaw_bin = _resolve_openclaw_bin()
            if not openclaw_bin:
                err = 'OpenClaw CLI 未找到：请确认已安装 openclaw 并加入 PATH；Windows 可设置 OPENCLAW_BIN 指向 openclaw.cmd'
                log.warning(f'⚠️ {task_id} 自动派发异常: {err}')
                _update_if_current(
                    'openclaw-missing',
                    error=err,
                    flow_remark=f'派发异常：OpenClaw CLI 未找到（{trigger}）',
                )
                _append_runtime_event('dispatch_missing_cli', task_id, agent_id, {
                    'from': runtime_label,
                    'to': agent_id,
                    'trigger': trigger,
                    'status': 'openclaw-missing',
                    'dispatchId': dispatch_id,
                    'remark': f'派发异常：OpenClaw CLI 未找到（{trigger}）',
                    'error': err,
                }, confidence='low', trace_id=trace_id)
                _outbox_mark_failed(dispatch_id, err, {'status': 'openclaw-missing'})
                return
            cmd = [openclaw_bin, 'agent', '--agent', agent_id, '-m', msg, '--timeout', '300']
            if channel:
                cmd.extend(['--deliver', '--channel', channel])
        max_retries = 2
        err = ''
        final_status = 'failed'
        for attempt in range(1, max_retries + 1):
            log.info(f'🔄 自动派发 {task_id} → {agent_id} (第{attempt}次)...')
            _append_runtime_event('dispatch_started', task_id, agent_id, {
                'from': runtime_label,
                'to': agent_id,
                'trigger': trigger,
                'status': 'started',
                'attempt': attempt,
                'dispatchId': dispatch_id,
                'remark': f'开始派发: {agent_id} (第{attempt}次)',
            }, trace_id=trace_id)
            result = _run_capture_timeout(cmd, timeout=310, env=dispatch_env)
            if result.returncode == 0:
                session_id = _opencode_session_id_from_output(result.stdout) if runtime == 'opencode' else ''
                session_error = _opencode_session_error(session_id) if runtime == 'opencode' else ''
                if session_error:
                    err = _clean_runtime_error(session_error, limit=300)
                    log.warning(f'⚠️ {task_id} OpenCode session 报错(第{attempt}次): {err}')
                    _append_runtime_event('dispatch_failed', task_id, agent_id, {
                        'from': runtime_label,
                        'to': agent_id,
                        'trigger': trigger,
                        'status': 'agent-error',
                        'attempt': attempt,
                        'sessionId': session_id,
                        'dispatchId': dispatch_id,
                        'remark': f'OpenCode session 报错: {agent_id}（{trigger}）',
                        'error': err,
                    }, confidence='low', trace_id=trace_id, session_id=session_id)
                    if attempt < max_retries:
                        import time
                        time.sleep(5)
                        continue
                    break
                log.info(f'✅ {task_id} 自动派发成功 → {agent_id}')
                if _update_if_current(
                    'success',
                    session_id=session_id,
                    flow_remark=f'派发成功：{agent_id}（{trigger}）',
                ):
                    _outbox_mark_done(dispatch_id, {'status': 'success', 'sessionId': session_id})
                _append_runtime_event('dispatch_succeeded', task_id, agent_id, {
                    'from': runtime_label,
                    'to': agent_id,
                    'trigger': trigger,
                    'status': 'success',
                    'attempt': attempt,
                    'sessionId': session_id,
                    'dispatchId': dispatch_id,
                    'remark': f'派发成功: {agent_id}（{trigger}）',
                }, trace_id=trace_id, session_id=session_id)
                return
            err = _runtime_error_summary(
                result.stderr if result.stderr else result.stdout,
                default='runtime command failed',
                limit=300,
            )
            if runtime == 'opencode' and _is_opencode_session_not_found(err):
                final_status = 'opencode-session-stale'
                log.warning(f'⚠️ {task_id} OpenCode session registry 异常，准备重启 server 后重试')
                _append_runtime_event('dispatch_runtime_recovering', task_id, agent_id, {
                    'from': runtime_label,
                    'to': agent_id,
                    'trigger': trigger,
                    'status': final_status,
                    'attempt': attempt,
                    'dispatchId': dispatch_id,
                    'error': err,
                    'remark': 'OpenCode session registry stale，正在重启 OpenCode server 后重试',
                }, confidence='medium', trace_id=trace_id)
                if attempt < max_retries and _restart_opencode_server():
                    continue
            log.warning(f'⚠️ {task_id} 自动派发失败(第{attempt}次): {err}')
            if attempt < max_retries:
                import time
                time.sleep(5)
        log.error(f'❌ {task_id} 自动派发最终失败 → {agent_id}')
        _update_if_current(
            final_status,
            error=err,
            flow_remark=f'派发失败：{agent_id}（{trigger}）',
        )
        _append_runtime_event('dispatch_failed', task_id, agent_id, {
            'from': runtime_label,
            'to': agent_id,
            'trigger': trigger,
            'status': final_status,
            'dispatchId': dispatch_id,
            'remark': f'派发失败: {agent_id}（{trigger}）',
            'error': err,
        }, confidence='low', trace_id=trace_id)
        _outbox_mark_failed(dispatch_id, err, {'status': final_status})
    except subprocess.TimeoutExpired as exc:
        timeout_error = _runtime_error_summary(
            getattr(exc, 'stderr', '') or getattr(exc, 'output', ''),
            default=f'{runtime_label} 派发超时（{agent_id}，{trigger}）',
            limit=300,
        )
        log.error(f'❌ {task_id} 自动派发超时 → {agent_id}')
        _update_if_current(
            'timeout',
            error=timeout_error,
            flow_remark=f'派发超时：{agent_id}（{trigger}）',
        )
        _append_runtime_event('dispatch_failed', task_id, agent_id, {
            'from': runtime_label,
            'to': agent_id,
            'trigger': trigger,
            'status': 'timeout',
            'dispatchId': dispatch_id,
            'remark': f'派发超时: {agent_id}（{trigger}）',
            'error': timeout_error,
        }, confidence='low', trace_id=trace_id)
        _outbox_mark_failed(dispatch_id, timeout_error, {'status': 'timeout'})
    except FileNotFoundError as e:
        missing_runtime = 'OpenCode' if runtime == 'opencode' else 'OpenClaw'
        missing_status = 'opencode-missing' if runtime == 'opencode' else 'openclaw-missing'
        err = f'{missing_runtime} CLI 未找到：{e}'
        log.warning(f'⚠️ {task_id} 自动派发异常: {err}')
        _update_if_current(
            missing_status,
            error=err[:200],
            flow_remark=f'派发异常：{missing_runtime} CLI 未找到（{trigger}）',
        )
        _append_runtime_event('dispatch_missing_cli', task_id, agent_id, {
            'from': runtime_label,
            'to': agent_id,
            'trigger': trigger,
            'status': missing_status,
            'dispatchId': dispatch_id,
            'remark': f'派发异常：{missing_runtime} CLI 未找到（{trigger}）',
            'error': err[:200],
        }, confidence='low', trace_id=trace_id)
        _outbox_mark_failed(dispatch_id, err[:200], {'status': missing_status})
    except Exception as e:
        log.warning(f'⚠️ {task_id} 自动派发异常: {e}')
        _update_if_current(
            'error',
            error=str(e)[:200],
            flow_remark=f'派发异常：{agent_id}（{trigger}）',
        )
        _append_runtime_event('dispatch_failed', task_id, agent_id, {
            'from': runtime_label,
            'to': agent_id,
            'trigger': trigger,
            'status': 'error',
            'dispatchId': dispatch_id,
            'remark': f'派发异常: {agent_id}（{trigger}）',
            'error': str(e)[:200],
        }, confidence='low', trace_id=trace_id)
        _outbox_mark_failed(dispatch_id, str(e)[:200], {'status': 'error'})


def dispatch_for_state(task_id, task, new_state, trigger='state-transition'):
    """Record a durable dispatch request and let the outbox worker execute it."""
    agent_id = _expected_agent_for_task(task, new_state)
    if not agent_id:
        log.info(f'ℹ️ {task_id} 新状态 {new_state} 无对应 Agent，跳过自动派发')
        return

    dispatch_id = uuid.uuid4().hex
    dispatch_started_at = now_iso()
    payload = _build_dispatch_payload(task_id, task, new_state, agent_id, trigger)
    trace_id = payload.get('traceId', '')
    existing_dispatch = next((
        item for item in _outbox_list(task_id=task_id, limit=1000)
        if item.get('kind') == 'dispatch'
        and item.get('state') == new_state
        and item.get('agentId') == agent_id
        and item.get('status') not in {'done', 'failed', 'cancelled'}
    ), None)
    if existing_dispatch:
        dispatch_id = existing_dispatch.get('id') or dispatch_id
        dispatch_started_at = existing_dispatch.get('claimedAt') or existing_dispatch.get('createdAt') or dispatch_started_at
        _update_task_scheduler(task_id, lambda t, s: s.update({
            'lastDispatchAt': dispatch_started_at,
            'lastDispatchStatus': existing_dispatch.get('status') or 'queued',
            'lastDispatchAgent': agent_id,
            'lastDispatchState': new_state,
            'lastDispatchTrigger': existing_dispatch.get('trigger') or trigger,
            'lastDispatchError': '',
            'activeDispatchId': dispatch_id,
            'activeDispatchState': new_state,
            'activeDispatchStartedAt': dispatch_started_at,
        }))
        _append_runtime_event('dispatch_deduped', task_id, agent_id, {
            'from': 'scheduler',
            'to': agent_id,
            'newState': new_state,
            'trigger': trigger,
            'status': existing_dispatch.get('status') or 'queued',
            'dispatchId': dispatch_id,
            'remark': f'已有未完成派发，跳过重复入队: {new_state} -> {agent_id}',
        }, trace_id=trace_id)
        _kick_dispatch_worker()
        log.info(f'ℹ️ {task_id} 已有未完成派发，跳过重复入队 → {agent_id}')
        return

    updated = _update_task_scheduler(task_id, lambda t, s: (
        s.update({
            'lastDispatchAt': dispatch_started_at,
            'lastDispatchStatus': 'queued',
            'lastDispatchAgent': agent_id,
            'lastDispatchState': new_state,
            'lastDispatchTrigger': trigger,
            'lastDispatchError': '',
            'activeDispatchId': dispatch_id,
            'activeDispatchState': new_state,
            'activeDispatchStartedAt': dispatch_started_at,
        }),
        _scheduler_add_flow(t, f'已入队派发：{new_state} → {agent_id}（{trigger}）', to=_STATE_LABELS.get(new_state, new_state))
    ))
    if not updated:
        return

    _outbox_enqueue_dispatch(
        task_id=task_id,
        state=new_state,
        agent_id=agent_id,
        trigger=trigger,
        dispatch_id=dispatch_id,
        trace_id=trace_id,
        payload=payload,
    )
    _append_runtime_event('dispatch_queued', task_id, agent_id, {
        'from': 'scheduler',
        'to': agent_id,
        'newState': new_state,
        'trigger': trigger,
        'status': 'queued',
        'dispatchId': dispatch_id,
        'remark': f'已入队派发: {new_state} -> {agent_id}',
    }, trace_id=trace_id)
    _kick_dispatch_worker()
    log.info(f'🚀 {task_id} 推进后自动派发已入 outbox → {agent_id}')


def handle_advance_state(task_id, comment=''):
    """手动推进任务到下一阶段（解卡用），推进后自动派发对应 Agent。"""
    tasks = load_tasks()
    task = next((t for t in tasks if t.get('id') == task_id), None)
    if not task:
        return {'ok': False, 'error': f'任务 {task_id} 不存在'}
    cur = task.get('state', '')
    if cur not in _STATE_FLOW:
        return {'ok': False, 'error': f'任务 {task_id} 状态为 {cur}，无法推进'}
    _ensure_scheduler(task)
    _scheduler_snapshot(task, f'advance-before-{cur}')
    next_state, from_dept, to_dept, default_remark = _STATE_FLOW[cur]
    remark = comment or default_remark

    task['state'] = next_state
    task['now'] = f'⬇️ 手动推进：{remark}'
    task.setdefault('flow_log', []).append({
        'at': now_iso(),
        'from': from_dept,
        'to': to_dept,
        'remark': f'⬇️ 手动推进：{remark}'
    })
    _scheduler_mark_progress(task, f'手动推进 {cur} -> {next_state}')
    task['updatedAt'] = now_iso()
    save_tasks(tasks)

    # 🚀 推进后自动派发对应 Agent（Done 状态无需派发）
    if next_state != 'Done':
        dispatch_for_state(task_id, task, next_state)

    from_label = _STATE_LABELS.get(cur, cur)
    to_label = _STATE_LABELS.get(next_state, next_state)
    dispatched = ' (已自动派发 Agent)' if next_state != 'Done' else ''
    return {'ok': True, 'message': f'{task_id} {from_label} → {to_label}{dispatched}'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 只记录 4xx/5xx 错误请求
        if args and len(args) >= 1:
            status = str(args[0]) if args else ''
            if status.startswith('4') or status.startswith('5'):
                log.warning(f'{self.client_address[0]} {fmt % args}')

    def handle_error(self):
        pass  # 静默处理连接错误，避免 BrokenPipe 崩溃

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass  # 客户端断开连接，忽略

    def do_OPTIONS(self):
        self.send_response(200)
        cors_headers(self)
        self.end_headers()

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_file(self, path: pathlib.Path, mime='text/html; charset=utf-8', download_name=None):
        if not path.exists():
            self.send_error(404)
            return
        try:
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            if download_name:
                self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{quote(download_name)}")
            cors_headers(self)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _serve_static(self, rel_path):
        """从 dist/ 目录提供静态文件。"""
        safe = rel_path.replace('\\', '/').lstrip('/')
        if '..' in safe:
            self.send_error(403)
            return True
        fp = DIST / safe
        if fp.is_file():
            mime = _MIME_TYPES.get(fp.suffix.lower(), 'application/octet-stream')
            self.send_file(fp, mime)
            return True
        return False

    def _check_auth(self):
        """检查认证，未通过返回 True（已发送 401 响应）。"""
        p = urlparse(self.path).path.rstrip('/')
        if not requires_auth(p):
            return False
        token = extract_token(self.headers)
        if not token or not verify_token(token):
            self.send_json({'ok': False, 'error': '未登录或会话已过期'}, 401)
            return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path.rstrip('/')
        qs = parse_qs(parsed.query)
        # 认证状态端点（公开）
        if p == '/api/auth/status':
            self.send_json({'enabled': auth_enabled(), 'configured': auth_configured()})
            return
        if self._check_auth():
            return
        if p in ('', '/dashboard', '/dashboard.html'):
            self.send_file(DIST / 'index.html')
        elif p == '/healthz':
            task_data_dir = get_task_data_dir()
            checks = {'dataDir': task_data_dir.is_dir(), 'tasksReadable': (task_data_dir / 'tasks_source.json').exists()}
            checks['dataWritable'] = os.access(str(task_data_dir), os.W_OK)
            all_ok = all(checks.values())
            self.send_json({'status': 'ok' if all_ok else 'degraded', 'ts': now_iso(), 'checks': checks})
        elif p == '/api/live-status':
            task_data_dir = get_task_data_dir()
            self.send_json(read_json(task_data_dir / 'live_status.json'))
        elif p == '/api/agent-config':
            self.send_json(read_json(DATA / 'agent_config.json'))
        elif p == '/api/capabilities':
            self.send_json(list_capabilities())
        elif p == '/api/run-specs':
            try:
                limit = int((qs.get('limit') or ['100'])[0])
            except Exception:
                limit = 100
            self.send_json(list_run_specs(limit))
        elif p == '/api/model-change-log':
            self.send_json(read_json(DATA / 'model_change_log.json', []))
        elif p == '/api/last-result':
            self.send_json(read_json(DATA / 'last_model_change_result.json', {}))
        elif p == '/api/officials-stats':
            self.send_json(read_json(DATA / 'officials_stats.json', {}))
        elif p == '/api/morning-brief':
            self.send_json(read_json(DATA / 'morning_brief.json', {}))
        elif p == '/api/morning-config':
            migrate_notification_config()
            self.send_json(read_json(DATA / 'morning_brief_config.json', {
                'categories': [
                    {'name': '政治', 'enabled': True},
                    {'name': '军事', 'enabled': True},
                    {'name': '经济', 'enabled': True},
                    {'name': 'AI大模型', 'enabled': True},
                ],
                'keywords': [], 'custom_feeds': [],
                'notification': {'enabled': True, 'channel': 'feishu', 'webhook': ''},
            }))
        elif p == '/api/notification-channels':
            self.send_json({'ok': True, 'channels': get_channel_info()})
        elif p.startswith('/api/morning-brief/'):
            date = p.split('/')[-1]
            # 标准化日期格式为 YYYYMMDD（兼容 YYYY-MM-DD 输入）
            date_clean = date.replace('-', '')
            if not date_clean.isdigit() or len(date_clean) != 8:
                self.send_json({'ok': False, 'error': f'日期格式无效: {date}，请使用 YYYYMMDD'}, 400)
                return
            self.send_json(read_json(DATA / f'morning_brief_{date_clean}.json', {}))
        elif p == '/api/remote-skills-list':
            self.send_json(get_remote_skills_list())
        elif p.startswith('/api/skill-content/'):
            # /api/skill-content/{agentId}/{skillName}
            parts = p.replace('/api/skill-content/', '').split('/', 1)
            if len(parts) == 2:
                self.send_json(read_skill_content(parts[0], parts[1]))
            else:
                self.send_json({'ok': False, 'error': 'Usage: /api/skill-content/{agentId}/{skillName}'}, 400)
        elif p.startswith('/api/task-activity/'):
            task_id = p.replace('/api/task-activity/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_task_activity(task_id))
        elif p.startswith('/api/scheduler-state/'):
            task_id = p.replace('/api/scheduler-state/', '')
            if not task_id:
                self.send_json({'ok': False, 'error': 'task_id required'}, 400)
            else:
                self.send_json(get_scheduler_state(task_id))
        elif p == '/api/runtime-outbox':
            try:
                limit = int((qs.get('limit') or ['8'])[0])
            except Exception:
                limit = 8
            self.send_json(get_runtime_outbox_health(limit))
        elif p == '/api/agents-status':
            self.send_json(get_agents_status())
        elif p.startswith('/api/coding-session/'):
            task_id = p.replace('/api/coding-session/', '')
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid task_id'}, 400)
            else:
                result = get_task_coding_session(task_id)
                self.send_json(result, 200 if result.get('ok') else 404)
        elif p.startswith('/api/patch-reviews/'):
            task_id = p.replace('/api/patch-reviews/', '')
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid task_id'}, 400)
            else:
                self.send_json({'ok': True, 'taskId': task_id, 'reviews': [_patch_review_public(item) for item in list_patch_reviews(task_id)]})
        elif p == '/api/source-file':
            rel_path = (qs.get('path') or [''])[0]
            try:
                start = int((qs.get('start') or ['0'])[0] or 0)
            except Exception:
                start = 0
            try:
                end = int((qs.get('end') or ['0'])[0] or 0)
            except Exception:
                end = 0
            try:
                context = int((qs.get('context') or ['4'])[0] or 4)
            except Exception:
                context = 4
            result = read_source_file(rel_path, start, end, max(0, min(context, 20)))
            self.send_json(result, 200 if result.get('ok') else 404)
        elif p == '/api/output-files':
            try:
                limit = int((qs.get('limit') or ['300'])[0])
            except Exception:
                limit = 300
            self.send_json(list_output_files(max(1, min(limit, 1000))))
        elif p == '/api/output-file':
            rel_path = (qs.get('path') or [''])[0]
            output_file = _safe_project_file(rel_path)
            if not output_file:
                self.send_json({'ok': False, 'error': 'file not found or not allowed'}, 404)
            else:
                mime = _MIME_TYPES.get(output_file.suffix.lower(), 'application/octet-stream')
                download = (qs.get('download') or [''])[0] in ('1', 'true', 'yes')
                self.send_file(output_file, mime, output_file.name if download else None)
        elif p.startswith('/api/task-output/'):
            task_id = p.replace('/api/task-output/', '')
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid task_id'}, 400)
            else:
                tasks = load_tasks()
                task = next((t for t in tasks if t.get('id') == task_id), None)
                if not task:
                    self.send_json({'ok': False, 'error': 'task not found'}, 404)
                else:
                    output_path = task.get('output', '')
                    if not output_path or output_path == '-':
                        self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                    else:
                        p_out = pathlib.Path(output_path)
                        if not p_out.exists():
                            self.send_json({'ok': True, 'taskId': task_id, 'content': '', 'exists': False})
                        else:
                            try:
                                content = p_out.read_text(encoding='utf-8', errors='replace')[:50000]
                                self.send_json({'ok': True, 'taskId': task_id, 'content': content, 'exists': True})
                            except Exception as e:
                                self.send_json({'ok': False, 'error': f'读取失败: {e}'}, 500)
        elif p.startswith('/api/agent-activity/'):
            agent_id = p.replace('/api/agent-activity/', '')
            if not agent_id or not _SAFE_NAME_RE.match(agent_id):
                self.send_json({'ok': False, 'error': 'invalid agent_id'}, 400)
            else:
                self.send_json({'ok': True, 'agentId': agent_id, 'activity': get_agent_activity(agent_id)})
        # ── 朝堂议政 ──
        elif p == '/api/court-discuss/list':
            self.send_json({'ok': True, 'sessions': cd_list()})
        elif p == '/api/court-discuss/officials':
            self.send_json({'ok': True, 'officials': CD_PROFILES})
        elif p.startswith('/api/court-discuss/session/'):
            sid = p.replace('/api/court-discuss/session/', '')
            data = cd_get(sid)
            self.send_json(data if data else {'ok': False, 'error': 'session not found'}, 200 if data else 404)
        elif p == '/api/court-discuss/fate':
            self.send_json({'ok': True, 'event': cd_fate()})
        elif self._serve_static(p):
            pass  # 已由 _serve_static 处理 (JS/CSS/图片等)
        else:
            # SPA fallback：非 /api/ 路径返回 index.html
            if not p.startswith('/api/'):
                idx = DIST / 'index.html'
                if idx.exists():
                    self.send_file(idx)
                    return
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path.rstrip('/')
        length = int(self.headers.get('Content-Length', 0))
        if length > MAX_REQUEST_BODY:
            self.send_json({'ok': False, 'error': f'Request body too large (max {MAX_REQUEST_BODY} bytes)'}, 413)
            return
        raw = self.rfile.read(length) if length else b''
        try:
            body = json.loads(raw) if raw else {}
        except Exception:
            self.send_json({'ok': False, 'error': 'invalid JSON'}, 400)
            return

        # ── 认证端点（公开） ──
        if p == '/api/auth/setup':
            pw = body.get('password', '')
            if not isinstance(pw, str) or not pw:
                self.send_json({'ok': False, 'error': '请提供密码'}, 400)
                return
            self.send_json(setup_password(pw))
            return
        if p == '/api/auth/login':
            pw = body.get('password', '')
            if not isinstance(pw, str) or not pw:
                self.send_json({'ok': False, 'error': '请提供密码'}, 400)
                return
            if verify_password(pw):
                token = create_token()
                resp = {'ok': True, 'token': token}
                # 同时设置 HttpOnly cookie
                try:
                    body_bytes = json.dumps(resp, ensure_ascii=False).encode()
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json; charset=utf-8')
                    self.send_header('Content-Length', str(len(body_bytes)))
                    self.send_header('Set-Cookie', f'edict_token={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400')
                    cors_headers(self)
                    self.end_headers()
                    self.wfile.write(body_bytes)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self.send_json({'ok': False, 'error': '密码错误'}, 401)
            return

        # ── 认证检查 ──
        if self._check_auth():
            return

        if p == '/api/morning-config':
            if not isinstance(body, dict):
                self.send_json({'ok': False, 'error': '请求体必须是 JSON 对象'}, 400)
                return
            allowed_keys = {'categories', 'keywords', 'custom_feeds', 'notification', 'feishu_webhook'}
            unknown = set(body.keys()) - allowed_keys
            if unknown:
                self.send_json({'ok': False, 'error': f'未知字段: {", ".join(unknown)}'}, 400)
                return
            if 'categories' in body and not isinstance(body['categories'], list):
                self.send_json({'ok': False, 'error': 'categories 必须是数组'}, 400)
                return
            if 'keywords' in body and not isinstance(body['keywords'], list):
                self.send_json({'ok': False, 'error': 'keywords 必须是数组'}, 400)
                return
            if 'notification' in body:
                noti = body['notification']
                if not isinstance(noti, dict):
                    self.send_json({'ok': False, 'error': 'notification 必须是对象'}, 400)
                    return
                channel_type = noti.get('channel', 'feishu')
                if channel_type not in NOTIFICATION_CHANNELS:
                    self.send_json({'ok': False, 'error': f'不支持的渠道: {channel_type}'}, 400)
                    return
                webhook = noti.get('webhook', '').strip()
                if webhook:
                    channel_cls = get_channel(channel_type)
                    if channel_cls and not channel_cls.validate_webhook(webhook):
                        self.send_json({'ok': False, 'error': f'{channel_cls.label} Webhook URL 无效'}, 400)
                        return
            webhook_legacy = body.get('feishu_webhook', '').strip()
            if webhook_legacy and 'notification' not in body:
                body['notification'] = {'enabled': True, 'channel': 'feishu', 'webhook': webhook_legacy}
            cfg_path = DATA / 'morning_brief_config.json'
            cfg_path.write_text(json.dumps(body, ensure_ascii=False, indent=2))
            self.send_json({'ok': True, 'message': '订阅配置已保存'})
            return

        if p == '/api/scheduler-scan':
            threshold_sec = body.get('thresholdSec', 180)
            try:
                result = handle_scheduler_scan(threshold_sec)
                self.send_json(result)
            except Exception as e:
                self.send_json({'ok': False, 'error': f'scheduler scan failed: {e}'}, 500)
            return

        if p == '/api/repair-flow-order':
            try:
                self.send_json(handle_repair_flow_order())
            except Exception as e:
                self.send_json({'ok': False, 'error': f'repair flow order failed: {e}'}, 500)
            return

        if p == '/api/scheduler-retry':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_retry(task_id, reason))
            return

        if p == '/api/scheduler-escalate':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_escalate(task_id, reason))
            return

        if p == '/api/scheduler-rollback':
            task_id = body.get('taskId', '').strip()
            reason = body.get('reason', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            self.send_json(handle_scheduler_rollback(task_id, reason))
            return

        if p == '/api/runtime-outbox/retry':
            item_id = body.get('itemId', '').strip() if isinstance(body.get('itemId'), str) else ''
            reason = body.get('reason', '').strip() if isinstance(body.get('reason'), str) else ''
            result = handle_runtime_outbox_retry(item_id, reason)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/runtime-outbox/archive':
            item_id = body.get('itemId', '').strip() if isinstance(body.get('itemId'), str) else ''
            task_id = body.get('taskId', '').strip() if isinstance(body.get('taskId'), str) else ''
            reason = body.get('reason', '').strip() if isinstance(body.get('reason'), str) else ''
            archive_all_failed = bool(body.get('archiveAllFailed'))
            result = handle_runtime_outbox_archive(item_id, archive_all_failed, task_id, reason)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/patch-review/create':
            task_id = body.get('taskId', '').strip() if isinstance(body.get('taskId'), str) else ''
            paths = body.get('paths') if isinstance(body.get('paths'), list) else []
            if not task_id or not _SAFE_NAME_RE.match(task_id):
                self.send_json({'ok': False, 'error': 'invalid taskId'}, 400)
                return
            result = create_patch_review(task_id, paths)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/patch-review/action':
            patch_id = body.get('patchId', '').strip() if isinstance(body.get('patchId'), str) else ''
            action = body.get('action', '').strip() if isinstance(body.get('action'), str) else ''
            reason = body.get('reason', '').strip() if isinstance(body.get('reason'), str) else ''
            if not patch_id:
                self.send_json({'ok': False, 'error': 'patchId required'}, 400)
                return
            result = handle_patch_review_action(patch_id, action, reason)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/open-source-file':
            path = body.get('path', '').strip() if isinstance(body.get('path'), str) else ''
            line = body.get('startLine') or body.get('line') or 0
            if not path:
                self.send_json({'ok': False, 'error': 'path required'}, 400)
                return
            result = open_source_file(path, line)
            self.send_json(result, 200 if result.get('ok') else 404)
            return

        if p == '/api/morning-brief/refresh':
            force = body.get('force', True)  # 从看板手动触发默认强制
            def do_refresh():
                try:
                    cmd = [python_bin(), str(SCRIPTS / 'fetch_morning_news.py')]
                    if force:
                        cmd.append('--force')
                    subprocess.run(cmd, timeout=120)
                    push_to_feishu()
                except Exception as e:
                    print(f'[refresh error] {e}', file=sys.stderr)
            threading.Thread(target=do_refresh, daemon=True).start()
            self.send_json({'ok': True, 'message': '采集已触发，约30-60秒后刷新'})
            return

        if p == '/api/add-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', body.get('name', '')).strip()
            desc = body.get('description', '').strip() or skill_name
            trigger = body.get('trigger', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = add_skill_to_agent(agent_id, skill_name, desc, trigger)
            self.send_json(result)
            return

        if p == '/api/add-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            source_url = body.get('sourceUrl', '').strip()
            description = body.get('description', '').strip()
            if not agent_id or not skill_name or not source_url:
                self.send_json({'ok': False, 'error': 'agentId, skillName, and sourceUrl required'}, 400)
                return
            result = add_remote_skill(agent_id, skill_name, source_url, description)
            self.send_json(result)
            return

        if p == '/api/remote-skills-list':
            result = get_remote_skills_list()
            self.send_json(result)
            return

        if p == '/api/update-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = update_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/remove-remote-skill':
            agent_id = body.get('agentId', '').strip()
            skill_name = body.get('skillName', '').strip()
            if not agent_id or not skill_name:
                self.send_json({'ok': False, 'error': 'agentId and skillName required'}, 400)
                return
            result = remove_remote_skill(agent_id, skill_name)
            self.send_json(result)
            return

        if p == '/api/task-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # stop, cancel, resume
            reason = body.get('reason', '').strip() or f'皇上从看板{action}'
            if not task_id or action not in ('stop', 'cancel', 'resume'):
                self.send_json({'ok': False, 'error': 'taskId and action(stop/cancel/resume) required'}, 400)
                return
            result = handle_task_action(task_id, action, reason)
            self.send_json(result)
            return

        if p == '/api/archive-task':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            archived = body.get('archived', True)
            archive_all = body.get('archiveAllDone', False)
            if not task_id and not archive_all:
                self.send_json({'ok': False, 'error': 'taskId or archiveAllDone required'}, 400)
                return
            result = handle_archive_task(task_id, archived, archive_all)
            self.send_json(result)
            return

        if p == '/api/delete-archived':
            task_id = body.get('taskId', '').strip() if body.get('taskId') else ''
            delete_all = body.get('deleteAll', False)
            if not task_id and not delete_all:
                self.send_json({'ok': False, 'error': 'taskId or deleteAll required'}, 400)
                return
            result = handle_delete_archived(task_id, delete_all)
            self.send_json(result)
            return

        if p == '/api/task-todos':
            task_id = body.get('taskId', '').strip()
            todos = body.get('todos', [])  # [{id, title, status}]
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            # todos 输入校验
            if not isinstance(todos, list) or len(todos) > 200:
                self.send_json({'ok': False, 'error': 'todos must be a list (max 200 items)'}, 400)
                return
            valid_statuses = {'not-started', 'in-progress', 'completed'}
            for td in todos:
                if not isinstance(td, dict) or 'id' not in td or 'title' not in td:
                    self.send_json({'ok': False, 'error': 'each todo must have id and title'}, 400)
                    return
                if td.get('status', 'not-started') not in valid_statuses:
                    td['status'] = 'not-started'
            result = update_task_todos(task_id, todos)
            self.send_json(result)
            return

        if p == '/api/create-task':
            title = body.get('title', '').strip()
            org = body.get('org', '中书省').strip()
            official = body.get('official', '中书令').strip()
            priority = body.get('priority', 'normal').strip()
            template_id = body.get('templateId', '')
            params = body.get('params', {})
            if not title:
                self.send_json({'ok': False, 'error': 'title required'}, 400)
                return
            target_dept = body.get('targetDept', '').strip()
            result = handle_create_task(title, org, official, priority, template_id, params, target_dept)
            self.send_json(result)
            return

        if p == '/api/runs/create':
            result = create_run_spec(body)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/runs/preview':
            result = preview_run_spec(body)
            self.send_json(result, 200 if result.get('ok') else 400)
            return

        if p == '/api/review-action':
            task_id = body.get('taskId', '').strip()
            action = body.get('action', '').strip()  # approve, reject
            comment = body.get('comment', '').strip()
            if not task_id or action not in ('approve', 'reject'):
                self.send_json({'ok': False, 'error': 'taskId and action(approve/reject) required'}, 400)
                return
            result = handle_review_action(task_id, action, comment)
            self.send_json(result)
            return

        if p == '/api/advance-state':
            task_id = body.get('taskId', '').strip()
            comment = body.get('comment', '').strip()
            if not task_id:
                self.send_json({'ok': False, 'error': 'taskId required'}, 400)
                return
            result = handle_advance_state(task_id, comment)
            self.send_json(result)
            return

        if p == '/api/agent-wake':
            agent_id = body.get('agentId', '').strip()
            message = body.get('message', '').strip()
            if not agent_id:
                self.send_json({'ok': False, 'error': 'agentId required'}, 400)
                return
            result = wake_agent(agent_id, message)
            self.send_json(result)
            return

        if p == '/api/set-model':
            agent_id = body.get('agentId', '').strip()
            model = body.get('model', '').strip()
            if not agent_id or not model:
                self.send_json({'ok': False, 'error': 'agentId and model required'}, 400)
                return

            # Write to pending (atomic)
            pending_path = DATA / 'pending_model_changes.json'
            def update_pending(current):
                current = [x for x in current if x.get('agentId') != agent_id]
                current.append({'agentId': agent_id, 'model': model})
                return current
            atomic_json_update(pending_path, update_pending, [])

            # Async apply
            def apply_async():
                try:
                    subprocess.run([python_bin(), str(SCRIPTS / 'apply_model_changes.py')], timeout=30)
                    sync_script = 'sync_opencode_agents.py' if _agent_runtime() == 'opencode' else 'sync_agent_config.py'
                    subprocess.run([python_bin(), str(SCRIPTS / sync_script)], timeout=10)
                except Exception as e:
                    print(f'[apply error] {e}', file=sys.stderr)

            threading.Thread(target=apply_async, daemon=True).start()
            self.send_json({'ok': True, 'message': f'Queued: {agent_id} → {model}'})

        # Fix #139: 设置派发渠道（feishu/telegram/wecom/signal/tui）
        elif p == '/api/set-dispatch-channel':
            channel = body.get('channel', '').strip()
            allowed = {'feishu', 'telegram', 'wecom', 'signal', 'tui', 'discord', 'slack'}
            if not channel or channel not in allowed:
                self.send_json({'ok': False, 'error': f'channel must be one of: {", ".join(sorted(allowed))}'}, 400)
                return
            def _set_channel(cfg):
                cfg['dispatchChannel'] = channel
                return cfg
            atomic_json_update(DATA / 'agent_config.json', _set_channel, {})
            self.send_json({'ok': True, 'message': f'派发渠道已切换为 {channel}'})

        # ── 朝堂议政 POST ──
        elif p == '/api/court-discuss/start':
            topic = body.get('topic', '').strip()
            officials = body.get('officials', [])
            task_id = body.get('taskId', '').strip()
            if not topic:
                self.send_json({'ok': False, 'error': 'topic required'}, 400)
                return
            if not officials or not isinstance(officials, list):
                self.send_json({'ok': False, 'error': 'officials list required'}, 400)
                return
            # 校验官员 ID
            valid_ids = set(CD_PROFILES.keys())
            officials = [o for o in officials if o in valid_ids]
            if len(officials) < 2:
                self.send_json({'ok': False, 'error': '至少选择2位官员'}, 400)
                return
            self.send_json(cd_create(topic, officials, task_id))

        elif p == '/api/court-discuss/advance':
            sid = body.get('sessionId', '').strip()
            user_msg = body.get('userMessage', '').strip() or None
            decree = body.get('decree', '').strip() or None
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_advance(sid, user_msg, decree))

        elif p == '/api/court-discuss/conclude':
            sid = body.get('sessionId', '').strip()
            if not sid:
                self.send_json({'ok': False, 'error': 'sessionId required'}, 400)
                return
            self.send_json(cd_conclude(sid))

        elif p == '/api/court-discuss/destroy':
            sid = body.get('sessionId', '').strip()
            if sid:
                cd_destroy(sid)
            self.send_json({'ok': True})

        else:
            self.send_error(404)


def main():
    parser = argparse.ArgumentParser(description='三省六部看板服务器')
    parser.add_argument('--port', type=int, default=7891)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--cors', default=None, help='Allowed CORS origin (default: reflect request Origin header)')
    args = parser.parse_args()

    global ALLOWED_ORIGIN, _DASHBOARD_PORT, _DEFAULT_ORIGINS
    ALLOWED_ORIGIN = args.cors
    _DASHBOARD_PORT = args.port
    _DEFAULT_ORIGINS = _DEFAULT_ORIGINS | {
        f'http://127.0.0.1:{args.port}', f'http://localhost:{args.port}',
    }

    server = HTTPServer((args.host, args.port), Handler)
    log.info(f'三省六部看板启动 → http://{args.host}:{args.port}')
    print(f'   按 Ctrl+C 停止')

    auth_init(DATA)
    if auth_enabled():
        log.info('🔒 JWT 认证已启用')
    else:
        log.info('🔓 认证未配置，所有 API 公开访问（POST /api/auth/setup 设置密码）')

    migrate_notification_config()

    # 启动恢复：重新派发上次被 kill 中断的 queued 任务
    threading.Timer(3.0, _startup_recover_queued_dispatches).start()

    # 定时巡检：每 120 秒自动扫描停滞任务并触发重试/升级/回滚
    def _periodic_scheduler_scan():
        while True:
            try:
                import time as _time
                _time.sleep(120)
                result = handle_scheduler_scan(threshold_sec=180)
                count = result.get('count', 0) if isinstance(result, dict) else 0
                if count > 0:
                    log.info(f'🔍 定时巡检：{count} 个动作')
            except Exception as e:
                log.warning(f'定时巡检异常: {e}')
    threading.Thread(target=_periodic_scheduler_scan, daemon=True).start()
    log.info('🔍 定时巡检已启动（每120秒）')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
