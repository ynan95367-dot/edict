#!/usr/bin/env python3
"""Generate project-local OpenCode agent config from 三省六部 SOUL files."""
import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time


BASE = pathlib.Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
OPENCODE_CFG = BASE / 'opencode.json'
OPENCODE_DIR = BASE / '.opencode'
PROMPTS_DIR = BASE / '.opencode' / 'prompts'
MODEL_CACHE = DATA / 'opencode_models_cache.json'
MODEL_CACHE_TTL_SEC = 300

AGENT_ORDER = [
    'taizi', 'zhongshu', 'menxia', 'shangshu',
    'hubu', 'libu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr',
    'zaochao', 'qintianjian',
]

ID_LABEL = {
    'taizi': {'label': '太子', 'role': '太子', 'duty': '飞书消息分拣与回奏', 'emoji': '🤴'},
    'zhongshu': {'label': '中书省', 'role': '中书令', 'duty': '起草任务令与优先级', 'emoji': '📜'},
    'menxia': {'label': '门下省', 'role': '侍中', 'duty': '审议与退回机制', 'emoji': '🔍'},
    'shangshu': {'label': '尚书省', 'role': '尚书令', 'duty': '派单与升级裁决', 'emoji': '📮'},
    'libu': {'label': '礼部', 'role': '礼部尚书', 'duty': '文档/UI/对外沟通', 'emoji': '📝'},
    'hubu': {'label': '户部', 'role': '户部尚书', 'duty': '数据/资源/成本', 'emoji': '💰'},
    'bingbu': {'label': '兵部', 'role': '兵部尚书', 'duty': '工程实现与架构设计', 'emoji': '⚔️'},
    'xingbu': {'label': '刑部', 'role': '刑部尚书', 'duty': '质量保障与合规审计', 'emoji': '⚖️'},
    'gongbu': {'label': '工部', 'role': '工部尚书', 'duty': '基础设施与部署运维', 'emoji': '🔧'},
    'libu_hr': {'label': '吏部', 'role': '吏部尚书', 'duty': '人事/培训/Agent管理', 'emoji': '👔'},
    'zaochao': {'label': '钦天监', 'role': '朝报官', 'duty': '每日新闻采集与简报', 'emoji': '📰'},
    'qintianjian': {'label': '钦天监', 'role': '监正', 'duty': '数据分析与趋势预测', 'emoji': '🔭'},
}

GROUPS = {
    'taizi': 'sansheng',
    'zhongshu': 'sansheng',
    'menxia': 'sansheng',
    'shangshu': 'sansheng',
    'hubu': 'liubu',
    'libu': 'liubu',
    'bingbu': 'liubu',
    'xingbu': 'liubu',
    'gongbu': 'liubu',
    'libu_hr': 'liubu',
    'qintianjian': 'liubu',
}

ALLOW_AGENTS = {
    'taizi': ['zhongshu'],
    'zhongshu': ['menxia', 'shangshu'],
    'menxia': ['zhongshu'],
    'shangshu': ['hubu', 'libu', 'bingbu', 'xingbu', 'gongbu', 'libu_hr', 'qintianjian'],
}

DEFAULT_PERMISSION = {
    'read': 'allow',
    'edit': 'allow',
    'glob': 'allow',
    'grep': 'allow',
    'list': 'allow',
    'bash': 'allow',
    'task': 'allow',
    'todowrite': 'allow',
    'webfetch': 'allow',
    'websearch': 'allow',
    'external_directory': 'allow',
}

OPENCODE_MODEL_PRESETS = [
    {'id': 'opencode/deepseek-v4-flash-free', 'label': 'DeepSeek V4 Flash Free', 'provider': 'OpenCode'},
    {'id': 'opencode/big-pickle', 'label': 'Big Pickle', 'provider': 'OpenCode'},
    {'id': 'opencode/mimo-v2.5-free', 'label': 'Mimo V2.5 Free', 'provider': 'OpenCode'},
    {'id': 'opencode/nemotron-3-super-free', 'label': 'Nemotron 3 Super Free', 'provider': 'OpenCode'},
]

_PRESET_BY_ID = {m['id']: m for m in OPENCODE_MODEL_PRESETS}


def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding='utf-8') if path.exists() else ''


