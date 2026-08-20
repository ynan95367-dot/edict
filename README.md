<h1 align="center">📋 后摩软件 JIRA 管理助手</h1>

<p align="center">
  <strong>基于三省六部框架改造的 JIRA 多 Agent 协作系统。<br>一句话建 JIRA 单，问题找人。</strong>
</p>

<p align="center">
  <sub>10 个 AI Agent（1 个编排者 + 6 个角色 Agent + 3 个基础服务 Agent）组成 JIRA 管理流水线：<br>太子编排分发 → 六部角色执行 → 基础服务层对接 JIRA API。</sub>
</p>

<p align="center">
  <a href="#-30-秒快速体验">🚀 30 秒体验</a> ·
  <a href="#-架构">🏛️ 架构</a> ·
  <a href="#-功能全景">📋 看板功能</a> ·
  <a href="docs/task-dispatch-architecture.md">📚 架构文档</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/OpenClaw-Required-blue?style=flat-square" alt="OpenClaw">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Agents-10_Specialized-8B5CF6?style=flat-square" alt="Agents">
  <img src="https://img.shields.io/badge/Dashboard-Real--time-F59E0B?style=flat-square" alt="Dashboard">
  <img src="https://img.shields.io/badge/License-MIT-22C55E?style=flat-square" alt="License">
  <img src="https://img.shields.io/badge/Frontend-React_18-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React">
  <img src="https://img.shields.io/badge/Backend-stdlib_only-EC4899?style=flat-square" alt="Zero Backend Dependencies">
</p>

---

## 🤔 为什么要做这个？

JIRA 管理有三大痛点：

- **建单门槛高**：在 JIRA 里新建一个单需要填 5-8 个字段，多人协作时格式不一
- **信息分散**：JIRA 单分布在不同的项目和版本中，缺乏统一的视图
- **跟踪靠人**：谁逾期了、哪个版本进度如何，要靠人工盯

**这套 JIRA 管理助手解决的就是这三个问题：**

```
飞书说一句话 → AI 自动建 JIRA 单 → 自动关联版本 → 六部角色自动协作 → 实时看板跟踪全局进度
```

## ✨ 功能亮点

- **一句话建单**：飞书消息直达，AI 自动填充模板、查重、建单
- **六部专责**：提出者、建单者、经办者、验证者、维护者、跟踪者各司其职
- **全局看板**：实时展示所有 JIRA 单的状态、流转链路和人员负载
- **版本跟踪**：自动维护大版本⇄模块版本映射，进度计算和风险预警
- **报表生成**：HTML 进度报表、燃尽图、工作量矩阵，定时推送飞书
- **扁平路由**：三省审核流程已移除，太子直达六部，秒级响应

---

## 🏛️ 架构

```
                        ┌──────────────────────────┐
                        │      用户 (飞书消息)        │
                        └───────────┬──────────────┘
                                    │
                        ┌───────────▼──────────────┐
                        │   🎯 太子 (taizi)         │
                        │   编排者：意图识别+路由分发     │
                        │   直接路由到对应六部，不再审批    │
                        └──┬──────┬──────┬──────┬───┘
                           │      │      │      │
              ┌────────────┼──────┼──────┼──────┼────────────┐
              │            │      │      │      │            │
         ┌────▼──┐  ┌─────▼──┐ ┌▼────┐ ┌▼────┐ ┌▼────┐ ┌───▼─────┐
         │💰 户部 │  │📝 礼部 │ │⚔️ 兵部│ │⚖️ 刑部│ │🔧 工部│ │👔 吏部  │
         │提出者  │  │建单者  │ │经办者│ │验证者│ │维护者│ │跟踪者  │
         └───┬───┘  └────┬───┘ └──┬──┘ └──┬──┘ └──┬──┘ └───┬─────┘
             │           │        │       │       │        │
             │           │        │       │       │        │
             └───────────┼────────┼───────┼───────┼────────┘
                         │        │       │       │
                         │   subagent 调用        │
                         ▼        ▼       ▼       ▼
              ┌──────────────────────────────────────────┐
              │          基础服务层 (3 Agents)             │
              │                                          │
              │  ┌────────────┐  ┌──────────┐  ┌──────┐  │
              │  │ 🔗 JIRA交互│  │ 📊 版本集成│  │ 📈 报表│  │
              │  │ (API 网关) │  │ (版本映射) │  │ (生成) │  │
              │  └─────┬──────┘  └─────┬────┘  └──┬───┘  │
              └────────┼───────────────┼──────────┼──────┘
                       │               │          │
                       ▼               ▼          ▼
                   JIRA API       飞书多维表格     HTML报表
```

