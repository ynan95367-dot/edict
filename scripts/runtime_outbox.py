#!/usr/bin/env python3
"""Durable local outbox for JSON-mode runtime work.

This is intentionally small and file-based: JSON dashboard mode should get the
same "record first, execute later" safety that the Postgres outbox provides,
without requiring Redis/Postgres for local usage.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import uuid
from typing import Any

from file_lock import atomic_json_read, atomic_json_update

_BASE = pathlib.Path(os.environ['EDICT_HOME']) if 'EDICT_HOME' in os.environ else pathlib.Path(__file__).resolve().parent.parent
OUTBOX_FILE = pathlib.Path(os.environ.get('EDICT_RUNTIME_OUTBOX', str(_BASE / 'data' / 'runtime_outbox.json')))
_FINISHED_STATUSES = {'done', 'failed', 'cancelled'}


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _parse_iso(value: str | None) -> _dt.datetime | None:
    if not value:
        return None
    try:
        return _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def _new_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:16]}'


def _dedupe_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item.get('kind') or ''),
        str(item.get('taskId') or ''),
        str(item.get('state') or ''),
        str(item.get('agentId') or ''),
    )


def _is_unfinished(item: dict[str, Any]) -> bool:
    return item.get('status', 'pending') not in _FINISHED_STATUSES


def append_outbox_event(
    kind: str,
    *,
    task_id: str,
    state: str = '',
    agent_id: str = '',
    trigger: str = '',
    trace_id: str = '',
    payload: dict[str, Any] | None = None,
    event_id: str = '',
    status: str = 'pending',
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Append one durable runtime item unless the same event id already exists."""
    created = now_iso()
    item = {
        'id': event_id or _new_id(kind),
        'kind': kind,
        'taskId': task_id,
        'state': state,
        'agentId': agent_id,
        'trigger': trigger,
        'traceId': trace_id,
        'payload': payload or {},
        'status': status,
        'attempts': 0,
        'maxAttempts': max(1, int(max_attempts or 1)),
        'createdAt': created,
        'updatedAt': created,
        'claimedAt': '',
        'finishedAt': '',
        'lastError': '',
    }

    def _append(items):
        items = items if isinstance(items, list) else []
        item_key = _dedupe_key(item)
        if any(x.get('id') == item['id'] for x in items if isinstance(x, dict)):
            return items
        for existing in items:
            if not isinstance(existing, dict):
                continue
            if not _is_unfinished(existing):
                continue
            if _dedupe_key(existing) != item_key:
                continue
            existing['updatedAt'] = created
            if item.get('traceId') and not existing.get('traceId'):
                existing['traceId'] = item['traceId']
            if item.get('payload'):
                existing['payload'] = item['payload']
            result = existing.get('result') if isinstance(existing.get('result'), dict) else {}
            result['duplicateSuppressedAt'] = created
            result['duplicateEventId'] = item['id']
            existing['result'] = result
            item['id'] = existing.get('id') or item['id']
            item['deduped'] = True
            return items
        items.append(item)
        # Keep the local queue bounded but preserve unfinished work.
        if len(items) > 1000:
            unfinished = [x for x in items if x.get('status') not in _FINISHED_STATUSES]
            finished = [x for x in items if x.get('status') in _FINISHED_STATUSES]
            items = finished[-500:] + unfinished[-500:]
        return items

    OUTBOX_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_json_update(OUTBOX_FILE, _append, [])
    return item


