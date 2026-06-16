# v2 后端收口 + SSE 实时化 — 设计 Spec

> 日期：2026-06-16 · 分支：`codex/agent-control-plane-upgrade-20260602`
> 上游决策：用户已批准 v2 收口设计;⑦ 选 SSE;⑧ 已并入本 spec 的 CI 部分;⑨ 仓库卫生已完成(commit `6d26f68`)。
> 工作树已干净(WIP 已提交 `adf2a27`),本 spec 涉及文件与用户在途工作零冲突。

## 0. 范围与非范围

**本 spec 覆盖两个相互独立的子系统(同一轮交付):**
- **A. v2 后端收口** — 修 `edict/backend` 的 CORS 通配 + 补 service 层测试(纯逻辑 + 真 Postgres 集成)+ 接入 CI。
- **B. ⑦ 前端实时化** — 给实时看板从「轮询」升级为 **SSE 推送**(server.py 新增 `/api/stream` + 前端 `EventSource` 订阅,带轮询回退)。

**非范围:**
- 不激活 v2(仍是休眠的并行后端);A 只是把它的真实缺口补上,便于将来启用。
- 不在 server.py 上手写 WebSocket(理由见 §B.1)。
- 不重构 server.py 单体路由(P0-2,需另立计划)。

---

## A. v2 后端收口

### A.1 CORS 白名单