### 核心设计原则

```
太子不认识基础服务，六部不需要知道 HTTP 请求怎么发。
基础服务不关心业务逻辑，只负责执行。
```

| 层 | 知道什么 | 不知道什么 |
|:---|:---|:---|
| **太子** | 用户意图→路由给谁 | 怎么操作 JIRA、版本怎么算 |
| **六部** | 什么时候该调 JIRA/版本/报表 | HTTP 请求怎么发、token 怎么刷新 |
| **基础服务** | JIRA API 格式、限流逻辑、HTML 模板 | 业务上下文、该不该创建这个单 |

---

## 📋 Agent 职责一览

### 三角色层（六部）

| Agent | 部门 | 职责 | 典型场景 |
|:---|:---|:---|:---|
| `hubu` | 户部 | 需求提出者 | 匹配 JIRA 模板、查重、建单 |
| `libu` | 礼部 | JIRA 建单者 | 模板填充、格式规范、文档生成 |
| `bingbu` | 兵部 | JIRA 经办者 | 查待办、更新单状态、经办操作 |
| `xingbu` | 刑部 | JIRA 验证者 | 单完整性检查、版本批量验证 |
| `gongbu` | 工部 | 系统维护者 | 健康检查、Webhook 配置、故障排查 |
| `libu_hr` | 吏部 | JIRA 跟踪者 | 进度汇总、逾期预警、日报生成 |

### 基础服务层

| Agent | 名称 | 职责 | 被谁调用 |
|:---|:---|:---|:---|
| `jira_bridge` | JIRA 交互 | 统一封装 JIRA REST API，限流/重试/Webhook | 六部 |
| `version_integrator` | 版本集成 | 维护版本映射表、进度计算、依赖分析 | 户部、刑部、吏部 |
| `report_generator` | 报表生成 | 生成 HTML 报表、燃尽图、定时快照 | 吏部 |

### 编排层

| Agent | 职责 |
|:---|:---|
| `taizi`（太子） | 接收飞书消息 → 识别意图 → 直接路由到对应六部 |

---

## 🚀 30 秒快速体验

