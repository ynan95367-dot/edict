# 三省六部最新版独立化与旧版差异梳理

> 梳理时间：2026-06-10  
> 对比口径：当前工作区 `codex/agent-control-plane-upgrade-20260602` / `HEAD=3d3bd75` 对比本地 `main=62f5a4a`。  
> 说明：当前分支与 `main` 未检测到共同 merge-base，因此这里按“文件树差异 + 当前功能结构”做版本对比，而不是按普通 PR 增量解释。

## 结论

最新版可以独立出来，而且应该独立成一个新的产品线：**三省六部 Agent Control Plane / Agent Workbench 版**。

它和旧版已经不是“同一个看板的功能增强”关系，而是定位发生了变化：

- 旧版核心是 **OpenClaw 多 Agent 治理看板**：强调三省六部流程、任务状态、奏折归档、模板和监控。
- 最新版核心是 **Agent Runtime 控制面**：强调 RunSpec、权限闸门、OpenCode/OpenClaw runtime 分离、模型健康、任务证据链、session 恢复、执行隔离和 coding cockpit。

建议独立时保留旧版作为稳定的 Edict / OpenClaw Dashboard，最新版另起仓库或长期分支，例如：

- `openclaw-sansheng-liubu`：保留旧版公开主线。
- `sansheng-liubu-control-plane`：承载最新版 Agent Workbench / Control Plane。
- `sansheng-liubu-bytedance-v9`：如果只是要分享材料，则只独立演示 deck，不必带整个产品代码。

## 独立化判断

### 可以独立的原因

1. 产品定位已经分层  
   最新版把 Agent Runtime 和 Agent Control Plane 分开：OpenCode/OpenClaw/Codex 可以作为执行层，三省六部负责治理、调度、观测和归档。

2. 代码边界已经成形  
   最新版新增了共享控制面契约 `edict/control_plane.py`，前端也从单一任务看板扩展为总控台、命令中心、模型控制、输出、任务证据链等模块。

3. Runtime 接入已经从旧版脱离  
   最新版新增 `opencode.json`、`.opencode/prompts/*`、`scripts/start_opencode.sh`、`scripts/sync_opencode_agents.py`、`scripts/runtime_outbox.py` 等 OpenCode 运行时资产，不再只是旧版 OpenClaw 数据刷新。

4. 验证面更完整  
   最新版新增或扩展了模型健康、事件日志、OpenCode 同步、任务派发、状态机一致性、webhook 等测试，适合单独维护。

### 需要注意的风险

1. 不建议直接把最新版强行合回旧 `main`  
   树差异达到 `110 files changed, 26883 insertions, 1914 deletions`，并且两条线没有普通 merge-base，合并成本会高于拆分成本。

2. 最新版仍混有两套后端形态  
   当前启动脚本主要跑 `dashboard/server.py` 这个 stdlib HTTP server；同时仓库里也有 `edict/backend/app/*` FastAPI/DB/EventBus 方向。独立时要决定：
   - 短期保留 `dashboard/server.py` 作为实际可运行主线；
   - FastAPI 后端作为未来控制面服务化方向，先放 `experimental` 或 `backend-next`。

3. 输出产物不能全量带走  
   `outputs/`、`logs/`、`tmp/`、大量历史 morning brief 和锁文件不适合作为新版源码仓库主体；只保留 demo 必需截图、v9 deck 或少量示例数据。

## 建议独立方案

### 方案 A：最快可用的新仓库

适合先把最新版作为一个独立产品保留下来。

保留：

