# JudgeFlow 产品化页面开发交接

> 用途：供新的 Codex 对话窗口直接接手“JudgeFlow 比赛 Demo 产品化页面”开发。  
> 页面样本：[frontend/product-concept.html](../frontend/product-concept.html)  
> 当前状态：产品样本已经确认；2026-08-16 已完成 P0/P1 产品化页面、最小只读投影、页面细节修正和真实数据环境浏览器联调。  
> 范围：只服务 5–8 分钟 GOAI Agent Infra 比赛演示，不建设通用平台。

## 1. 新窗口开始前必须阅读

按顺序完整阅读：

1. [AGENTS.md](../AGENTS.md)；
2. [README.md](../README.md)；
3. [JudgeFlow-开发顺序.md](../JudgeFlow-开发顺序.md)；
4. [JudgeFlow 比赛 Demo 技术设计](JudgeFlow-比赛Demo技术设计.md)；
5. [项目启动手册](../项目启动手册.md)；
6. [阶段五 AgentTeams 验收报告](../验收成果/阶段-5-AgentTeams/验收报告.md)；
7. [阶段六最终 Demo 验收报告](../验收成果/阶段-6-最终Demo/验收报告.md)；
8. 当前真实页面 [frontend/index.html](../frontend/index.html)、[frontend/app.css](../frontend/app.css)、[frontend/refinement.css](../frontend/refinement.css)、[frontend/app.js](../frontend/app.js)；
9. 设计参考样本 [frontend/product-concept.html](../frontend/product-concept.html)；
10. 当前服务端投影 [app/demo/server.py](../app/demo/server.py) 与 [app/demo/service.py](../app/demo/service.py)。

不要根据产品样本推断某个接口或数据已经存在。先以代码、数据库模型和测试为准。

## 2. 唯一开发目标

把已经真实跑通的 JudgeFlow 闭环，按产品样本改造成评委在 5–8 分钟内能够：

1. 一眼看懂裁决链和规则演进链由哪些 Agent 组成；
2. 看见案件如何按 Agent 流转，每个 Agent 收到什么、产出什么；
3. 从案件跳到相关规则，并理解基线版本、候选版本和适用条件；
4. 在人工审批中直接比较规则变更内容与历史案件回放影响；
5. 从回放汇总下钻到逐案新旧裁决，再进入案件详情；
6. 最后用 WorkOrder、Artifact、run_id、trace_id 和哈希证明结果可追溯。

不新增 Agent、房间、数据库表、MCP、Skill、微服务、框架、RAG、消息队列或状态中心。

## 3. 产品样本的权威性和边界

`frontend/product-concept.html` 是已经确认的产品结构和视觉参考，权威内容包括：

- 浅色主题、较大字号和页面整体信息密度；
- 四个一级入口：运行监控、案件中心、规则中心、人工审批；
- 二级页面：案件详情、规则详情、回放详情；
- 首页两条 Agent 链路及独立时间筛选；
- 案件详情横向 Agent 流程图，以及连接线输入、节点输出的表达方式；
- 规则详情的规则说明、引用统计、判定逻辑和版本状态；
- 人工审批中“规则变更内容”和“历史案件回放影响”两部分；
- 回放详情的逐案原裁决、候选裁决和影响方向；
- 规则、案件、回放之间的跳转关系；
- 用户可见中文术语统一使用“规则”。

该文件不是当前运行页面，也不是接口契约：

- 文件内批量案件、监控、审批和回放数字是静态模拟数据；
- 文件内交互由前端脚本模拟，不代表 Agent 正在运行；
- 当前 `app.demo.server` 不会提供该文件，只提供 `index.html`、`app.css`、`refinement.css` 和 `app.js`；
- 开发时可以拆分和重构 HTML/CSS/JS，但不要静默改变已经确认的信息架构和业务边界；
- 保留该文件作为对照样本，不要在实现过程中覆盖或删除。

## 4. 统一术语

用户可见名称统一使用“规则”，不得混用“法条”“法律”“策略”。

| 使用位置 | 统一名称 |
|---|---|
| 导航与目录 | 规则中心 |
| 详情 | 规则详情 |
| 案件关联 | 相关规则 |
| 标识 | 规则编号 |
| 案件冻结版本 | 规则快照 |
| 当前不可变版本 | 基线规则 / 基线版本 |
| Agent 新生成版本 | 候选规则 / 候选版本 |
| 新旧比较 | 规则变更 |
| Python 验证 | 规则回放 / 历史案件回放 |

程序名称保持 `Policy`、`PolicyRow`、`policy_id`、`policy_snapshot_json` 等，不做数据库和代码层机械重命名。

## 5. 当前真实能力

### 5.1 当前页面

当前运行页面是：

