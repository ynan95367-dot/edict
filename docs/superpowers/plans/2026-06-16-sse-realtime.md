# SSE Realtime Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the dashboard's poll-only update model with **SSE push** — the live (stdlib) server gains a `/api/stream` Server-Sent-Events endpoint that emits an event whenever `live_status.json` changes; the frontend subscribes via `EventSource` and refreshes instantly, with the existing 1s/5s polling retained as a fallback.

**Architecture:** `dashboard/server.py` (stdlib `ThreadingHTTPServer`, one thread per connection) adds `GET /api/stream` that holds the connection open, watches `live_status.json` mtime once per second, and writes `event: live-status` frames (plus periodic keep-alive comments). The browser's native `EventSource` auto-reconnects. The frontend keeps `startPolling()` as a safety net and adds `startRealtime()` that calls `loadLive()` on each SSE event. **Chose SSE over WebSocket** (push-only use case; ~30 stdlib lines vs ~200 hand-rolled WS; keeps zero-deps promise — see spec).

**Tech Stack:** Python stdlib `http.server`, browser `EventSource`, React + Zustand + Vite + TypeScript.

**Working dir:** `/Users/bingsen/clawd/openclaw-sansheng-liubu`. Working tree is clean (WIP committed) — `dashboard/server.py` and the frontend files are now committed, so commit normally (scoped `git add` per task). Backend tests: `python -m pytest tests/ -q` (root). Frontend typecheck: `cd edict/frontend && npm run build`.

---

### Task 1: SSE endpoint on the live server

**Files:**
- Modify: `dashboard/server.py` (add `_serve_sse_stream` method to `class Handler`; add one route in `do_GET`)
- Create: `tests/test_sse_stream.py`

- [ ] **Step 1: Write the failing SSE test**

`tests/test_sse_stream.py`:
```python
import http.client
import threading
import time

import pytest


@pytest.fixture
def sse_server(tmp_path, monkeypatch):
    import server as srv
    monkeypatch.setattr(srv, "DATA", tmp_path, raising=False)
    monkeypatch.setattr(srv, "_ACTIVE_TASK_DATA_DIR", tmp_path, raising=False)
    (tmp_path / "live_status.json").write_text("{}", encoding="utf-8")
    httpd = srv.ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield srv, tmp_path, port
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_stream_sends_event_stream_headers_and_initial_event(sse_server):
    _srv, _dir, port = sse_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", "/api/stream")
    resp = conn.getresponse()
    try:
        assert resp.status == 200
        assert "text/event-stream" in (resp.getheader("Content-Type") or "")
        # initial frame is written immediately on connect
        chunk = resp.read(64)
        assert b"live-status" in chunk
    finally:
        conn.close()


def test_stream_emits_on_live_status_change(sse_server):
    _srv, data_dir, port = sse_server
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    conn.request("GET", "/api/stream")
    resp = conn.getresponse()
    try:
        resp.read(64)  # consume initial frame
        time.sleep(0.2)
        # mutate live_status.json -> bump mtime
        (data_dir / "live_status.json").write_text('{"tasks":{}}', encoding="utf-8")
        # next change frame should arrive within the ~1s watch interval
        deadline = time.time() + 6
        got = b""
        while time.time() < deadline and b"live-status" not in got:
            got += resp.read(48)
        assert b"live-status" in got
    finally:
        conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sse_stream.py -q`
Expected: FAIL — `/api/stream` returns 404 / not event-stream (route absent).

- [ ] **Step 3: Add the SSE handler method**

