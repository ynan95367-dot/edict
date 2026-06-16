# Runtime Health Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split runtime outbox health into actionable layers so current tasks are not misreported by ghost tasks or historical failures.

**Architecture:** Keep the existing JSON outbox and dashboard API. Add a lightweight classification layer inside `dashboard/server.py`, expose it through `/api/runtime-outbox`, update TypeScript types, and render the new layers in `WorkerHealthPanel`. No storage migration or large refactor.

**Tech Stack:** Python stdlib dashboard server, pytest, React 18, TypeScript, Vite, lucide-react.

---

## File Structure

- Modify: `dashboard/server.py`
  - Add helpers to classify outbox items into `current`, `ghost`, `history`, and `unknown`.
  - Update `get_runtime_outbox_health()` to return `layers` and make summary wording prefer current blockers over stale noise.
- Modify: `tests/test_server.py`
  - Add regression tests for ghost pending handoffs and historical failed items.
- Modify: `edict/frontend/src/api.ts`
  - Add TypeScript interfaces for the new `RuntimeOutboxLayers` response shape.
- Modify: `edict/frontend/src/components/edict-board/WorkerHealthPanel.tsx`
  - Render layer cards above raw queue lists.
  - Keep existing retry/archive controls for failed items.

## Task 1: Backend Runtime Layer Classification

**Files:**
- Modify: `tests/test_server.py`
- Modify: `dashboard/server.py`

- [x] **Step 1: Write failing tests for ghost and current blockers**

Add these tests near the existing runtime outbox health tests in `tests/test_server.py`:

```python
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

    assert health['layers']['current']['failed'] == 1
    assert health['layers']['ghost']['pending'] == 1
    assert health['layers']['current']['label'] == '当前任务阻塞'
    assert health['layers']['ghost']['label'] == '幽灵任务噪音'
    assert health['summary']['tone'] == 'err'
    assert health['summary']['blockingLayer'] == 'model'
    assert '模型连接失败' in health['summary']['detail']
    assert '幽灵任务' in health['layers']['ghost']['detail']
```

Add the second test:

```python
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
```

- [x] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest -q tests/test_server.py::test_runtime_outbox_health_separates_ghost_pending_from_current_failures tests/test_server.py::test_runtime_outbox_health_downgrades_history_only_failures
```

Expected: both tests fail because `layers` and `blockingLayer` are not present yet.

- [x] **Step 3: Implement classification helpers**

In `dashboard/server.py`, add helpers before `_runtime_outbox_summary()`:

```python
def _runtime_outbox_error_layer(item):
    text = f"{item.get('lastError', '')} {item.get('result', '')}".lower()
    if 'certificate verification' in text or 'unknown certificate' in text:
        return 'model', '模型连接失败'
    if 'timeout' in text or '超时' in text:
        return 'model', '模型或执行超时'
    if 'session not found' in text or 'opencode-session-stale' in text:
        return 'runtime', 'OpenCode 会话失效'
    if 'worktree' in text or 'patch' in text:
        return 'workspace', '工作区准备失败'
    return 'queue', '执行队列异常'


def _runtime_outbox_item_layer(item, task_map):
    task_id = str(item.get('taskId') or '')
    status = str(item.get('status') or '')
    task = task_map.get(task_id)
    if not task:
        if task_id and not task_id.startswith('JJC-'):
            return 'ghost'
        return 'history' if status == 'failed' else 'ghost'
    if bool(task.get('archived')) or str(task.get('state') or '') in {'Done', 'Cancelled'}:
        return 'history' if status == 'failed' else 'ghost'
    return 'current'
```

- [x] **Step 4: Implement layer summary builder**

In `dashboard/server.py`, add:

```python
def _runtime_outbox_layers(items, task_map, now_dt):
    buckets = {
        'current': {'key': 'current', 'label': '当前任务阻塞', 'detail': '当前任务相关执行请求。', 'pending': 0, 'running': 0, 'failed': 0, 'total': 0, 'blockingLayer': ''},
        'ghost': {'key': 'ghost', 'label': '幽灵任务噪音', 'detail': '任务不存在或已不属于当前看板，可归档或等待 stale 回收。', 'pending': 0, 'running': 0, 'failed': 0, 'total': 0, 'blockingLayer': 'queue'},
        'history': {'key': 'history', 'label': '历史失败', 'detail': '已完成、已取消或历史任务留下的失败记录，不应判定当前任务失败。', 'pending': 0, 'running': 0, 'failed': 0, 'total': 0, 'blockingLayer': ''},
        'unknown': {'key': 'unknown', 'label': '未分类队列', 'detail': '缺少足够上下文的执行请求。', 'pending': 0, 'running': 0, 'failed': 0, 'total': 0, 'blockingLayer': 'queue'},
    }
    examples = {key: [] for key in buckets}
    for item in items:
        if item.get('status') not in {'pending', 'running', 'failed'}:
            continue
        layer_key = _runtime_outbox_item_layer(item, task_map)
        bucket = buckets.get(layer_key) or buckets['unknown']
        status = item.get('status')
        bucket['total'] += 1
        if status in {'pending', 'running', 'failed'}:
            bucket[status] += 1
        if status == 'failed':
            blocking_layer, detail = _runtime_outbox_error_layer(item)
            if layer_key == 'current':
                bucket['blockingLayer'] = blocking_layer
                bucket['detail'] = detail
        if len(examples[layer_key]) < 3:
            examples[layer_key].append(_public_outbox_item(item, task_map, now_dt))
    for key, bucket in buckets.items():
        bucket['items'] = examples[key]
    return buckets