**问题**:[main.py:60](../../../edict/backend/app/main.py#L60) `allow_origins=["*"]` + `allow_credentials=True`(浏览器实际会拒绝该组合),且不可配置。

**设计**:
- [config.py](../../../edict/backend/app/config.py) `Settings` 增加:
  ```python
  cors_origins: list[str] = ["http://localhost:5173", "http://localhost:7891", "http://127.0.0.1:7891"]
  ```
  并加 `@field_validator("cors_origins", mode="before")`:若环境变量是逗号分隔字符串则 `split(",")` 去空白;`"*"` 原样保留为 `["*"]`。环境变量名 `CORS_ORIGINS`。
- [main.py](../../../edict/backend/app/main.py):
  ```python
  origins = settings.cors_origins
  app.add_middleware(
      CORSMiddleware,
      allow_origins=origins,
      allow_credentials=("*" not in origins),
      allow_methods=["*"],
      allow_headers=["*"],
  )
  ```

### A.2 纯逻辑测试(无 DB) — `edict/backend/tests/`

- `test_state_machine.py`:`STATE_TRANSITIONS == _contract_transitions()`(把 task.py:80 的 drift 断言显式化)、`TERMINAL_STATES` 正确、每个 `TaskState` 在枚举里、`Task.org_for_state(state, org)` 关键映射、`STATE_AGENT_MAP`/`STATE_ORG_MAP` 与 control_plane 一致。
- `test_task_to_dict.py`:内存构造 `Task(title=..., state=TaskState.Review, flow_log=[...], ...)`(不入库),验 `to_dict()` 含 `task_id/state/flow_log/...` 及旧前端兼容字段(`id/org/_scheduler/updatedAt`),且 `None` 字段兜底为 `[]/{}/""`。
- `test_config_cors.py`:`Settings(CORS_ORIGINS="http://a, http://b")` → `["http://a","http://b"]`;默认值;`"*"` → `["*"]`。

这些零依赖、本地与 CI 都能跑。

### A.3 异步 DB 集成测试(真 Postgres) — `edict/backend/tests/`

- `conftest.py`:
  - `pytest-asyncio`(`asyncio_mode=auto` 或显式 mark)。
  - `db_url` fixture:读 `DATABASE_URL`;**缺失则 `pytest.skip`**(本地无 PG 不报错)。
  - `engine` fixture:`create_async_engine(db_url)`;建表用 `Base.metadata.create_all`(够测 service 逻辑,免 alembic env 复杂度)。
  - `session` fixture:每用例一个 `AsyncSession`,用例后 `DROP`/`TRUNCATE` 全表保证隔离。
  - 导入路径:CI job 的 `working-directory: edict/backend`,故 `from app.services.task_service import TaskService`、`from app.models.task import Task, TaskState` 可解析(`app/__init__.py` 已在;task.py 自行把仓库根加入 path 以导入 `edict.control_plane`)。
- `test_task_service_db.py`(用 `TaskService(session)`):
  - `create_task` → 1 条 task 落库 + 1 条 outbox(`event_type="task.created"`, topic `TOPIC_TASK_CREATED`),同事务提交。
  - `transition_state(Taizi→Zhongshu)` → state 改、`flow_log` 追加 1 条(含 from/to/agent/ts)、1 条 outbox(`TOPIC_TASK_STATUS`)。
  - 非法 `transition_state(Taizi→Done)` → 抛 `ValueError`,state 不变、无新 outbox。
  - 终态 `transition_state(Doing→Done)` → outbox topic 为 `TOPIC_TASK_COMPLETED`。
  - `request_dispatch(task, "zhongshu")` → outbox `event_type="task.dispatch.request"`(`TOPIC_TASK_DISPATCH`)。
  - `add_progress` / `update_todos` → 对应字段更新。

> `SELECT FOR UPDATE`(`transition_state` 里)在真 PG 上隐式被覆盖。

### A.4 CI 接入(⑧)

`.github/workflows/ci.yml` 的 backend job:
```yaml
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: edict
          POSTGRES_PASSWORD: edict
          POSTGRES_DB: edict_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U edict" --health-interval 5s
          --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgresql+asyncpg://edict:edict@localhost:5432/edict_test
    # steps: pip install -r requirements.txt + -r requirements-dev.txt
    #        pytest (working-directory: edict/backend)
```
- 新增 `edict/backend/requirements-dev.txt`:`pytest`、`pytest-asyncio`。
- 现有 root `tests/`(221)已在 CI 跑;本 job 增量加 v2 测试。

---

## B. ⑦ 前端实时化(SSE)

### B.1 为什么 SSE 而非 WS(决策记录)

实时看板是**单向 server→client 推送**:用户操作已走普通 POST 端点,前端只需「收到变更就刷新」。
- **SSE**:`Content-Type: text/event-stream`,服务端 `data: <json>\n\n` + flush;浏览器 `EventSource` 原生**自动重连**;在 stdlib `ThreadingHTTPServer` 上 ~30 行即可,零新依赖。
- **WS**:需在 stdlib 上手写握手 + 掩码帧编解码 + ping/pong/close + 劫持连接(~200 行,安全敏感),其双向能力在此用不上。
- **结论**:本用例 SSE 是最优解。真正需要 WS 时,正确归宿是已有 `/ws` 的 v2 FastAPI(另案启用)。

### B.2 后端 — `dashboard/server.py` 新增 SSE 端点

- `GET /api/stream`(在 `Handler.do_GET` 路由表里):
  - 发送头:`Content-Type: text/event-stream`、`Cache-Control: no-cache`、`Connection: keep-alive`、CORS 头(复用 `cors_headers`)。
  - 循环(直到客户端断开 → 写失败/`BrokenPipe` → 退出):
    - 服务端侧每 ~1s 检查 `live_status.json` 的 mtime(或 `.refresh_pending` 信号文件);变更则读取并 `data: {"type":"live-status","ts":...}\n\n` 推送(只推变更信号 + 轻量元数据,前端收到后调用现有 `/api/live-status` 拉全量,避免在 SSE 里塞 1.6MB)。
    - 无变更则每 ~15s 推一个 `: keep-alive\n\n` 注释帧维持连接。
  - `ThreadingHTTPServer` 每连接一线程,自托管少量客户端可接受;客户端断开即释放线程。
- 不改变现有 `_trigger_refresh()` / `refresh_watcher.py` 机制(SSE 是其消费者,旁路增量)。

### B.3 前端 — `edict/frontend/src`

- `api.ts`:加 `subscribeLiveStatus(onEvent): () => void`,内部 `new EventSource(`${API_BASE}/api/stream`)`,`onmessage` 解析 → 回调;返回取消订阅函数。
- `store.ts`:启动时优先 `subscribeLiveStatus`,收到 `live-status` 事件即触发现有的 live-status 拉取/刷新动作;**回退**:`EventSource` 不可用或连接错误超阈值 → 退回现有轮询 `setInterval`。即「SSE 优先,轮询兜底」。
- 不改 UI 组件外观,仅换数据驱动来源。

### B.4 验证

- 后端:`tests/test_server_sse.py` — 起 server(或直接调 handler),GET `/api/stream`,改 `live_status.json` mtime → 断言收到一帧 `live-status` 事件;客户端断开不崩。
- 前端:`webapp-testing`(Playwright)冒烟 — 看板加载、SSE 连接建立、模拟一次变更后界面刷新;SSE 失败时回退轮询仍可用。

---

## 测试与交付策略

- A 与 B 独立,可分别成 plan;但同一轮交付。
- 全程 TDD:测试先行;每步小步提交,提交按文件精确 `git add`。
- 工作树已干净,无需 hunk 隔离;但仍**只提交本 spec 涉及文件**。

## 风险

- A.3 依赖 CI 有 Postgres service;本地无 PG 自动跳过,不阻断本地。
- B.2 的 SSE 持连占线程:自托管(个位数客户端)无虞;若将来需高并发,迁 v2/asgi。
- B.3 触碰 `store.ts`/`api.ts`(刚提交),后续你若并行改前端需注意。

## Self-review

- 占位符:无 TBD。
- 一致性:A(v2/FastAPI)与 B(stdlib server.py)是两套后端,互不耦合;CORS 仅改 v2,SSE 仅改 live server——无矛盾。
- 范围:两个独立子系统,已显式分节;writing-plans 时可拆两份 plan。
- 歧义:⑦ 传输已明确为 SSE(WS 作为 v2 未来选项记录在案)。