- 根启动与安装：`README.md`、`install.sh`、`install.ps1`、`start.sh`、`edict.sh`、`edict.ps1`、`Dockerfile`、`docker-compose.yml`
- 控制面核心：`dashboard/server.py`、`edict/control_plane.py`
- 前端源码与构建产物：`edict/frontend/`、`dashboard/dist/`
- Runtime 配置：`opencode.json`、`.opencode/prompts/`、`agents/groups/`、`agents.json.legacy`
- 运行脚本：`scripts/start_opencode.*`、`scripts/run_loop_opencode.*`、`scripts/sync_opencode_agents.py`、`scripts/runtime_outbox.py`、`scripts/event_log.py`、`scripts/kanban_update.py`、`scripts/apply_model_changes.py`、`scripts/refresh_watcher.py`
- 数据样例：`docker/demo_data/`、必要的 `data/schema.json`
- 测试：`tests/test_model_health.py`、`tests/test_event_log.py`、`tests/test_sync_opencode_agents.py`、`tests/test_dashboard_dispatch.py`、`tests/test_server.py` 等新版覆盖
- 文档：`docs/cft0808-agent-control-plane-upgrade.md`、`docs/edict-coding-cockpit-plan.md`、`docs/harness-runtime-review.md`、`docs/getting-started.md`

排除：

- `logs/`
- `tmp/`
- `data/*.lock`
- 旧版单文件看板 `dashboard/dashboard.html`
- 大部分历史 `outputs/`
- 个人临时报告，如无发布需要则不进源码仓库

### 方案 B：干净产品化拆分

适合后续正式维护。

建议拆成三层：

1. `control-plane-core`  
   放状态机、RunSpec、Policy、Capability、Event、Artifact 的纯逻辑和 schema。

2. `workbench-web`  
   放 React/Vite 前端、dashboard API、模型配置、命令中心、任务详情驾驶舱。

3. `runtime-adapters`  
   放 OpenCode/OpenClaw/Codex adapter、session probe、模型发现、outbox、worktree/checkpoint。

这条路更干净，但需要补 package 边界、配置加载、测试 fixtures 和启动脚本。

### 方案 C：只独立演示最新版

如果你说的“最新版”指的是分享 PPT，那么可以只独立：

- `outputs/.../presentations/sansheng-liubu-bytedance-v9/`
- 最终 PPTX：`output/sansheng-liubu-agent-workbench-bytedance-v9.pptx`
- contact sheet：`output/contact-sheet-v9.png`
- 源码：`slides/deck.mjs`、`layout/`、`assets/`

v9 是当前演示资料最新版：24 页，有最终 PPTX 和 contact sheet。v8 也是 24 页；v9 相比 v8 主要是讲法收敛、页脚版本、部分图表/措辞调整，结构不是大改。

## 最新版 vs 旧版核心区别

| 维度 | 旧版 main | 最新版当前分支 |
|---|---|---|
| 产品定位 | 三省六部 OpenClaw 多 Agent 看板 | Agent Control Plane / Agent Workbench |
| 任务入口 | 模板、看板、既有流程为主 | 命令中心生成 RunSpec，支持预览、创建、权限摘要 |
| Runtime | 以 OpenClaw / 本地数据同步为主 | OpenCode / OpenClaw 分离，具备 OpenCode server、CLI、model registry、session probe |
| 治理方式 | 固定三省六部状态流转 | 按风险展开治理链路，加入 Policy Gate、Capability、tool policy |
| 状态机 | 看板状态 + kanban_update 校验 | `edict/control_plane.py` 统一契约，前后端共享状态、部门、Agent 映射 |
| 可观测性 | flow log、任务进度、心跳 | event ledger、runtime outbox、worker health、model health、coding session evidence |
| 模型配置 | 看板切模型，偏静态配置 | OpenCode live model catalog、模型延迟探针、失败降级、写回配置 |
| 故障恢复 | 更多依赖人工重试或流程按钮 | session stale 检测、OpenCode probe、自愈重启、重试和诊断状态 |
| 执行隔离 | 旧版任务执行隔离弱 | worktree/checkpoint/patch review/isolation panel 已进入设计和 UI |
| 前端结构 | 面板式 Dashboard | 总控台：CommandCenter、OutputPanel、模型配置拆分、任务详情拆分 |
| 后端 API | 看板读写与任务操作 | 新增 `/api/capabilities`、`/api/runs/preview`、`/api/runs/create`、`/api/model-health`、`/api/coding-session/{id}` 等 |
| 测试覆盖 | 基础看板、状态、同步测试 | 新增 model health、event log、OpenCode sync、dispatch、race、webhook、state consistency |
| 对外叙事 | 古代制度改造 Multi-Agent 编排 | AI PC / Agent Runtime 时代的任务控制面 |