def read_json(path: pathlib.Path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def atomic_write_json(path: pathlib.Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write('\n')
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def provider_for_model(model_id: str) -> str:
    prefix = model_id.split('/', 1)[0] if '/' in model_id else ''
    return {
        'opencode': 'OpenCode',
        'github-copilot': 'GitHub Copilot',
        'copilot': 'Copilot',
        'anthropic': 'Anthropic',
        'openai': 'OpenAI',
        'openai-codex': 'OpenAI Codex',
        'google': 'Google',
    }.get(prefix, prefix or 'Custom')


def label_for_model(model_id: str) -> str:
    preset = _PRESET_BY_ID.get(model_id)
    if preset:
        return preset['label']
    raw = model_id.split('/', 1)[-1].replace('-', ' ').replace('_', ' ')
    parts = []
    for token in raw.split():
        lower = token.lower()
        if lower in {'gpt', 'api', 'v4', 'v5'}:
            parts.append(token.upper())
        elif lower.startswith('gpt'):
            parts.append(token.upper())
        else:
            parts.append(token[:1].upper() + token[1:])
    return ' '.join(parts) or model_id


def normalize_model_entry(entry, default_provider: str = ''):
    if isinstance(entry, str):
        model_id = entry.strip()
        label = ''
        provider = default_provider
    elif isinstance(entry, dict):
        model_id = str(entry.get('id') or entry.get('name') or '').strip()
        label = str(entry.get('label') or entry.get('name') or '').strip()
        provider = str(entry.get('provider') or default_provider or '').strip()
    else:
        return None
    if not model_id:
        return None
    preset = _PRESET_BY_ID.get(model_id, {})
    return {
        'id': model_id,
        'label': label or preset.get('label') or label_for_model(model_id),
        'provider': provider or preset.get('provider') or provider_for_model(model_id),
    }


def cached_opencode_models() -> list[dict]:
    cached = read_json(MODEL_CACHE, {})
    if not isinstance(cached, dict):
        return []
    models = cached.get('models') if isinstance(cached.get('models'), list) else []
    generated_at = cached.get('generatedAt') or 0
    try:
        fresh = time.time() - float(generated_at) < MODEL_CACHE_TTL_SEC
    except Exception:
        fresh = False
    if not fresh:
        return []
    return [m for m in (normalize_model_entry(item) for item in models) if m]


def discover_opencode_models() -> list[dict]:
    """Discover OpenCode models with a short TTL cache.

    The dashboard refresh loop calls this script often, so the CLI probe is
    cached and bounded. Configured models and presets still work when the CLI
    is unavailable.
    """
    cached = cached_opencode_models()
    if cached:
        return cached

    configured_bin = os.environ.get('OPENCODE_BIN', '').strip()
    bin_path = configured_bin if configured_bin and pathlib.Path(configured_bin).exists() else shutil.which('opencode')
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, 'models'],
            cwd=str(BASE),
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []

    models = []
    for line in proc.stdout.splitlines():
        model_id = line.strip()
        if not model_id or model_id.startswith('#'):
            continue
        entry = normalize_model_entry(model_id)
        if entry:
            models.append(entry)
    if models:
        atomic_write_json(MODEL_CACHE, {'generatedAt': time.time(), 'models': models})
    return models


def collect_opencode_models(cfg: dict, existing_models: list, default_model: str = '') -> list[dict]:
    """Merge OpenCode-discovered, configured, preset, and historical models.

    Configured/current models are inserted first so a running model is always
    selectable even when OpenCode discovery fails or returns a trimmed list.
    """
    merged = []
    seen = set()

    def add(entry, provider: str = ''):
        normalized = normalize_model_entry(entry, provider)
        if not normalized or normalized['id'] in seen:
            return
        seen.add(normalized['id'])
        merged.append(normalized)

    for model_id in (
        default_model,
        os.environ.get('OPENCODE_MODEL'),
        os.environ.get('OPENCODE_DEFAULT_MODEL'),
        cfg.get('model') if isinstance(cfg, dict) else '',
    ):
        add(model_id)

    cfg_agents = cfg.get('agent') if isinstance(cfg.get('agent'), dict) else {}
    for entry in cfg_agents.values():
        if isinstance(entry, dict):
            add(entry.get('model'))

    for entry in discover_opencode_models():
        add(entry)
    for entry in OPENCODE_MODEL_PRESETS:
        add(entry)
    for entry in existing_models or []:
        add(entry)

    return merged


def dashboard_agent_models() -> dict[str, str]:
    existing = read_json(DATA / 'agent_config.json', {})
    if not isinstance(existing, dict):
        return {}
    models = {}
    for agent in existing.get('agents') or []:
        if not isinstance(agent, dict):
            continue
        agent_id = str(agent.get('id') or '').strip()
        model = str(agent.get('model') or '').strip()
        if agent_id and model:
            models[agent_id] = model
    return models


def latest_logged_agent_models() -> dict[str, str]:
    """Recover the latest explicit UI model choices from the append-only log."""
    changes = read_json(DATA / 'model_change_log.json', [])
    if not isinstance(changes, list):
        return {}
    models = {}
    for item in changes:
        if not isinstance(item, dict):
            continue
        if item.get('runtime') != 'opencode':
            continue
        agent_id = str(item.get('agentId') or '').strip()
        model = str(item.get('newModel') or '').strip()
        if agent_id and model:
            models[agent_id] = model
    return models


def cleanup_unmanaged_opencode_artifacts() -> None:
    """Keep only project-owned OpenCode prompt files under .opencode/."""
    for path in (
        OPENCODE_DIR / 'node_modules',
        OPENCODE_DIR / 'package.json',
        OPENCODE_DIR / 'package-lock.json',
        OPENCODE_DIR / '.gitignore',
    ):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError:
            # OpenCode may keep package artifacts busy while the server is
            # running.  These files are only cleanup targets; failing to remove
            # them must not block regenerating agent/model config.
            pass


def build_prompt(agent_id: str) -> str:
    meta = ID_LABEL[agent_id]
    parts = [
        '# OpenCode 运行时适配\n'
        f'你正在 OpenCode 中担任「{meta["label"]} / {meta["role"]}」。\n\n'
        f'- 项目根目录：`{BASE}`。\n'
        '- 默认工作目录就是项目根目录；执行命令前确认在该目录下。\n'
        '- 看板状态必须通过 `python3 scripts/kanban_update.py ...` 更新，不要直接改 JSON。\n'
        '- 查询任务详情使用 `python3 scripts/kanban_update.py show <任务ID>`；不要读取 `kanban/<任务ID>.json`、`data/kanban.json` 或其他猜测路径。\n'
        '- JSON 看板数据源是 `data/tasks_source.json`，实时展示文件是 `data/live_status.json`；除非调试，不要直接读写这些文件。\n'
        '- 目标代码仓库如果在项目外部目录，优先用 `bash` 执行 `ls`、`find`、`rg`、`sed` 查看；不要用 `read` 工具直接读取目录路径。\n'
        '- `state` 命令的状态值必须使用英文枚举，禁止写中文状态名。合法值：Pending, Taizi, Zhongshu, Menxia, Assigned, Next, Doing, Review, PendingConfirm, Done, Blocked, Cancelled。\n'
        '- 三省主流程固定为：Taizi -> Zhongshu -> Menxia -> Assigned -> Doing -> Review -> Done。\n'
        '- 需要调用其他官员时，使用 OpenCode 的 subagent/task 能力，目标 agent id 使用本项目定义的英文 id。\n'
        '- 不要调用 `openclaw`、`sessions_send` 或写入 `~/.openclaw`；本项目当前由 OpenCode 接管。\n'
        '- 如原 SOUL 中出现 `__REPO_DIR__`，它指向上面的项目根目录。\n',
        read_text(BASE / 'agents' / 'GLOBAL.md'),
    ]
    group = GROUPS.get(agent_id)
    if group:
        parts.append(read_text(BASE / 'agents' / 'groups' / f'{group}.md'))
    parts.append(read_text(BASE / 'agents' / agent_id / 'SOUL.md'))
    return '\n\n---\n\n'.join(p.strip() for p in parts if p.strip()).replace('__REPO_DIR__', str(BASE))


def sync_prompts() -> list[str]:
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for agent_id in AGENT_ORDER:
        prompt_path = PROMPTS_DIR / f'{agent_id}.md'
        prompt_path.write_text(build_prompt(agent_id) + '\n', encoding='utf-8')
        written.append(str(prompt_path.relative_to(BASE)))
    return written


def sync_opencode_config() -> dict:
    cfg = read_json(OPENCODE_CFG, {})
    if not isinstance(cfg, dict):
        cfg = {}
    cfg['$schema'] = cfg.get('$schema') or 'https://opencode.ai/config.json'

    server = cfg.get('server') if isinstance(cfg.get('server'), dict) else {}
    server.setdefault('hostname', '127.0.0.1')
    server.setdefault('port', 4096)
    cors = list(server.get('cors') or [])
    for origin in ('http://127.0.0.1:7891', 'http://localhost:7891'):
        if origin not in cors:
            cors.append(origin)
    server['cors'] = cors
    cfg['server'] = server

    cfg.setdefault('default_agent', 'taizi')
    default_model = os.environ.get('OPENCODE_MODEL') or os.environ.get('OPENCODE_DEFAULT_MODEL') or 'opencode/deepseek-v4-flash-free'
    if cfg.get('model') in ('', None, 'github-copilot/claude-sonnet-4.6', 'github-copilot/gpt-4o'):
        cfg['model'] = default_model
    agents = cfg.get('agent') if isinstance(cfg.get('agent'), dict) else {}
    dashboard_models = dashboard_agent_models()
    logged_models = latest_logged_agent_models()
    for agent_id in AGENT_ORDER:
        meta = ID_LABEL[agent_id]
        existing = agents.get(agent_id) if isinstance(agents.get(agent_id), dict) else {}
        entry = dict(existing)
        model = entry.get('model') or logged_models.get(agent_id) or dashboard_models.get(agent_id)
        if model:
            entry['model'] = model
        entry['description'] = entry.get('description') or f'{meta["label"]}：{meta["duty"]}'
        entry['mode'] = entry.get('mode') or 'all'
        entry['prompt'] = f'{{file:./.opencode/prompts/{agent_id}.md}}'
        entry.setdefault('temperature', 0.1)
        entry.setdefault('steps', 60)
        permission = dict(DEFAULT_PERMISSION)
        if isinstance(entry.get('permission'), dict):
            permission.update(entry['permission'])
        permission['external_directory'] = 'allow'
        entry['permission'] = permission
        agents[agent_id] = entry
    cfg['agent'] = agents

    atomic_write_json(OPENCODE_CFG, cfg)
    return cfg


def sync_dashboard_config(cfg: dict) -> None:
    existing = read_json(DATA / 'agent_config.json', {})
    if not isinstance(existing, dict):
        existing = {}
    default_model = (
        os.environ.get('OPENCODE_MODEL')
        or os.environ.get('OPENCODE_DEFAULT_MODEL')
        or cfg.get('model')
        or existing.get('defaultModel')
        or 'opencode/deepseek-v4-flash-free'
    )
    dashboard_models = dashboard_agent_models()
    logged_models = latest_logged_agent_models()
    historical_models = list(logged_models.values()) + list(dashboard_models.values()) + list(existing.get('knownModels') or [])
    known_models = collect_opencode_models(cfg, historical_models, default_model)
    agents = []
    cfg_agents = cfg.get('agent') or {}
    for agent_id in AGENT_ORDER:
        meta = ID_LABEL[agent_id]
        entry = cfg_agents.get(agent_id) or {}
        model = entry.get('model') or logged_models.get(agent_id) or dashboard_models.get(agent_id) or default_model
        agents.append({
            'id': agent_id,
            'label': meta['label'],
            'role': meta['role'],
            'duty': meta['duty'],
            'emoji': meta['emoji'],
            'model': model,
            'defaultModel': default_model,
            'workspace': str(BASE),
            'prompt': str((PROMPTS_DIR / f'{agent_id}.md').relative_to(BASE)),
            'skills': [],
            'allowAgents': ALLOW_AGENTS.get(agent_id, []),
            'runtime': 'opencode',
        })
    payload = {
        'generatedAt': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'runtime': 'opencode',
        'defaultModel': default_model,
        'knownModels': known_models,
        'dispatchChannel': existing.get('dispatchChannel') or '',
        'agents': agents,
    }
    atomic_write_json(DATA / 'agent_config.json', payload)


def main() -> None:
    parser = argparse.ArgumentParser(description='Sync 三省六部 agents to project-local OpenCode config.')
    parser.add_argument('--no-dashboard-config', action='store_true', help='Skip data/agent_config.json update')
    args = parser.parse_args()

    cleanup_unmanaged_opencode_artifacts()
    prompts = sync_prompts()
    cfg = sync_opencode_config()
    if not args.no_dashboard_config:
        sync_dashboard_config(cfg)
    print(f'OpenCode config synced: {len(prompts)} prompts, {len(cfg.get("agent") or {})} agents')


if __name__ == '__main__':
    main()
