# Harness 化运行审计与修复记录

> 日期：2026-05-29  
> 范围：任务派发、停滞恢复、刷新性能、任务详情信息展示。

## 结论

本项目应该吸收的是 **harness architecture**，不是另造一个 “harmess” 架构。这里的 harness 指 Agent 外围的确定性运行基座：状态机、任务队列、派发、重试、恢复、审计、观察面板。三省六部本身仍然保留“太子 -> 中书 -> 门下 -> 尚书 -> 六部”的制度模型，但不能把关键移交完全交给 Agent 自觉执行。

一句话：LLM 负责判断、规划、表达，harness 负责“下一步一定会被派发、失败一定可见、重试一定有边界”。

## 调研依据

- Harness Platform 的官方架构把执行拆成 Manager 与 Delegate：控制面下发任务，执行面在目标环境实际操作。这对应本项目的“看板/调度器”和“OpenClaw/OpenCode Agent runtime”分层。  
  <https://developer.harness.io/docs/getting-started/harness-platform-architecture/>
- Harness 官方失败策略支持按 stage/step 设置 retry、rollback、abort 等动作；这对应本项目巡检器的 retry、escalate、rollback。  
  <https://developer.harness.io/docs/platform/pipelines/failure-handling/define-a-failure-strategy-on-stages-and-steps/>
- Harness 官方支持从失败阶段 retry，而不是整条流水线重跑；这对应任务卡住后从当前状态恢复，而不是重建 JJC 任务。  
  <https://developer.harness.io/docs/platform/pipelines/failure-handling/resume-pipeline-deployments/>
- Temporal 官方 durable execution 强调崩溃、网络失败、基础设施故障后从中断点恢复；这对应本项目需要把派发状态、active dispatch、last progress 写入持久数据。  
  <https://docs.temporal.io/>
- LangGraph 官方 interrupts 依赖 checkpointer 保存图状态并恢复；这对应“人工叫停/准奏/封驳/恢复”必须是状态机动作，而不是只写 UI 文案。  
  <https://docs.langchain.com/oss/python/langgraph/interrupts>
- OpenTelemetry 官方把可观测性拆成 traces、metrics、logs；本项目现有 flow_log、progress_log、event ledger 应被合并成一条任务时间线，而不是在 UI 中重复出现三遍。  
  <https://opentelemetry.io/docs/>
- OpenAI Agents SDK 官方强调 handoffs、guardrails、tracing；这对应本项目需要“确定性交接 + 越权拦截 + 完整链路证据”。  
  <https://platform.openai.com/docs/guides/agents-sdk/>

## 运行问题定位

### 1. 太子不执行或不分发

JSON 看板模式下，`kanban_update.py state JJC-xxx Zhongshu` 只更新任务文件，不会自动触发 dashboard 的 `dispatch_for_state()`。因此如果太子 Agent 只做了状态推进，没有真的调用中书 subagent，任务会停在 Zhongshu，直到人工推进或巡检误判停滞。

已修复：`dashboard/server.py` 的 scheduler scan 增加 **state handoff scan**。当状态已经变更，但 `_scheduler.lastDispatchAgent/lastDispatchState` 不是当前状态应有的 Agent 时，巡检会自动入队派发下游 Agent。

### 2. 卡顿与刷新进程风暴

原逻辑中，dashboard 保存任务会直接后台运行 `refresh_live_data.py`；Agent 的 `kanban_update.py` 若未发现 watcher，也会每次进展都 fork 刷新。多 Agent 并发时，几十次 progress 会变成几十个刷新进程。

已修复：

- dashboard `_trigger_refresh()` 改为优先 touch `.refresh_pending`。
- watcher 存活时完全交给 watcher debounce。
- watcher 不存在时，dashboard 内部使用 1.5 秒 debounce timer 合并刷新。
- `start.sh`、`edict.sh`、`scripts/start_opencode.sh` 都会启动 `scripts/refresh_watcher.py`。

### 3. 信息栏重复混乱

旧任务详情页同时展示：

- 当前阶段横幅
- 大流程条
- 太子调度 KPI
- 基础信息
- 流转日志
- 实时动态里的 flow entries
- 运行证据
- todo 快照和 todo 清单