In `dashboard/server.py`, add this method inside `class Handler(BaseHTTPRequestHandler)` (e.g. right after `send_file`, near line 10973):
```python
    def _serve_sse_stream(self):
        """SSE: push a 'live-status' event whenever live_status.json changes.

        Push-only stream for the dashboard. One thread per connection
        (ThreadingHTTPServer); returns when the client disconnects.
        """
        task_data_dir = get_task_data_dir()
        live_file = task_data_dir / 'live_status.json'
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')
            cors_headers(self)
            self.end_headers()
        except Exception:
            return

        def _mtime():
            try:
                return live_file.stat().st_mtime if live_file.exists() else 0.0
            except OSError:
                return 0.0

        last_mtime = _mtime()
        last_ping = time.time()
        try:
            self.wfile.write(b'event: live-status\ndata: {"type":"init"}\n\n')
            self.wfile.flush()
            while True:
                mtime = _mtime()
                now = time.time()
                if mtime != last_mtime:
                    last_mtime = mtime
                    payload = json.dumps({'type': 'live-status', 'mtime': mtime})
                    self.wfile.write(f'event: live-status\ndata: {payload}\n\n'.encode('utf-8'))
                    self.wfile.flush()
                    last_ping = now
                elif now - last_ping >= 15:
                    self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
                    last_ping = now
                time.sleep(1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
```
Confirm `time` and `json` are imported at the top of `server.py` (they are — used throughout). `cors_headers` is a module function (`server.py:378`); calling `cors_headers(self)` matches its `cors_headers(h)` signature.

- [ ] **Step 4: Add the route in `do_GET`**

In `dashboard/server.py` `do_GET`, after the auth gate (`if self._check_auth(): return`, ~line 11020) and among the `elif p == …` chain (e.g. right after the `'/api/live-status'` branch, ~line 11033), add:
```python
        elif p == '/api/stream':
            self._serve_sse_stream()
            return
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_sse_stream.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Full regression (ensure no breakage to existing server tests)**

Run: `python -m pytest tests/ -q`
Expected: PASS (all prior tests + 2 new).

- [ ] **Step 7: Commit**

```bash
git add dashboard/server.py tests/test_sse_stream.py
git commit -m "feat(dashboard): SSE /api/stream pushes live-status changes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Frontend SSE subscription helper

**Files:**
- Modify: `edict/frontend/src/api.ts` (add `subscribeLiveStatus` export)

- [ ] **Step 1: Add the EventSource helper**

