# 三省六部自用稳定版 V1 设计

日期：2026-06-12

## 目标

自用稳定版 V1 的目标不是继续扩展功能，而是让三省六部成为用户日常敢用的本地 Agent 控制台。验收样本固定为“当前项目代码修改任务”，因为这类任务会同时压测 RunSpec、权限审批、OpenCode 派发、文件修改、测试验证、产物回写和最终审查。

V1 成功的标准是：

- 用户下达小型代码修改任务后，系统大多数情况下能自动跑到 Review 或 Done。
- 如果任务失败，系统能明确说明卡在 approval、runtime、model、queue、test 或 workspace 哪一层。
- 当前任务判断不被 T-4 这类幽灵任务、历史 outbox 失败或旧模型健康记录染红。
- 任务详情首屏在 10 秒内能看懂：现在发生了什么、是否需要用户动作、下一步是什么。

## 非目标

V1 不做开源包装，不新增大功能，不重做整套前端视觉，不把 JSON 存储迁移到数据库，也不把 `dashboard/server.py` 一次性拆成完整后端服务。长期需要这些事，但它们不应阻塞自用稳定。

## 用户路径

验收任务示例：

> 修复三省六部当前项目里的一个小 bug，并运行对应测试，最后告诉我改了什么。

理想路径：

1. CommandCenter 将用户目标整理为 RunSpec，识别为代码修改任务。
2. RunSpec 给出目标部门、能力需求、风险等级、交付物和测试要求。
3. Policy Gate 明确告诉用户：是否需要命令执行、文件写入、patch 审批或专属 worktree。
4. 用户准奏后，任务进入 runtime outbox，并交给 OpenCode 执行。
5. 执行过程持续回写行动证据：读取文件、修改文件、运行测试、生成报告。
6. 任务进入 Review 或 PendingConfirm 时，任务详情说明“你现在要审什么、准奏后会怎样、封驳后会怎样”。
7. 任务 Done 后，详情页展示改动文件、测试结果、输出文件和下一步建议。

## 架构边界

V1 采用最小稳定闭环，而不是大重构。

### CommandCenter

职责：

- 收集目标、模式、优先级和能力偏好。
- 调用 RunSpec preview/create。
- 展示风险、能力、隔离和审批预期。

不负责：

- 判断 OpenCode 当前是否真的能执行。
- 展示 outbox 失败细节。

### Policy Gate

职责：

- 将代码修改任务中的命令执行、文件写入和 patch 审批解释成人能理解的审批请求。
- 在任务详情中统一呈现 Menxia、Review、PendingConfirm 和 policy-held 这几类待决状态。

不负责：

- 自动忽略审批。
- 在用户不知情时执行高风险命令。

### Runtime Outbox

职责：

- 作为任务交办的 durable queue。
- 分类 pending、running、failed、archived、done。
- 区分当前任务真实阻塞、幽灵任务、历史失败和已归档死信。
- 提供可操作建议：扫描证据、重新交办、归档幽灵项、切换模型或升级协调。

不负责：

- 直接解释所有业务流程。
- 把全局失败简单映射成当前任务失败。

### OpenCode Runtime

职责：

- 实际执行代码阅读、文件修改、命令运行和测试。
- 将 session、trace、工具调用和输出文件回写到控制面可聚合的数据源。

不负责：

- 决定任务是否该执行。
- 替代 Policy Gate 做用户审批。

### TaskModal

职责：

- 首屏回答三个问题：当前结论、要用户做什么、最近关键证据。
- 将详细日志、模型、outbox、session 和 patch 证据折叠到诊断区。
- 对失败给出分层解释和主操作按钮。

不负责：

- 平铺所有日志。
- 把内部术语直接暴露给用户作为结论。

## 错误分类

V1 的错误必须进入明确层级。