def enqueue_dispatch(
    *,
    task_id: str,
    state: str,
    agent_id: str,
    trigger: str,
    dispatch_id: str,
    trace_id: str = '',
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return append_outbox_event(
        'dispatch',
        task_id=task_id,
        state=state,
        agent_id=agent_id,
        trigger=trigger,
        trace_id=trace_id,
        payload=payload or {},
        event_id=dispatch_id,
        max_attempts=1,
    )


def enqueue_handoff(
    *,
    task_id: str,
    state: str,
    agent_id: str,
    trigger: str,
    trace_id: str = '',
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return append_outbox_event(
        'handoff',
        task_id=task_id,
        state=state,
        agent_id=agent_id,
        trigger=trigger,
        trace_id=trace_id,
        payload=payload or {},
        max_attempts=3,
    )


def list_outbox(*, task_id: str = '', status: str = '', kind: str = '', limit: int = 200) -> list[dict[str, Any]]:
    items = atomic_json_read(OUTBOX_FILE, [])
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if task_id and item.get('taskId') != task_id:
            continue
        if status and item.get('status') != status:
            continue
        if kind and item.get('kind') != kind:
            continue
        out.append(item)
    out.sort(key=lambda x: x.get('createdAt', ''))
    return out[-limit:] if limit and len(out) > limit else out


def claim_pending(*, worker_id: str, kinds: set[str] | None = None, limit: int = 1, stale_after_sec: int = 600) -> list[dict[str, Any]]:
    """Claim pending or stale-running items for a worker."""
    now = now_iso()
    now_dt = _parse_iso(now) or _dt.datetime.now(_dt.timezone.utc)
    claimed: list[dict[str, Any]] = []
    kinds = kinds or {'dispatch', 'handoff'}

    def _claim(items):
        nonlocal claimed
        items = items if isinstance(items, list) else []
        for item in items:
            if len(claimed) >= limit or not isinstance(item, dict):
                continue
            if item.get('kind') not in kinds:
                continue
            status = item.get('status', 'pending')
            stale = False
            if status == 'running':
                claimed_at = _parse_iso(item.get('claimedAt'))
                stale = bool(claimed_at and (now_dt - claimed_at).total_seconds() >= stale_after_sec)
            if status != 'pending' and not stale:
                continue
            if int(item.get('attempts') or 0) >= int(item.get('maxAttempts') or 1):
                item['status'] = 'failed'
                item['updatedAt'] = now
                item['lastError'] = item.get('lastError') or 'max attempts reached'
                continue
            item['status'] = 'running'
            item['claimedBy'] = worker_id
            item['claimedAt'] = now
            item['updatedAt'] = now
            item['attempts'] = int(item.get('attempts') or 0) + 1
            claimed.append(dict(item))
        return items

    atomic_json_update(OUTBOX_FILE, _claim, [])
    return claimed


def mark_done(item_id: str, result: dict[str, Any] | None = None) -> bool:
    return _mark(item_id, 'done', result=result or {})


def mark_failed(item_id: str, error: str = '', result: dict[str, Any] | None = None, retry: bool = False) -> bool:
    return _mark(item_id, 'pending' if retry else 'failed', error=error, result=result or {})


def requeue_orphaned_running(worker_id: str, reason: str = 'dashboard restarted') -> dict[str, Any]:
    """Move running items claimed by a previous worker back to pending."""
    ts = now_iso()
    count = 0

    def _update(items):
        nonlocal count
        items = items if isinstance(items, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get('status') != 'running':
                continue
            if item.get('claimedBy') == worker_id:
                continue
            item['status'] = 'pending'
            item['attempts'] = max(0, int(item.get('attempts') or 0) - 1)
            item['updatedAt'] = ts
            item['claimedAt'] = ''
            item['lastError'] = reason[:500]
            item.pop('claimedBy', None)
            result = item.get('result') if isinstance(item.get('result'), dict) else {}
            result['requeuedAt'] = ts
            result['requeueReason'] = reason[:200]
            item['result'] = result
            count += 1
        return items

    atomic_json_update(OUTBOX_FILE, _update, [])
    return {'ok': True, 'count': count}


def compact_unfinished_duplicates(reason: str = 'duplicate unfinished outbox') -> dict[str, Any]:
    """Cancel duplicate unfinished items with the same kind/task/state/agent."""
    ts = now_iso()
    count = 0

    def _update(items):
        nonlocal count
        items = items if isinstance(items, list) else []
        groups: dict[tuple[str, str, str, str], list[int]] = {}
        for idx, item in enumerate(items):
            if not isinstance(item, dict) or not _is_unfinished(item):
                continue
            groups.setdefault(_dedupe_key(item), []).append(idx)
        for indexes in groups.values():
            if len(indexes) <= 1:
                continue
            running = [idx for idx in indexes if items[idx].get('status') == 'running']
            keep = running[0] if running else indexes[0]
            for idx in indexes:
                if idx == keep:
                    continue
                item = items[idx]
                item['status'] = 'cancelled'
                item['updatedAt'] = ts
                item['finishedAt'] = ts
                item['lastError'] = reason[:500]
                item.pop('claimedBy', None)
                result = item.get('result') if isinstance(item.get('result'), dict) else {}
                result['dedupedAt'] = ts
                result['dedupedInto'] = items[keep].get('id', '')
                item['result'] = result
                count += 1
        return items

    atomic_json_update(OUTBOX_FILE, _update, [])
    return {'ok': True, 'count': count}


def requeue_failed(item_id: str, reason: str = '') -> dict[str, Any]:
    """Move a failed item back to pending so the dashboard worker can retry it."""
    ts = now_iso()
    outcome: dict[str, Any] = {'ok': False, 'error': 'item not found'}

    def _update(items):
        nonlocal outcome
        items = items if isinstance(items, list) else []
        for item in items:
            if not isinstance(item, dict) or item.get('id') != item_id:
                continue
            if item.get('status') != 'failed':
                outcome = {'ok': False, 'error': f'item status is {item.get("status", "unknown")}, not failed'}
                return items
            item['status'] = 'pending'
            item['attempts'] = 0
            item['updatedAt'] = ts
            item['claimedAt'] = ''
            item['finishedAt'] = ''
            item['lastError'] = ''
            item.pop('claimedBy', None)
            result = item.get('result') if isinstance(item.get('result'), dict) else {}
            result['requeuedAt'] = ts
            if reason:
                result['requeueReason'] = reason[:200]
            item['result'] = result
            outcome = {'ok': True, 'item': dict(item)}
            return items
        return items

    atomic_json_update(OUTBOX_FILE, _update, [])
    return outcome


def _mark(item_id: str, status: str, *, error: str = '', result: dict[str, Any] | None = None) -> bool:
    found = [False]
    ts = now_iso()

    def _update(items):
        items = items if isinstance(items, list) else []
        for item in items:
            if not isinstance(item, dict) or item.get('id') != item_id:
                continue
            item['status'] = status
            item['updatedAt'] = ts
            if status in {'done', 'failed', 'cancelled'}:
                item['finishedAt'] = ts
                item.pop('claimedBy', None)
            if error:
                item['lastError'] = error[:500]
            if result:
                item['result'] = result
            found[0] = True
            break
        return items

    atomic_json_update(OUTBOX_FILE, _update, [])
    return found[0]


def task_summary(task_id: str) -> dict[str, Any]:
    items = list_outbox(task_id=task_id, limit=200)
    counts: dict[str, int] = {}
    latest = None
    for item in items:
        counts[item.get('status', 'unknown')] = counts.get(item.get('status', 'unknown'), 0) + 1
        if latest is None or item.get('updatedAt', '') >= latest.get('updatedAt', ''):
            latest = item
    return {
        'total': len(items),
        'counts': counts,
        'latest': latest or {},
        'pending': counts.get('pending', 0),
        'running': counts.get('running', 0),
        'failed': counts.get('failed', 0),
    }