In `edict/frontend/src/api.ts`, after the `export const api = { … }` block, add:
```typescript
/**
 * SSE 实时订阅 live_status 变更。每次变更触发 onChange()。
 * 浏览器原生 EventSource 自动重连;EventSource 不可用时返回 no-op。
 * 返回取消订阅函数。
 */
export function subscribeLiveStatus(onChange: () => void): () => void {
  if (typeof EventSource === 'undefined') return () => {};
  let es: EventSource | null = null;
  try {
    es = new EventSource(`${API_BASE}/api/stream`);
  } catch {
    return () => {};
  }
  es.addEventListener('live-status', () => onChange());
  return () => {
    try {
      es?.close();
    } catch {
      /* noop */
    }
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd edict/frontend && npm run build`
Expected: build succeeds (tsc passes), no type errors. (Build writes to `dashboard/dist/` per vite config — that's expected; do not commit the rebuilt dist in this task, only `api.ts`.)

- [ ] **Step 3: Commit**

```bash
git add edict/frontend/src/api.ts
git commit -m "feat(frontend): subscribeLiveStatus SSE helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire SSE into the store + app startup (polling as fallback)

**Files:**
- Modify: `edict/frontend/src/store.ts` (add `startRealtime`/`stopRealtime` near `startPolling`, ~line 421-445)
- Modify: the component that calls `startPolling()` (find with grep — likely `edict/frontend/src/App.tsx`)

- [ ] **Step 1: Add realtime start/stop to the store**

In `edict/frontend/src/store.ts`, the imports already include `api` (used throughout). After the `stopPolling()` function (ends ~line 445), add:
```typescript
// ── SSE realtime (push); polling above remains as fallback ──

let _esUnsub: (() => void) | null = null;

export function startRealtime() {
  if (_esUnsub) return;
  _esUnsub = api.subscribeLiveStatus(() => {
    useStore.getState().loadLive();
  });
}

export function stopRealtime() {
  if (_esUnsub) {
    _esUnsub();
    _esUnsub = null;
  }
}
```
Then ensure `subscribeLiveStatus` is importable: at the top of `store.ts`, the existing import of `api` comes from `./api`. Add `subscribeLiveStatus` to that import. Find the line `import { api } from './api';` (or similar) and change it to:
```typescript
import { api, subscribeLiveStatus } from './api';
```
and update the `startRealtime` body to call `subscribeLiveStatus(...)` directly:
```typescript
  _esUnsub = subscribeLiveStatus(() => {
    useStore.getState().loadLive();
  });
```
(If `api.ts` exports both `api` and `subscribeLiveStatus`, importing the named function is cleaner than `api.subscribeLiveStatus`.)

- [ ] **Step 2: Call startRealtime alongside startPolling at app startup**

Run: `grep -rn "startPolling" edict/frontend/src` to find the caller (expected: `App.tsx`). In that file, where it imports and calls `startPolling()` (e.g. in a `useEffect`), add `startRealtime` to the import and call it next to `startPolling()`, and `stopRealtime` in the cleanup next to `stopPolling()`. Concretely, change the import:
```typescript
import { startPolling, stopPolling, startRealtime, stopRealtime } from './store';
```
and the effect:
```typescript
  useEffect(() => {
    startPolling();
    startRealtime();
    return () => {
      stopPolling();
      stopRealtime();
    };
  }, []);
```
(Match the existing effect's exact shape; only add the two `*Realtime` calls.)

- [ ] **Step 3: Typecheck**

Run: `cd edict/frontend && npm run build`
Expected: build succeeds, no type errors.

- [ ] **Step 4: Commit (source only)**

```bash
git add edict/frontend/src/store.ts edict/frontend/src/App.tsx
git commit -m "feat(frontend): SSE-driven live updates with polling fallback

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(If grep found a caller other than `App.tsx`, `git add` that file instead.)

---

### Task 4: End-to-end verification (manual / webapp-testing)

**Files:** none (verification only)

- [ ] **Step 1: Rebuild the served bundle**

Run: `cd edict/frontend && npm run build` (outputs to `dashboard/dist/`).

- [ ] **Step 2: Start the dashboard and confirm SSE**

Run: `python dashboard/server.py --host 127.0.0.1 --port 7891` (or `./edict.sh`). In a browser at `http://127.0.0.1:7891`, open DevTools → Network → confirm an `EventStream` request to `/api/stream` stays open. Then in a shell `touch data/live_status.json` (or trigger a task mutation) and confirm the board refreshes without waiting for the 5s poll. Optionally use the **webapp-testing** skill (Playwright) to script: load board → assert `/api/stream` connection → mutate `live_status.json` → assert UI updates.

- [ ] **Step 3: Commit the rebuilt dist (if the team commits the built bundle)**

```bash
git add dashboard/dist
git commit -m "build(frontend): rebuild dist with SSE realtime

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(The repo tracks `dashboard/dist/` as the served production build — see `.gitignore` un-ignore rules — so committing the rebuild keeps source and bundle in sync.)

---

## Self-Review

- **Spec coverage:** B.2 server SSE endpoint → Task 1; B.3 `subscribeLiveStatus` → Task 2; B.3 store wiring + fallback → Task 3; B.4 verification → Task 4. All covered.
- **Placeholder scan:** none — concrete code + commands. Task 3/4 contain one grep-to-locate (`startPolling` caller) because the exact file is environment-dependent; the code to add is fully specified.
- **Type/name consistency:** `subscribeLiveStatus`, `startRealtime`, `stopRealtime`, `_esUnsub`, `_serve_sse_stream`, route `/api/stream`, event name `live-status` are consistent across server, api.ts, store.ts, and tests.
- **Decision recorded:** SSE (not WS) — rationale in the spec; the live stdlib server can't host WS without hand-rolling the protocol, and the use case is push-only.