- `frontend/index.html`；
- `frontend/app.css`；
- `frontend/refinement.css`；
- `frontend/app.js`。

它保留固定闭环运行、刷新、Human 审批和失败场景验证，并已迁入四个一级入口和案件、规则、回放三个详情页。页面数据来自 `/api/demo/*`；当前目录无 Git 元数据，开发时无法用 `git status/diff` 识别历史改动。

### 5.2 当前 HTTP 接口

`app.demo.server` 保留四个阶段六接口，并新增以下只读投影：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/demo/runs` | 按 `demo_run_id` 运行固定闭环 |
| `GET` | `/api/demo/runs/{demo_run_id}` | 读取一次 Demo 的完整投影 |
| `POST` | `/api/demo/approve` | 记录固定 Demo 的 Human 审批 |
| `POST` | `/api/demo/failures` | 创建固定 TIMEOUT 失败场景 |
| `GET` | `/api/demo/overview?from=&to=` | 双 Agent 链路任务量、任务历时、成功率与业务聚合 |
| `GET` | `/api/demo/cases?q=&status=&policy_id=&page=&page_size=` | 案件检索、综合状态、规则筛选和分页 |
| `GET` | `/api/demo/cases/{case_id}` | 案件摘要、分支与逐 WorkOrder/Artifact 流程 |
| `GET` | `/api/demo/policies?q=&status=&page=&page_size=` | 规则目录、版本和引用次数 |
| `GET` | `/api/demo/policies/{policy_id}?version=&from=&to=` | 规则用途、DSL、版本、引用统计和近期案件 |
| `GET` | `/api/demo/approvals?page=&page_size=` | `AWAITING_APPROVAL` 只读队列与两类审批依据 |
| `GET` | `/api/demo/replays/{replay_run_id}?impact=&q=&page=&page_size=` | 回放上下文、汇总、筛选、分页和逐样本结果 |

现有 `GET /api/demo/runs/{demo_run_id}` 已返回：Case、当前步骤、Artifact、Appeal、PolicyEvolution、基线/候选规则、规则 Diff、ReplayRun、非规则分支、WorkOrder 和执行证据。

### 5.3 当前数据真源

不得新增表。产品化页面使用现有数据：

| 页面信息 | 数据来源 |
|---|---|
| 案件与状态 | `Case` |
| 申诉与白名单 | `Appeal` |
| 规则演进状态 | `PolicyEvolution` |
| Agent 任务、输入引用、耗时、失败 | `WorkOrderRow` |
| Agent 正式输出、运行与追踪编号 | `Artifact` |
| 基线规则和候选规则 | `PolicyRow` |
| 回放汇总和逐案变化 | `ReplayRun.metrics_json`、`case_results_json` |
| 状态迁移、验收、失败、MCP、审批 | `DomainEvent` |
| 组织与职责 | 现有 AgentTeams 资源定义和后端允许的只读投影 |

前端不能直连 PostgreSQL、AgentTeams、Matrix 或 MCP。

## 6. 已完成能力与真实数据缺口

已完成：

- 产品样本的浅色视觉壳和运行监控、案件中心、规则中心、人工审批信息架构；
- 两条固定 Agent 链路的真实 WorkOrder 聚合；
- 案件、规则、审批和回放的真实只读列表/详情与跳转；
- 风险/反证独立 WorkOrder 展示、按实际分支显示申诉链和规则演进链；
- 固定回放样本的逐条新旧结果、影响方向、检索、筛选和分页；
- 技术 ID/哈希证据抽屉，以及原阶段六运行、审批和 TIMEOUT 入口；
- 旧阶段待审批记录保留只读展示；只有符合 `DEMO-STAGE6-*` 的运行记录允许调用当前 Human APPROVE 接口；
- 运行监控按正式产物日期展示案件结果趋势，并将 WorkOrder 创建至结束明确标为“任务历时”，不冒充模型响应时间；
- 案件详情主视图使用业务输入名称，技术 ID 留在证据抽屉；规则引用可跳转，多个申诉分支按规则缺口/证据缺口标注；
- 规则详情支持与首页一致的时间筛选、中文判定条件、候选版本切换和结构化变更查看；
- 人工审批使用待处理列表与选中详情布局，展示申诉原因、基线/候选规则对比，并明确历史记录只读原因；
- 回放详情展示来源案件、候选规则、数据集版本、中文关键事实与独立回放证据，回放样本证据不会复用其他页面状态。

以下内容仍确实缺失或没有可靠来源：

- 样本中的 240 案件、12 条规则、12 条审批和 7 天批量回放数据。
- Token、模型费用和协调 Agent 性能；
- 案件级裁决置信度；现有正式裁决 Schema 没有该字段；
- 回放案件业务时间范围；固定回放样本没有业务时间字段；
- 严格的 Artifact Schema 验收通过率分母；P0 展示 WorkOrder 任务成功率；

尤其注意：当前正式模型没有可信 Token 用量和模型费用字段。没有现有遥测来源时必须显示“—”或不展示，不能估算。

当前表结构也不能严格统计“Artifact Schema 验收通过率”的分母，因为被拒绝的提交不一定形成持久记录。P0 可以改用能够从 WorkOrder 可靠计算的“任务成功率”；若继续展示“产物验收通过率”，必须先证明现有 DomainEvent 能提供完整分母，不能编造。

## 7. 已实现的最小只读投影接口

接口继续位于现有 `app.demo.server` / `DemoService`，没有引入新 Web 框架、服务或数据表。响应统一包含 `ok`、`state` 和真实数据来源标识；列表统一返回 `items` 与 `pagination`。契约由 `tests/test_stage6_demo.py` 覆盖。

| 方法 | 已实现路径 | 用途 |
|---|---|---|
| `GET` | `/api/demo/overview?from=&to=` | 业务趋势、规则命中、各 Agent 任务量/耗时/成功率 |
| `GET` | `/api/demo/cases?q=&status=&policy_id=&page=&page_size=` | 案件中心检索、筛选和分页 |
| `GET` | `/api/demo/cases/{case_id}` | 案件摘要、分支、逐 Agent WorkOrder/Artifact 流程 |
| `GET` | `/api/demo/policies?q=&status=&page=&page_size=` | 规则中心列表 |
| `GET` | `/api/demo/policies/{policy_id}?version=&from=&to=` | 规则说明、DSL、版本、来源、时间筛选和引用统计 |
| `GET` | `/api/demo/approvals?page=&page_size=` | `AWAITING_APPROVAL` 规则演进队列 |
| `GET` | `/api/demo/replays/{replay_run_id}?impact=&q=&page=&page_size=` | 回放上下文、时间范围说明、汇总和逐案结果 |

审批动作优先复用现有 `POST /api/demo/approve` 的 Kernel 逻辑。若为了队列选择必须扩展请求体，只允许传明确的 `evolution_id`、`proposal_artifact_id`、`replay_id`、`reviewer_id`、`decision` 和 `comment`，仍然保存为 `HUMAN_APPROVAL` Artifact；不得增加审批表，不得把批准解释成生产发布。

## 8. 页面与数据映射

### 8.1 运行监控

- 业务趋势：按 `Case.created_at` 和正式 `DECISION_RECORD` / `APPEAL_DECISION` 聚合；
- 规则命中：按正式 Artifact 中的 `policy_ref` 聚合；
- Agent 任务流量：按 `WorkOrderRow.assignee`、`step_type` 聚合；
- 任务历时：只对已结束 WorkOrder 使用 `updated_at - created_at`，不表述为模型响应时间；
- 任务成功率：`SUCCEEDED / 已结束任务`；
- 链路拓扑：使用现有固定 AgentTeams 资源与步骤映射，不根据聊天内容推断；
- 时间筛选：后端接受明确 `from/to`，前端按钮只是生成时间参数。

### 8.2 案件中心与案件详情

- 案件状态只展示一个综合状态字段；
- 一个案件可以关联多条规则，列表和筛选必须按数组处理；
- 创建、更新时间精确到秒；
- 案件详情按 WorkOrder/Artifact 顺序生成节点；
- 连接线展示 `input_refs` 对应的人读摘要；
- 节点三格展示领域输出，不展示重复的技术元数据；
- 风险研判和反证审查必须竖向并列，并证明两张独立 WorkOrder、互不可见意见；
- 没有申诉的案件不显示申诉链；没有 `POLICY_GAP` 的案件不显示规则演进链；
- WorkOrder、Artifact、run_id、trace_id 和哈希放证据抽屉。

### 8.3 规则中心与规则详情

- UI 名称使用“规则”，代码字段保持 `policy_*`；
- 标题/说明来自 `PolicyRow.title`、`description`；
- 判定条件来自 `dsl_json.required_elements`；
- 例外条件来自 `dsl_json.exceptions`；
- 当前版本来自 `(policy_id, version, status)`；
- 候选来源来自 `source_proposal_artifact_id`；
- 引用案件数来自 `Case.policy_snapshot_json` 聚合，不在 `policies` 表伪造计数字段；
- 条件命中和适用例外只有在正式产物能证明时才展示；
- `content_hash` 默认进入技术信息抽屉。

### 8.4 人工审批

首屏只保留两个主要依据：

1. **规则变更内容**：完整展示原规则、候选规则、变更条件及实际影响；
2. **历史案件回放影响**：时间范围、回放案件总数、不受影响、受影响、判轻和判重。

必须满足：

```text
总案件 = 不受影响 + 受影响
受影响 = 判轻 + 判重
```

页面提供规则详情和回放详情跳转。批准只记录比赛审批，不发布候选规则，不替换 BASELINE。

### 8.5 回放详情

- 数据来自 `ReplayRun.case_results_json`；
- 展示样本/案件编号、关键事实、基线裁决、候选裁决和影响方向；
- 支持检索、影响类型筛选和分页；
- 案件编号可以进入案件详情；
- 回放使用的数据集版本与 `dataset_manifest_hash` 放技术信息区；
- 真实阶段六固定数据只有 `TRIGGER`、`DRAFTING`、`SCORING` 三个样本，不能冒充 240 场真实回放。

## 9. 模拟数据与真实数据

产品样本使用批量数据证明列表、筛选、分页和图表布局，但当前运行页面不加载这些 Mock。后续如确有展示需要，必须继续分层：

- **真实主演示数据**：现有阶段六固定 Case、Artifact、ReplayRun、ID 和哈希；
- **确定性产品模拟数据**：为列表和图表准备的固定 fixture 或固定种子数据，必须标注“模拟数据”；
- **禁止**：用随机计时器、假聊天、假运行状态或前端自增进度冒充 Agent 执行。

同一个组件内不要把真实值与模拟值拼成一个未标注的指标。模拟数据必须可重复生成，刷新后不能随机漂移。

当前运行页面只展示真实后端数据和明确的无数据状态，没有为补足数量加入确定性模拟列表。不得为了“全动态”或“大批量效果”增加新表、状态中心或污染真实业务表。

## 10. 当前实施状态与后续顺序

### P0：真实主演示路径产品化（已完成）

1. 为新页面定义响应 Schema 和失败语义；
2. 把 `product-concept.html` 的壳、导航和样式拆入现有 `index.html/app.css/refinement.css/app.js`；
3. 接通现有 `GET /api/demo/runs/{demo_run_id}`；
4. 用真实数据完成主演示案件详情、规则详情、审批和真实回放详情；
5. 保留证据抽屉和 Human 审批边界；
6. 保证当前阶段六页面能力不回退。

### P1：只读列表和聚合（已完成）

1. 实现 overview、cases、policies、approvals、replays 的最小只读投影；
2. 完成时间筛选、搜索、状态/规则筛选和分页；
3. 接通案件、规则、审批、回放之间的跳转；
4. 对无数据、未开始、运行中、成功和失败分别渲染。

### 后续：仅在可靠数据源到位后增量补充

1. 若现有正式 Schema 增加可信字段，再接入 Token、费用、裁决置信度等指标；
2. 若固定回放数据增加业务时间，再展示真实回放时间范围；
3. 若 DomainEvent 能提供完整的验收分母，再评估 Artifact Schema 验收通过率；
4. 继续只运行与改动直接相关的最小测试和浏览器验收，不新增部署组件。

## 11. 最小验收标准

- 页面不直连 Agent、Matrix、MCP 或数据库；
- 真实主演示案件可从监控进入案件详情，再进入规则、审批和回放详情；
- 风险与反证是两张独立 WorkOrder 和两份独立 Artifact；
- 只有 `POLICY_GAP` 进入规则编写和回放；
- `EVIDENCE_GAP` 在归因后关闭，不错误进入规则编写；
- 案件快照始终锁定原规则版本；
- 候选版本不覆盖 BASELINE；
- Human APPROVE 不发布规则；
- 回放指标来自固定 Python 代码；
- 回放汇总与逐案结果严格对账；
- 工具失败不显示成业务无数据；
- Token 无可信来源时显示“—”；
- 模拟数据始终有明显标识；
- 当前阶段六回归测试继续通过；
- 页面完整走查不需要进入 Element 或手工查询数据库。

## 12. 后续维护窗口的第一条指令建议

```text
工作目录：<JUDGEFLOW_REPO>

请先完整阅读 AGENTS.md、README.md、JudgeFlow-开发顺序.md、
docs/JudgeFlow-比赛Demo技术设计.md、项目启动手册.md、
docs/JudgeFlow-产品化页面开发交接.md、frontend/product-concept.html，
以及当前 frontend/index.html、frontend/app.css、frontend/refinement.css、
frontend/app.js、app/demo。

先审计实际代码、当前页面和工作区已有修改，再处理明确的问题或可靠数据源新增项。
保持现有只读投影、业务边界和静态文件技术栈；不新增表、Agent、MCP、Skill、
服务或框架，不把 product-concept.html 中的模拟状态冒充真实运行。
实现后只运行直接相关的最小测试。
```

新窗口不要一开始重建数据库、重装 AgentTeams、复制 opspilot-zero-demo 或替换现有技术栈。
