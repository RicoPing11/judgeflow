"""Strict ID-only Matrix protocol for the fixed stage-five demo."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


MessageType = Literal[
    "WORK_ORDER_ASSIGNED",
    "ARTIFACT_ACCEPTED",
    "WORK_ORDER_FAILED",
    "HUMAN_APPROVAL_REQUIRED",
]
AggregateType = Literal["CASE", "APPEAL", "POLICY_EVOLUTION"]
MessageStatus = Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED", "AWAITING_APPROVAL"]


class MatrixIDMessage(BaseModel):
    """The complete business message surface allowed in Matrix rooms."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    message_type: MessageType
    work_order_id: str
    aggregate_type: AggregateType
    aggregate_id: str
    step_type: str
    assignee: str
    run_id: str
    trace_id: str
    artifact_id: str | None = None
    status: MessageStatus
    error_code: str | None = None
    proposal_artifact_id: str | None = None
    replay_id: str | None = None

    @model_validator(mode="after")
    def validate_conditional_fields(self) -> "MatrixIDMessage":
        human_fields = self.proposal_artifact_id is not None or self.replay_id is not None
        if self.message_type == "HUMAN_APPROVAL_REQUIRED":
            if self.assignee != "policy-owner":
                raise ValueError("Human reminder must be assigned to policy-owner")
            if not self.proposal_artifact_id or not self.replay_id:
                raise ValueError("Human reminder must bind proposal_artifact_id and replay_id")
            if self.status != "AWAITING_APPROVAL":
                raise ValueError("Human reminder must use AWAITING_APPROVAL status")
        elif human_fields:
            raise ValueError("proposal_artifact_id and replay_id are Human-reminder-only")
        if self.status == "FAILED" and not self.error_code:
            raise ValueError("failed message requires a short error_code")
        if self.status != "FAILED" and self.error_code is not None:
            raise ValueError("error_code is failure-only")
        if self.message_type == "ARTIFACT_ACCEPTED" and not self.artifact_id:
            raise ValueError("accepted artifact message requires artifact_id")
        return self


@dataclass(frozen=True)
class CoordinatorSpec:
    resource_id: str
    role: Literal["MANAGER", "TEAM_LEADER"]
    allowed: tuple[str, ...]
    prohibited: tuple[str, ...]


COORDINATOR_SPECS = {
    "judgeflow-manager": CoordinatorSpec(
        "judgeflow-manager",
        "MANAGER",
        ("接收已创建的 WorkOrder ID", "按 aggregate 和 step 路由", "只传 ID、状态和失败摘要"),
        ("生成领域 Artifact", "修改 WorkOrder", "访问数据库", "推进业务状态", "使用 Matrix 自由文本作为业务输入"),
    ),
    "adjudication-team-leader": CoordinatorSpec(
        "adjudication-team-leader",
        "TEAM_LEADER",
        ("按后端已验收 Artifact ID 唤醒裁决链路 Agent", "检查所需 Artifact ID 是否齐全"),
        ("生成领域 Artifact", "替领域 Agent 判断", "读取完整 Artifact payload", "访问数据库", "推进业务状态"),
    ),
    "policy-evolution-team-leader": CoordinatorSpec(
        "policy-evolution-team-leader",
        "TEAM_LEADER",
        ("按归因结果路由规则演进", "回放验收后提醒 policy-owner"),
        ("强迫规则归因", "生成领域 Artifact", "读取 SCORING", "批准或发布规则", "访问数据库", "推进业务状态"),
    ),
}


BUSINESS_ROOMS = {
    "judgeflow-control": (
        "judgeflow-manager",
        "adjudication-team-leader",
        "policy-evolution-team-leader",
        "appeal-reviewer",
        "demo-admin",
    ),
    "judgeflow-adjudication": (
        "adjudication-team-leader",
        "case-investigator",
        "risk-prosecutor",
        "counter-reviewer",
        "independent-judge",
    ),
    "judgeflow-policy": (
        "policy-evolution-team-leader",
        "case-attributor",
        "policy-author",
        "replay-analyst",
        "policy-owner",
    ),
}


def route_target(message: MatrixIDMessage) -> str:
    """Return the only native AgentTeams recipient for a backend-issued task."""

    if message.aggregate_type == "APPEAL":
        return "appeal-reviewer"
    if message.aggregate_type == "CASE":
        return "adjudication-team-leader"
    return "policy-evolution-team-leader"
