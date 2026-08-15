# JudgeFlow

[简体中文](README.md) | [English](README.en.md)

JudgeFlow 是一个面向高风险内容治理的多 Agent 调查、裁决、申诉与规则演进系统。它把调查、对抗式研判、独立裁决和规则改进拆成职责隔离、可验证、可追溯的工作流。

当前仓库保存 JudgeFlow 的第一个完整可运行版本。它以固定案件和有限基础设施证明核心闭环，已经针对 5–8 分钟现场演示完成验收；这描述的是当前版本边界，不限定项目后续演进方向。

## 核心闭环

案件裁决：

```text
调查取证 → 风险研判与反证审查 → 独立裁决
```

规则演进：

```text
申诉改判 → 问题归因 → 规则草案 → 确定性回放 → Human 审批
```

当前版本不执行真实处罚或用户权益变更，不发布或替换生产规则，也不建设通用审核平台、生产权限中心、RAG、消息队列或额外微服务。正式产物必须通过结构化 Schema 和后端验收；Agent 不能直接写数据库或推进业务状态。

仓库示例中的 `judgeflow/judgeflow` 只用于绑定本机回环地址的隔离数据库，不得用于公网、共享或生产环境。模型 API Key 和实际数据库连接只通过未提交的私有配置提供。

## 当前能力

- 8 个领域 Agent、3 个协调 Agent 和 1 个 Human 角色；
- AgentTeams / Matrix 负责组织、任务路由和按需唤醒；
- 5 个最小 MCP 工具和 4 个可复用 Skill；
- PostgreSQL 中 8 张业务表，以及不可变 Artifact、规则版本和审计事件；
- Python 确定性规则执行与历史案件回放，LLM 不计算指标；
- 单页运行监控、案件中心、规则中心、人工审批和回放详情；
- 明确区分成功、执行失败、未开始和业务无数据；
- Human APPROVE 只记录当前候选版本的审批结果，不等于生产发布。

## 运行与验收文档

- [项目启动手册](项目启动手册.md)：启动、健康检查、模型配置、重建、恢复和排障入口；
- [产品化页面开发交接](docs/JudgeFlow-产品化页面开发交接.md)：当前页面、只读投影、数据映射和真实数据缺口；
- [最小技术设计](docs/JudgeFlow-比赛Demo技术设计.md)：当前版本架构、Agent 边界、状态机和数据设计；
- [阶段六验收报告](验收成果/阶段-6-最终Demo/验收报告.md)：完整闭环、失败/幂等、数据库证据和演示计时；
- [阶段五 AgentTeams 验收报告](验收成果/阶段-5-AgentTeams/验收报告.md)：AgentTeams、Matrix 和 MCP 动态链路证据；
- [产品开发样本](frontend/product-concept.html)：信息架构和交互参考，文件中的静态模拟数据不进入运行时。

运行当前版本时先阅读项目启动手册。不要根据历史阶段报告重新安装 AgentTeams、重建数据库或删除 Docker volume。

## 目录

```text
app/
├── core/                # JudgeFlowKernel：正式产物验收与业务状态推进
├── demo/                # 标准库 HTTP 服务、固定闭环和只读页面投影
├── agents/              # 八个领域 Agent 的固定 runner 与 MCP 适配
├── agentteams/          # AgentTeams 消息契约与 HTTP MCP transport
├── mcp/                 # 五个 MCP 工具的 FastMCP Server
├── replay/              # 确定性规则执行与回放
├── models/              # Pydantic Schema 和八张业务表模型
└── api/                 # 空占位；当前 HTTP 入口位于 app.demo.server
agentteams/
├── resources/           # Manager、Team、Worker、Human 资源定义
└── packages/            # 各 Agent 的最小运行包
skills/                  # Agent 可复用的工作方法
frontend/                # 当前单页应用和产品设计参考
fixtures/                # 固定案件、申诉、证据、规则和回放数据
tests/                   # Schema、契约和主流程测试
migrations/              # 本项目从零产生的数据库迁移
docs/                    # 当前版本技术设计和开发交接
验收成果/                # 各阶段的历史验收证据
```

目录中若只有 `.gitkeep`，它只是脚手架占位，不代表已有实现。

## 运行页面

启动、健康检查和私有数据库配置统一见[项目启动手册](项目启动手册.md)。页面进程启动后访问 `http://127.0.0.1:8086/`。

当前页面由 `frontend/index.html`、`frontend/app.css`、`frontend/refinement.css` 和 `frontend/app.js` 提供，通过 `/api/demo/*` 接入运行监控、案件、规则、待审批和回放等真实投影。页面不直连数据库、Agent、Matrix 或 MCP。

固定演示路径：

1. 查看案件和锁定规则；
2. 运行调查、风险、反证和独立裁决；
3. 查看冻结快照、新增证据和申诉改判；
4. 对比 `POLICY_GAP` 与 `EVIDENCE_GAP → CLOSED`；
5. 查看候选规则 Diff 和确定性回放；
6. 记录 Human APPROVE，并确认候选规则未发布、BASELINE 未替换；
7. 展开 WorkOrder、Artifact、`run_id`、`trace_id` 和 TIMEOUT 失败证据。

## 测试

```bash
UV_CACHE_DIR=/tmp/judgeflow-uv-cache uv run pytest -q
```

不设置 `TEST_DATABASE_URL` 时，PostgreSQL 在线迁移和持久化测试会明确跳过。完整验收命令及当前基线见[项目启动手册](项目启动手册.md)。

## 许可证

当前仓库未授予开源许可证，默认保留全部权利。