| 层级 | 典型症状 | 用户解释 | 主操作 |
| --- | --- | --- | --- |
| approval | 等待 Menxia、Review、PendingConfirm 或 policy-held | 等你确认方案、权限或结果 | 准奏 / 封驳 |
| runtime | OpenCode server/session 不可用 | 执行器没接住任务 | 重启/扫描/重新交办 |
| model | timeout、not supported、certificate verification error | 模型或上游连接异常 | 切换模型 / 退避重试 |
| queue | outbox pending/running/failed 异常 | 队列里还有未处理执行请求 | 扫描 / 重试 / 归档 |
| workspace | worktree 分配失败、patch 失败、文件不可写 | 工作区准备失败 | 查看路径 / 回滚 / 重新分配 |
| test | 测试命令失败 | 代码已执行但验证未通过 | 查看测试输出 / 退回复修 |

全局健康栏必须避免一个红色数字吞掉所有语义。当前任务、幽灵任务、历史死信和真实 runtime/model 故障应分开显示。

## UI 收敛

任务详情首屏只显示：

1. 当前结论：正常推进、等待你、需要处理、已完成。
2. 待用户动作：准奏、封驳、扫描、重新交办、归档幽灵项或继续等待。
3. 最近关键证据：最多 5 条，包括状态变化、工具调用、测试结果、输出文件。

默认折叠：

- 完整活动流。
- runtime outbox 明细。
- 模型健康和探针记录。
- OpenCode session 细节。
- patch diff 和文件证据。

内部术语需要翻译：

- “派发失败”显示为“执行请求没有被 OpenCode 接住”或“模型连接失败”。
- `PendingConfirm` 显示为“待御批”。
- `state-handoff-scan` 显示为“巡检发现需要交办下一步”。
- `missingTask` 显示为“历史幽灵任务，可归档”。

## 数据流

代码修改任务的关键数据流：

1. 用户输入进入 RunSpec。
2. RunSpec 写入任务和 `run_specs.json`。
3. 审批状态写入任务 `runSpec.policyGate` 和 `_scheduler`。
4. 状态变更写入 `flow_log`、event ledger 和 `runtime_outbox` handoff。
5. dashboard worker 将 handoff 转为 dispatch。
6. OpenCode 执行结果写回 event ledger、scheduler、outbox 和输出文件。
7. TaskModal 聚合 task activity、scheduler state、coding session、task evidence 和 output group。

V1 的关键改造点是聚合层：同一事实只在一个首屏位置表达，详细证据只在折叠区展开。

## 验收策略

准备 5 个小型代码修改任务：

1. 修改一处 UI 文案并跑前端构建。
2. 修复一个后端诊断文案或分类 bug 并跑对应 pytest。
3. 增加一个小型回归测试并让它通过。
4. 清理一个幽灵 outbox 诊断误报。
5. 修改一个任务详情展示逻辑并跑前端构建。

通过标准：

- 至少 4 个任务自动走到 Review 或 Done。
- 失败任务必须明确归因到一个错误层级。
- 任何任务都不能因为无关的 T-4、历史 dead letter 或旧模型记录被判定为当前失败。
- 任务详情首屏能够直接说明当前状态和主操作。
- 验证命令至少覆盖对应 pytest 和前端构建。

## 实施切分建议

实施时按四个小阶段推进：

1. Runtime health 分类：拆分当前任务、全局死信、幽灵任务和模型连接失败。
2. OpenCode 稳定策略：证书错误分类、退避重试、模型锁定和失败冷却。
3. TaskModal 首屏收敛：当前结论、用户动作、最近证据三块默认可见。
4. 五任务验收：跑 5 个代码修改任务，记录成功率、失败原因和体验问题。

每个阶段都应先补回归测试，再做实现，最后用真实任务 smoke test。

## 风险

- 如果 OpenCode 证书错误来自系统 CA 或上游服务，代码层只能做重试和解释，不能完全消除根因。
- 如果继续在 `dashboard/server.py` 内堆逻辑，短期效率高，但长期维护压力会继续增加。
- 如果 UI 收敛过度，调试信息可能不够，所以详细证据必须保留在折叠区。

## 决策

自用稳定版 V1 采用稳定性优先策略。先让代码修改任务可跑、可解释、可收口，再进入开源传播版的安装、文档、截图和 demo 数据整理。