### 前置条件
- [OpenClaw](https://openclaw.ai) 已安装
- Python 3.10+
- macOS / Linux

### 安装

```bash
git clone https://github.com/ynan95367-dot/edict.git
cd edict
chmod +x install.sh && ./install.sh
```

安装脚本自动完成：
- ✅ 创建 10 个 Agent Workspace
- ✅ 写入各 Agent 的 SOUL.md（JIRA 管理角色定义）
- ✅ 注册 Agent 及权限矩阵到 `openclaw.json`
- ✅ 符号链接统一数据目录
- ✅ 设置 Agent 间通信可见性
- ✅ 同步 API Key 到所有 Agent
- ✅ 初始化数据目录
- ✅ 重启 Gateway 使配置生效

> ⚠️ **首次安装**：需先配置 API Key：`openclaw agents add taizi`，然后重新运行 `./install.sh` 同步到所有 Agent。

### 启动

```bash
# 方式 1：一键启动
chmod +x start.sh && ./start.sh

# 方式 2：分别启动
bash scripts/run_loop.sh &      # 数据刷新循环
python3 dashboard/server.py     # 看板服务器

# 打开浏览器
open http://127.0.0.1:7891
```

---

## 🔄 任务流转流程

```
用户飞书说："模块A登录接口报500，版本v1.3.1"

① 太子
   ├─ 意图识别 → "建故障单"
   └─ 路由 → 礼部（建单者匹配模板）

② 礼部
   ├─ 匹配模板 → "HMSW故障模板"
   ├─ 提取字段 → 模块A、v1.3.1、优先级=High
   └─ subagent 调用 JIRA交互Agent → POST 建单

③ JIRA交互Agent
   ├─ 创建 JIRA 单 → 返回 HMSW-789
   ├─ 通知 版本集成Agent → 更新版本映射
   └─ 返回结果给礼部

④ 礼部 → 返回给太子 → 太子回复用户
   "已创建 HMSW-789，模块A v1.3.1，优先级 High"
```

### 状态机（7 状态）

```
Pending (待编排) → Taizi (分拣中) → Doing (执行中) → Review (验证中) → Done (完成)
                                                            ↓
                                                      Blocked (阻塞)
```

原三省六部的 9 状态（含 Zhongshu/Menxia/Assigned/Next/PendingConfirm）已简化为 7 状态。

---

## 📁 项目结构

```
edict/
├── agents/                     # 10 个 Agent 的 SOUL.md 模板
│   ├── taizi/SOUL.md           # 太子 · 编排者
│   ├── hubu/SOUL.md            # 户部 · JIRA 提出者
│   ├── libu/SOUL.md            # 礼部 · JIRA 建单者
│   ├── bingbu/SOUL.md          # 兵部 · JIRA 经办者
│   ├── xingbu/SOUL.md          # 刑部 · JIRA 验证者
│   ├── gongbu/SOUL.md          # 工部 · JIRA 维护者
│   ├── libu_hr/SOUL.md         # 吏部 · JIRA 跟踪者
│   ├── jira_bridge/SOUL.md     # JIRA 交互服务
│   ├── version_integrator/SOUL.md  # 版本集成服务
│   ├── report_generator/SOUL.md    # 报表生成服务
│   └── GLOBAL.md               # 全局配置
├── dashboard/
│   ├── dashboard.html          # 军机处看板
│   ├── auth.py                 # 看板登录鉴权
│   └── server.py               # API 服务器
├── scripts/
│   ├── kanban_update.py        # 看板 CLI（状态机+权限检测）
│   ├── run_loop.sh             # 数据刷新循环
│   └── file_lock.py            # 文件锁
├── data/                       # 运行时数据
├── docs/                       # 文档
├── install.sh                  # 一键安装
├── start.sh                    # 一键启动
└── LICENSE                     # MIT License
```

---

## 🔧 技术亮点

| 特点 | 说明 |
|:---|:---|
| **React 18 前端** | 实时看板，10+ 功能面板 |
| **纯 stdlib 后端** | `server.py` 基于 `http.server`，零依赖 |
| **状态机审计** | 严格生命周期状态转换 + 完整审计日志 |
| **越权拦截** | `kanban_update.py` 内置 Agent 权限策略，跨部门操作被拒绝 |
| **文件锁** | 防多 Agent 并发写入 `tasks_source.json` |
| **一键安装** | `install.sh` 自动完成全部配置 |
| **15 秒同步** | 数据自动刷新，看板实时更新 |

---

## 📮 改造说明

本项目基于 [cft0808/edict](https://github.com/cft0808/edict)（三省六部框架）改造而来。

### 与原版的关键差异

| 维度 | 原版（三省六部） | 本版（JIRA 助手） |
|:---|:---|:---|
| Agent 数量 | 12 | 10 |
| 流程 | 太子→中书→门下→尚书→六部 | 太子→六部→基础服务 |
| 审核 | 门下省强制审议 | 刑部验证可选 |
| 执行 | 代码/文档/部署 | JIRA API 操作 |
| 三省 | 中书省+门下省+尚书省 | 已移除 |
| 新增 | 无 | JIRA交互+版本集成+报表生成 |

---

## 📄 License

[MIT](LICENSE) · 基于 [cft0808/edict](https://github.com/cft0808/edict) 改造

---

<p align="center">
  <strong>📋 后摩软件 JIRA 管理助手</strong><br>
  <sub>降低建单门槛，加速版本交付</sub>
</p>
