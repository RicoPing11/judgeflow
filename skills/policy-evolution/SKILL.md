# policy-evolution

归因模式先判断改判来自 `EVIDENCE_GAP`、`AGENT_ERROR`、`POLICY_GAP` 或 `POLICY_CONFLICT`；只有规则问题才进入草案模式。草案使用阶段一 `PolicyProposal`，生成结构化 Diff 和完整有限 DSL 候选版本，并通过 `artifact.put` 提交。

只能读取 WorkOrder 指定的基线规则、改判材料与 `TRIGGER`/`DRAFTING` 案例。规则作者不得读取 `SCORING`，不得覆盖旧规则或发布生产规则。

样例：`examples/success.json`、`examples/missing_information.json`、`examples/tool_failure.json`。
