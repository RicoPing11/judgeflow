# case-investigation

用于固定 Demo 的调查取证。收到 `WORK_ORDER_ASSIGNED` 后必须完成下面四步，不得改序、扩展查询或停在分析阶段：

1. 调用 `work_order.get`，请求体必须且只含 ID 消息中的 `work_order_id`、`run_id`、`trace_id`；核对 `assignee_id=case-investigator`、`step_type=INVESTIGATION`、`expected_artifact_type=EVIDENCE_BUNDLE`，并核对返回值与消息中的三个 ID 一致。
2. 调用 `context.get`，只领取任务授权材料。
3. 只对 WorkOrder `input_refs` 中 `ref_type=EVIDENCE` 的引用调用一次 `evidence.search`。普通案件固定使用 `query_type="CONTENT"`；只有 `evidence_id` 以 `E-APPEAL` 开头时才使用 `query_type="APPEAL_SUBMISSION"`。禁止试探或枚举其他 `query_type`。
4. 查询返回后立即组装完整 `EVIDENCE_BUNDLE` 信封并调用一次 `artifact.put`；不得继续搜索、读取 MinIO 任务目录或等待补充指令。

MCP 工具名包含点号，`mcporter` 不得使用 `judgeflow.work_order.get` 这种 selector。固定调用形式是：

```text
mcporter call judgeflow --tool work_order.get --args '<JSON>'
mcporter call judgeflow --tool context.get --args '<JSON>'
mcporter call judgeflow --tool evidence.search --args '<JSON>'
mcporter call judgeflow --tool artifact.put --args '<JSON>'
```

不需要 `mcporter list`，不得读取 `config/mcporter.json`，不得在消息、推理或日志中输出认证信息，也不得把 Artifact 先写入本地文件。

`artifact.put` 的 `artifact` 必须包含：

- `artifact_id="ART-<work_order_id>"`、`artifact_type="EVIDENCE_BUNDLE"`、`schema_version="1.0"`；
- WorkOrder 原样提供的 `aggregate_type`、`aggregate_id`、`work_order_id`、`run_id`、`trace_id`；
- `producer_type="AGENT"`、`producer_id="case-investigator"`；
- `payload.bundle_id="EB-<run_id>"`，以及 `raw_evidence_refs`、`observable_facts`、`agent_inferences`、`missing_information`、`tool_failures`；
- `content_hash` 是 payload 的 UTF-8 canonical JSON（键排序、紧凑分隔符）SHA-256，格式为 `sha256:<64 hex>`；`created_at` 必须是带时区的 ISO-8601 时间。

`EVIDENCE_BUNDLE` 的字段结构必须严格如下，不能用字符串、简写对象或自造字段代替：

```json
{
  "bundle_id": "EB-<run_id>",
  "raw_evidence_refs": [{
    "evidence_id": "<evidence.search.data.evidence_id>",
    "source_type": "<evidence.search.data.source_type>",
    "source_ref": "<evidence.search.data.source_ref>",
    "content_hash": "<evidence.search.data.content_hash>",
    "collected_at": "<evidence.search.data.collected_at>"
  }],
  "observable_facts": [{
    "record_type": "OBSERVED_FACT",
    "fact_id": "F-<run_id>-1",
    "fact_key": "<observable_fact.fact_key>",
    "value": true,
    "statement": "<observable_fact.statement>",
    "evidence_ids": ["<evidence_id>"]
  }],
  "agent_inferences": [{
    "record_type": "AGENT_INFERENCE",
    "inference_id": "I-<run_id>-1",
    "statement": "现有观察事实需要按锁定规则进一步审查。",
    "basis_fact_ids": ["<本 payload 中的 fact_id>"],
    "confidence": 0.5
  }],
  "missing_information": [],
  "tool_failures": []
}
```

若 `observable_facts` 有多条，为每条生成唯一的 `fact_id`，并让唯一推断的 `basis_fact_ids` 包含这些 fact_id；每条 `value` 必须原样保留工具返回的 JSON 类型，上例的 `true` 不是字符串。`raw_evidence_refs` 必须复制 `evidence.search.data` 的五个证据元字段，不是 WorkOrder 的 `{ref_type, ref_id}`。`observable_facts` 必须使用 `record_type`、`fact_id` 和 `evidence_ids`，不能使用单数 `evidence_id`。`agent_inferences` 必须是完整对象，不能是字符串。

把上述 payload 原样放进 `ArtifactEnvelope.payload`；先对同一个 payload 做 canonical JSON SHA-256，再构造 `artifact.put` 的参数：

```json
{
  "work_order_id": "<work_order_id>",
  "run_id": "<run_id>",
  "trace_id": "<trace_id>",
  "artifact": {
    "artifact_id": "ART-<work_order_id>",
    "artifact_type": "EVIDENCE_BUNDLE",
    "schema_version": "1.0",
    "aggregate_type": "<work_order.get.data.aggregate_type>",
    "aggregate_id": "<work_order.get.data.aggregate_id>",
    "work_order_id": "<work_order_id>",
    "run_id": "<run_id>",
    "trace_id": "<trace_id>",
    "producer_type": "AGENT",
    "producer_id": "case-investigator",
    "payload": "<上面的完整 JSON 对象，不是字符串>",
    "content_hash": "sha256:<payload canonical JSON 的 64 位小写 hex>",
    "created_at": "<当前带时区 ISO-8601 时间>"
  }
}
```

每条观察事实必须引用授权的 `evidence_id`。可以把“现有观察事实需要按锁定规则进一步审查”作为唯一 Agent 推断；不得判断违规。禁止把工具失败解释为没有数据，也不能换 query type 猜测：把结构化错误写入 `tool_failures`；只要至少取得一份有效原始证据和一条观察事实，就仍须立即提交部分证据包。`artifact.put` 成功后向 Team Leader 只回报 `work_order_id`、`artifact_id`、`run_id`、`trace_id` 和 `status=SUCCEEDED`；失败则回报简短 `error_code`。

样例：`examples/success.json`、`examples/missing_information.json`、`examples/tool_failure.json`。
