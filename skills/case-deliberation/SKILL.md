# case-deliberation

用于 `RISK`、`COUNTER`、`DECISION`、`APPEAL` 四种固定模式。读取锁定规则，逐项检查要件与例外，每个正式结论同时引用 `evidence_id` 和规则条款 ID；只提交当前 WorkOrder 期待的 Artifact 类型。

`APPEAL` 模式只能使用 `context.get` 返回的原案不可变快照以及 `allowed_evidence_ids` 中的新增证据，禁止读取原 Team 对话或白名单外证据。缺失信息与工具失败都必须显式保留。

样例：`examples/success.json`、`examples/missing_information.json`、`examples/tool_failure.json`。