```

- [x] **Step 5: Update summary to prefer current blockers**

Change `_runtime_outbox_summary()` signature to accept `layers=None`, then before the existing `if failed:` branch add:

```python
    layers = layers or {}
    current = layers.get('current') or {}
    ghost = layers.get('ghost') or {}
    history = layers.get('history') or {}
    current_failed = int(current.get('failed') or 0)
    current_active = int(current.get('pending') or 0) + int(current.get('running') or 0)
    if current_failed:
        layer = current.get('blockingLayer') or 'queue'
        return {
            'tone': 'err',
            'label': f'当前任务失败 {current_failed}',
            'detail': f'{current.get("detail") or "当前任务有失败执行请求"}；幽灵任务 {ghost.get("total", 0)} 个，历史失败 {history.get("failed", 0)} 个。',
            'nextAction': '先处理当前任务失败；幽灵任务可单独归档，不要混入当前判断。',
            'blockingLayer': layer,
        }
    if current_active:
        return {
            'tone': 'warn',
            'label': '当前任务执行中',
            'detail': f'当前任务 pending={current.get("pending", 0)} running={current.get("running", 0)}；幽灵任务 {ghost.get("total", 0)} 个。',
            'nextAction': '优先观察当前任务；若超过阈值再扫描或重新交办。',
            'blockingLayer': 'queue',
        }
    if failed and int(history.get('failed') or 0) == failed and not current_active:
        return {
            'tone': 'warn',
            'label': '仅历史失败',
            'detail': f'有 {failed} 个历史失败记录，但不会判定当前任务失败。',
            'nextAction': '可以归档历史失败，继续观察当前任务。',
            'blockingLayer': 'history',
        }
```

Also pass `layers=layers` when calling `_runtime_outbox_summary()`.

- [x] **Step 6: Return layers from health API**

Inside `get_runtime_outbox_health()`, after `task_map` is built, add:

```python
    layers = _runtime_outbox_layers(items, task_map, now_dt)
```

Then add this key to the returned dict:

```python
        'layers': layers,
```

- [x] **Step 7: Run backend tests**

Run:

```bash
pytest -q tests/test_server.py::test_runtime_outbox_health_exposes_dead_letters tests/test_server.py::test_runtime_outbox_health_separates_ghost_pending_from_current_failures tests/test_server.py::test_runtime_outbox_health_downgrades_history_only_failures
```

Expected: all selected tests pass.

## Task 2: Frontend Runtime Layer Display

**Files:**
- Modify: `edict/frontend/src/api.ts`
- Modify: `edict/frontend/src/components/edict-board/WorkerHealthPanel.tsx`
- Modify: `edict/frontend/src/index.css`

- [x] **Step 1: Add TypeScript layer types**

In `edict/frontend/src/api.ts`, add:

```ts
export interface RuntimeOutboxLayer {
  key: string;
  label: string;
  detail: string;
  pending: number;
  running: number;
  failed: number;
  total: number;
  blockingLayer?: string;
  items?: RuntimeOutboxItem[];
}
```

Then add to `RuntimeOutboxHealth`:

```ts
  layers?: Record<string, RuntimeOutboxLayer>;
```

- [x] **Step 2: Render layer cards**

In `WorkerHealthPanel.tsx`, after `deadTotal` is computed, add:

```ts
  const layers = data?.layers || {};
  const visibleLayers = ['current', 'ghost', 'history']
    .map((key) => layers[key])
    .filter((layer) => layer && layer.total > 0);
```

Before the existing `wh-grid`, render:

```tsx
      {!!visibleLayers.length && (
        <div className="wh-layer-row">
          {visibleLayers.map((layer) => (
            <div key={layer.key} className={`wh-layer ${layer.key}`}>
              <span>{layer.label}</span>
              <b>{layer.pending}/{layer.running}/{layer.failed}</b>
              <em>{layer.detail}</em>
            </div>
          ))}
        </div>
      )}
```

- [x] **Step 3: Run frontend build**

Run:

```bash
npm run build
```

from `edict/frontend`.

Expected: TypeScript and Vite build pass, and `dashboard/dist` is regenerated.

## Task 3: Runtime Smoke Verification

**Files:**
- No code files unless verification reveals a defect.

- [x] **Step 1: Check API health**

Run:

```bash
curl -sS --max-time 5 http://127.0.0.1:7891/api/runtime-outbox | python3 -m json.tool | sed -n '1,180p'
```

Expected: response includes `layers.current`, `layers.ghost`, and `layers.history`.

- [x] **Step 2: Confirm current repository state**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` passes. `git status` may show existing user/Codex changes, but this task's touched files should be limited to the files listed in this plan.

- [x] **Step 3: Record verification in final response**

Final response should mention:

- The backend tests that passed.
- The frontend build result.
- The runtime API smoke result.
- Any remaining known blocker, especially OpenCode certificate verification errors if still present.
