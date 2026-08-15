"""Minimal immutable identities and permissions for the eight domain agents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    responsibility: str
    skill: str
    skill_mode: str | None
    step_type: str
    output_type: str
    allowed_tools: tuple[str, ...]
    prohibited: tuple[str, ...]


_COMMON = ("work_order.get", "context.get", "artifact.put")

AGENT_SPECS: dict[str, AgentSpec] = {
    "case-investigator": AgentSpec(
        "case-investigator", "整理授权案件材料，分离事实、推断、缺失信息和工具失败",
        "case-investigation", None, "INVESTIGATION", "EVIDENCE_BUNDLE",
        ("work_order.get", "context.get", "evidence.search", "artifact.put"),
        ("判断是否违规", "查询 WorkOrder 未授权证据", "把推断写成观察事实"),
    ),
    "risk-prosecutor": AgentSpec(
        "risk-prosecutor", "把已验收证据映射到锁定规则的风险成立要件",
        "case-deliberation", "RISK", "RISK_ARGUMENT", "RISK_ARGUMENT", _COMMON,
        ("读取或改写反证意见", "使用未锁定规则", "引用未授权证据"),
    ),
    "counter-reviewer": AgentSpec(
        "counter-reviewer", "独立检查例外、矛盾和替代解释",
        "case-deliberation", "COUNTER", "COUNTER_ARGUMENT", "COUNTER_ARGUMENT", _COMMON,
        ("读取或改写风险意见", "使用未锁定规则", "引用未授权证据"),
    ),
    "independent-judge": AgentSpec(
        "independent-judge", "仅在正反意见均已验收后依锁定规则独立裁决",
        "case-deliberation", "DECISION", "ADJUDICATION", "DECISION_RECORD", _COMMON,
        ("修改证据", "修改任一方意见", "读取自由文本或内部对话"),
    ),
    "appeal-reviewer": AgentSpec(
        "appeal-reviewer", "仅用冻结原案快照、其中锁定规则和新增证据白名单独立重审",
        "case-deliberation", "APPEAL", "APPEAL_REVIEW", "APPEAL_DECISION", _COMMON,
        ("读取原裁决 Team 对话", "读取实时案件或新版规则", "读取白名单外证据"),
    ),
    "case-attributor": AgentSpec(
        "case-attributor", "区分证据、Agent 与规则原因，并允许关闭规则演进",
        "policy-evolution", "ATTRIBUTION", "ATTRIBUTION", "ATTRIBUTION_REPORT", _COMMON,
        ("为进入规则编写而强行归因", "读取 SCORING", "修改既有规则"),
    ),
    "policy-author": AgentSpec(
        "policy-author", "仅在规则缺口或冲突成立时生成有限 DSL 候选版本",
        "policy-evolution", "DRAFTING", "POLICY_DRAFTING", "POLICY_PROPOSAL", _COMMON,
        ("读取 SCORING", "覆盖或批准旧规则", "发布生产规则"),
    ),
    "replay-analyst": AgentSpec(
        "replay-analyst", "核对确定性回放绑定并解释 Python 回放结果",
        "case-replay", None, "REPLAY", "REPLAY_REPORT",
        ("work_order.get", "context.get", "replay.execute", "artifact.put"),
        ("自行计算或修改指标", "修改标签或逐案结果", "批准或发布规则"),
    ),
}