这导致同一事实在 3-4 个区域重复出现，尤其是 flow_log 和 activity stream 重复。

已修复为：

- 顶部保留流程条，表示制度阶段。
- `运行摘要` 合并阶段、派发、未推进时长、目标 Agent。
- `子任务清单` 只负责 todos。
- `任务要点` 只显示状态、阻塞、当前进展、验收标准。
- `实时动态/执行回顾` 合并 flow、progress、tool、thinking 摘要，过滤 todo 快照重复项。
- 卡片上直接暴露关键派发异常，例如运行时未启动、CLI 缺失、派发超时。

## 后续建议落地记录

### 1. JSON state change 接入本地 outbox

已落地。新增 `scripts/runtime_outbox.py`，JSON 看板模式现在有轻量 durable outbox：

- `kanban_update.py state` 在状态真正改变后写入 `handoff` outbox event。
- `kanban_update.py create`、`done`、`confirm` 也会为需要下游 Agent 的状态写入 handoff event。
- handoff event 只表达“状态移交需要派发”，不直接在 Agent 进程里派发，避免 CLI 脚本和 dashboard 争抢 runtime 控制权。
- dashboard worker 会认领 handoff，确认任务仍停留在对应状态且尚未派发，再生成真正的 dispatch request。

这解决的是最关键的可靠性问题：即使 Agent 只写了 JSON 状态，系统也有一条可恢复的“下一棒应派发给谁”的记录。

### 2. `dispatch_for_state()` 迁移为持久队列 worker

已落地。`dispatch_for_state()` 现在不再只依赖瞬时后台线程，而是：

1. 给任务 `_scheduler` 写入 `queued / activeDispatchId / lastDispatchState`。
2. 写入 `runtime_outbox.json` 的 `dispatch` item。
3. 唤起 dashboard outbox worker。
4. worker claim item 后执行 OpenClaw/OpenCode 派发。
5. 成功、失败、超时、CLI 缺失、runtime 离线都会回写 scheduler、event ledger 和 outbox 状态。

启动恢复也已更新：dashboard 启动时会优先检查 pending/running outbox；如果旧任务仍显示 queued 但 outbox item 丢失，才重新创建 dispatch request。

### 3. Trace ID 与统一聚合

已落地第一阶段。JSON 任务现在会获得稳定 `traceId`：

- dashboard 创建任务会写入 `traceId`。
- `kanban_update.py` 触达旧任务时会补齐 `traceId`。
- event ledger 的事件结构新增 `traceId`。
- `/api/task-activity/{id}` 返回 `traceId` 和 `traceSummary`，聚合 event kind、source、outbox 状态。
- `/api/scheduler-state/{id}` 返回 `traceId`、`expectedAgent`、`outbox` summary。
- 任务详情页 `运行摘要` 显示 Trace 和队列状态。

下一阶段可以把 traceId 继续贯穿到 OpenCode/OpenClaw session title、agent message id、工具调用 event，做到跨任务、跨 Agent 的完整检索。

### 4. “思维链路”改为行动证据

已落地。任务详情页不再把思考内容作为单独主栏目，而是统一进入时间线：

- flow / progress / dispatch / tool / assistant activity 统一排序。
- todo 快照默认过滤，避免和子任务清单重复。
- 原“思考摘要”在 UI 上改成折叠式“行动证据”，默认显示短摘要，需要调试时再展开片段。
- 运行摘要保留阶段、派发、未推进时长、目标 Agent、Trace、队列，减少重复 KPI。

## 后续仍建议推进

1. 把 `runtime_outbox.json` 的 worker 指标接到顶部健康栏：pending/running/failed 数量、最旧 pending 年龄、最近 worker heartbeat。
2. 给 outbox item 增加 dead-letter 视图：连续失败后不再静默留在文件里，而是在 UI 中出现“需人工处理”的列表。
3. 把 OpenCode/OpenClaw session id 和 traceId 做强绑定：派发命令 title 已含任务信息，下一步应在 session metadata 或输出解析中写回 trace。
4. 将 JSON outbox 与 Postgres outbox 抽成同一接口，后续切换数据库模式时前端和调度逻辑不用再分叉。
