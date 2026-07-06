#!/usr/bin/env python3
"""史馆 · 回溯收口审计 — retroactive false-completion detector.

Replays the evidence gate over historical ``Done`` tasks to answer one question
the system has never been able to answer about itself:

    Of everything we marked Done, how much actually produced the artifact it
    claimed — and how much shipped as Done with nothing on disk?

This is both the day-one proof of the Evidence Gate's value and the seed of the
eval ledger (slice 2). It is read-only: it never mutates task state.

Usage:
    python3 scripts/shiguan_eval.py            # audit data/tasks_source.json
    python3 scripts/shiguan_eval.py --json     # machine-readable report
"""
import argparse
import json
import os
import pathlib
import sys

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
if str(_BASE / 'scripts') not in sys.path:
    sys.path.insert(0, str(_BASE / 'scripts'))

import evidence_gate as eg

TASKS_FILE = _BASE / 'data' / 'tasks_source.json'

VERIFIED = 'verified'
WOULD_VETO = 'would_veto'
NOT_CHECKABLE = 'not_checkable'


def classify(task, root):
    """Bucket a single Done task by whether its declared artifact exists on disk."""
    output = str(task.get('output') or '').strip()
    acceptance = eg.acceptance_for_done(task, output)
    if not acceptance:
        return {
            'id': task.get('id'),
            'category': NOT_CHECKABLE,
            'output': output,
            'detail': 'no on-disk contract (prose / empty output)',
        }
    result = eg.gate(task, output, root)
    detail = '; '.join(f"{r['type']}={r['detail']}" for r in result.get('results', []))
    return {
        'id': task.get('id'),
        'category': VERIFIED if result['ok'] else WOULD_VETO,
        'output': output,
        'detail': detail,
    }


def evaluate_done(tasks, root):
    """Audit every Done task; return summary counts + per-task rows."""
    done = [t for t in tasks if str(t.get('state', '')).lower() == 'done']
    rows = [classify(t, root) for t in done]
    counts = {VERIFIED: 0, WOULD_VETO: 0, NOT_CHECKABLE: 0}
    for r in rows:
        counts[r['category']] = counts.get(r['category'], 0) + 1
    return {
        'summary': {
            'total_done': len(done),
            'verified': counts[VERIFIED],
            'would_veto': counts[WOULD_VETO],
            'not_checkable': counts[NOT_CHECKABLE],
        },
        'rows': rows,
    }


def _load_tasks(path):
    raw = json.loads(pathlib.Path(path).read_text(encoding='utf-8'))
    if isinstance(raw, dict):
        return raw.get('tasks', list(raw.values()))
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description='史馆回溯收口审计')
    parser.add_argument('--tasks', default=str(TASKS_FILE), help='tasks_source.json 路径')
    parser.add_argument('--root', default=str(_BASE), help='相对产物解析根目录')
    parser.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    args = parser.parse_args(argv)

    tasks = _load_tasks(args.tasks)
    report = evaluate_done(tasks, args.root)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    s = report['summary']
    print('史馆回溯收口审计')
    print('=' * 56)
    print(f"Done 任务总数        : {s['total_done']}")
    print(f"✅ 产物可验证(verified): {s['verified']}")
    print(f"🚫 本应驳回(would_veto): {s['would_veto']}  ← 自述完成、磁盘无产物")
    print(f"➖ 无法核验(prose)     : {s['not_checkable']}  ← output 是描述而非路径，闸门保持沉默")
    print('=' * 56)
    veto = [r for r in report['rows'] if r['category'] == WOULD_VETO]
    if veto:
        print('\n本应被证据闸门驳回的任务：')
        for r in veto:
            print(f"  - {r['id']}: {r['output']}\n      {r['detail']}")
    else:
        print('\n（没有路径形态却消失的产物。）')
    print('\n说明：当前 todos-only 收口把以上 would_veto 任务全部放行为 Done；')
    print('      证据闸门会驳回其中的 would_veto 子集，对 prose 子集保持沉默（避免误杀）。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
