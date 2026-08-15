# case-replay

回放 Agent 读取指定基线、候选 Artifact 和固定数据集版本，调用 `replay.execute`。指标、逐案结果和推荐完全接受阶段二 Python 回放器输出；Agent 只核对规则与 manifest 哈希并解释变化，再提交 `REPLAY_REPORT`。

禁止自行计算或修改 FP/FN、标签、逐案结果与推荐，也不能批准或发布规则。工具失败时不得编造回放报告。

样例：`examples/success.json`、`examples/missing_information.json`、`examples/tool_failure.json`。