## 已提交代码差异摘要

当前分支相对本地 `main` 的已提交树差异：

- `110 files changed`
- `26883 insertions`
- `1914 deletions`
- 新增 12 份 `.opencode/prompts/*.md`
- 新增 `opencode.json`
- 新增 `edict/control_plane.py`
- 新增 `CommandCenter`、`OutputPanel`、`WorkerHealthPanel`
- 拆分 `ModelConfig` 为模型健康、模型注册表、模型变更日志、手动模型表单、Agent 模型网格等组件
- 拆分 `TaskModal` 为 coding session、证据链、隔离、patch review、调度诊断、source preview、todo、notes、outputs 等子模块
- 大幅扩展 `dashboard/server.py`
- 新增 OpenCode 启动、同步、运行循环、runtime outbox、事件日志脚本
- 增加模型健康、事件日志、OpenCode 同步、webhook、状态机一致性等测试

## 当前未提交本地改动

工作区还有两类本地改动，独立前需要决定是否纳入：

1. `opencode.json`  
   所有 Agent 模型被本地改为 `opencode/nemotron-3-ultra-free`。这更像当前运行环境配置，建议独立源码时不要固定死，改成环境变量默认值或示例配置。

2. `docs/wechat-article.md`  
   新增“探针自愈与守护拉起机制”段落。适合纳入新版叙事文档，但它不是代码依赖。

另有多个未跟踪输出和报告目录，建议按发布需要选择，不要默认并入源码仓库。

## PPT / 分享材料版本差异

| 版本 | 页数 | 输出状态 | 主要定位 |
|---|---:|---|---|
| v5 | 22 | 有 PPTX / contact sheet / speaker notes | 较早版 Agent Workbench 分享 |
| v6 | 24 | 有 PPTX / contact sheet / speaker notes | Agent Control Plane 叙事增强 |
| v7 | 21 | 有 PPTX / contact sheet / speaker notes | 精简优化版本 |
| v8 | 24 | 有 PPTX / contact sheet / speaker notes | 个人 Agent Workbench 工程复盘 |
| v9 | 24 | 有 PPTX / contact sheet | 当前最新版，语气更正式，定位更收敛 |

v9 相对 v8：

- 页脚从“技术分享 V8 · 一个个人 Agent Workbench 的工程复盘”调整为“技术分享 V9 · Agent Workbench 控制面工程复盘”。
- 首页从“真实故障 / 架构 review / 欢迎拍砖”调整为“工程动机 / 架构复盘 / 开放讨论”。
- 语气从自黑、现场感更强，调整为更正式的工程复盘表达。
- 第 6 页由风险表格改成控制面链路流图，强化“按任务风险展开治理链路”。
- 增加 `laneLabel`、`statusDot` 等 deck 辅助函数。
- `slide-*.mjs` 基本未变，主要差异集中在 `slides/deck.mjs`。

## 推荐下一步

1. 先确认“独立出来”指产品代码还是演示材料。  
   如果是产品代码，走方案 A；如果是 PPT，走方案 C。

2. 若走产品独立，先做一个 clean tree。  
   建议新建目录或新仓库，只迁移必要文件，并补 `.gitignore` 排除 `logs/`、`tmp/`、`data/*.lock`、历史 outputs。

3. 把 `opencode.json` 改成示例配置。  
   当前本地模型全指向 `opencode/nemotron-3-ultra-free`，适合个人测试，不适合固定为独立版默认。

4. 给新版单独写 README。  
   标题不要再只是“古代制度多 Agent”，而要明确：`三省六部 · Agent Control Plane / Agent Workbench`。

5. 留一个旧版对照标签。  
   给旧版 main 打 tag，例如 `legacy-openclaw-dashboard`，给新版打 tag，例如 `control-plane-v0.1`，后续文章和演示引用会清楚很多。
