"""Pydantic v2 contracts for stage 1 of the JudgeFlow competition demo."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _require_timezone(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value


class ArtifactType(StrEnum):
    EVIDENCE_BUNDLE = "EVIDENCE_BUNDLE"
    RISK_ARGUMENT = "RISK_ARGUMENT"
    COUNTER_ARGUMENT = "COUNTER_ARGUMENT"
    DECISION_RECORD = "DECISION_RECORD"
    APPEAL_DECISION = "APPEAL_DECISION"
    ATTRIBUTION_REPORT = "ATTRIBUTION_REPORT"
    POLICY_PROPOSAL = "POLICY_PROPOSAL"
    REPLAY_REPORT = "REPLAY_REPORT"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"


class AggregateType(StrEnum):
    CASE = "CASE"
    APPEAL = "APPEAL"
    POLICY_EVOLUTION = "POLICY_EVOLUTION"


class WorkStep(StrEnum):
    INVESTIGATION = "INVESTIGATION"
    RISK_ARGUMENT = "RISK_ARGUMENT"
    COUNTER_ARGUMENT = "COUNTER_ARGUMENT"
    ADJUDICATION = "ADJUDICATION"
    APPEAL_REVIEW = "APPEAL_REVIEW"
    ATTRIBUTION = "ATTRIBUTION"
    POLICY_DRAFTING = "POLICY_DRAFTING"
    REPLAY = "REPLAY"


class InputRef(StrictModel):
    ref_type: Literal["CASE", "APPEAL", "EVIDENCE", "ARTIFACT", "POLICY_SNAPSHOT", "REPLAY_DATASET"]
    ref_id: Identifier


class WorkOrder(StrictModel):
    work_order_id: Identifier
    aggregate_type: AggregateType
    aggregate_id: Identifier
    step_type: WorkStep
    assignee_id: Identifier
    input_refs: list[InputRef] = Field(min_length=1)
    expected_artifact_type: ArtifactType
    run_id: Identifier
    trace_id: Identifier
    status: Literal["PENDING", "RUNNING", "SUCCEEDED", "FAILED"]
    retry_count: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime

    _created_at_tz = field_validator("created_at")(_require_timezone)
    _updated_at_tz = field_validator("updated_at")(_require_timezone)


class EvidenceRef(StrictModel):
    evidence_id: Identifier
    source_type: Literal["CONTENT", "TRANSCRIPT", "ACCOUNT", "RELATIONSHIP", "APPEAL_SUBMISSION"]
    source_ref: Identifier
    content_hash: Sha256
    collected_at: datetime

    _collected_at_tz = field_validator("collected_at")(_require_timezone)


FactValue = bool | int | float | str | list[str]


class FactRecord(StrictModel):
    record_type: Literal["OBSERVED_FACT"]
    fact_id: Identifier
    fact_key: Identifier
    value: FactValue
    statement: NonEmptyText
    evidence_ids: list[Identifier] = Field(min_length=1)


class InferenceRecord(StrictModel):
    record_type: Literal["AGENT_INFERENCE"]
    inference_id: Identifier
    statement: NonEmptyText
    basis_fact_ids: list[Identifier] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ToolFailure(StrictModel):
    failure_id: Identifier
    tool_name: Identifier
    error_code: Literal["NOT_FOUND", "INCOMPLETE_DATA", "PERMISSION_DENIED", "VALIDATION_ERROR", "TIMEOUT", "CONFLICT"]
    message: NonEmptyText
    retryable: bool
    occurred_at: datetime

    _occurred_at_tz = field_validator("occurred_at")(_require_timezone)


class EvidenceBundle(StrictModel):
    bundle_id: Identifier
    raw_evidence_refs: list[EvidenceRef] = Field(min_length=1)
    observable_facts: list[FactRecord]
    agent_inferences: list[InferenceRecord]
    missing_information: list[NonEmptyText]
    tool_failures: list[ToolFailure]

    @model_validator(mode="after")
    def validate_internal_references(self) -> EvidenceBundle:
        evidence_ids = {item.evidence_id for item in self.raw_evidence_refs}
        fact_ids = {item.fact_id for item in self.observable_facts}
        for fact in self.observable_facts:
            if not set(fact.evidence_ids) <= evidence_ids:
                raise ValueError("fact references evidence outside raw_evidence_refs")
        for inference in self.agent_inferences:
            if not set(inference.basis_fact_ids) <= fact_ids:
                raise ValueError("inference references facts outside observable_facts")
        return self


class PolicyReference(StrictModel):
    policy_id: Identifier
    version: Identifier
    clause_ids: list[Identifier] = Field(min_length=1)


class FormalConclusion(StrictModel):
    conclusion_id: Identifier
    statement: NonEmptyText
    evidence_id: Identifier
    policy_ref: PolicyReference


class RiskArgument(StrictModel):
    argument_id: Identifier
    conclusions: list[FormalConclusion] = Field(min_length=1)
    uncertainties: list[NonEmptyText]


class CounterArgument(StrictModel):
    argument_id: Identifier
    conclusions: list[FormalConclusion] = Field(min_length=1)
    alternative_explanations: list[NonEmptyText]


class DecisionRecord(StrictModel):
    decision_id: Identifier
    outcome: Literal["VIOLATION", "NO_VIOLATION", "INSUFFICIENT_EVIDENCE"]
    conclusions: list[FormalConclusion] = Field(min_length=1)
    considered_argument_ids: list[Identifier] = Field(min_length=2)


class AppealDecision(StrictModel):
    appeal_decision_id: Identifier
    outcome: Literal["UPHOLD", "OVERTURN", "INSUFFICIENT_EVIDENCE"]
    conclusions: list[FormalConclusion] = Field(min_length=1)
    original_decision_id: Identifier
    new_evidence_ids: list[Identifier] = Field(min_length=1)


class AttributionReport(StrictModel):
    report_id: Identifier
    attribution: Literal["EVIDENCE_GAP", "AGENT_ERROR", "POLICY_GAP", "POLICY_CONFLICT"]
    findings: list[NonEmptyText] = Field(min_length=1)
    appeal_decision_id: Identifier
    policy_change_recommended: bool

    @model_validator(mode="after")
    def policy_recommendation_matches_attribution(self) -> AttributionReport:
        policy_issue = self.attribution in {"POLICY_GAP", "POLICY_CONFLICT"}
        if self.policy_change_recommended != policy_issue:
            raise ValueError("policy change recommendation must match attribution")
        return self


class PolicyOperator(StrEnum):
    EQ = "EQ"
    IN = "IN"
    GTE = "GTE"
    LTE = "LTE"


class PolicyCondition(StrictModel):
    condition_id: Identifier | None = None
    fact: Identifier | None = None
    op: PolicyOperator | None = None
    value: bool | int | float | str | list[bool | int | float | str] | None = None
    all_of: list[PolicyCondition] | None = Field(default=None, min_length=1)
    any_of: list[PolicyCondition] | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_condition_form(self) -> PolicyCondition:
        is_atomic = self.fact is not None or self.op is not None or self.value is not None
        groups = int(self.all_of is not None) + int(self.any_of is not None)
        if is_atomic:
            if groups or self.condition_id is None or self.fact is None or self.op is None or self.value is None:
                raise ValueError("atomic condition requires condition_id, fact, op, and value only")
            if self.op == PolicyOperator.IN and not isinstance(self.value, list):
                raise ValueError("IN condition value must be a list")
            if self.op in {PolicyOperator.GTE, PolicyOperator.LTE} and (
                isinstance(self.value, bool) or not isinstance(self.value, (int, float))
            ):
                raise ValueError("GTE and LTE condition values must be numeric")
        elif groups != 1:
            raise ValueError("group condition requires exactly one of all_of or any_of")
        return self


class PolicyApplicability(StrictModel):
    content_types: list[Literal["VIDEO", "IMAGE", "TEXT", "AUDIO"]] = Field(min_length=1)


class Policy(StrictModel):
    policy_id: Identifier
    version: Identifier
    status: Literal["BASELINE", "CANDIDATE"]
    title: NonEmptyText
    description: NonEmptyText
    applicability: PolicyApplicability
    required_elements: PolicyCondition
    exceptions: PolicyCondition
    decision: Literal["VIOLATION", "NO_VIOLATION"]
    content_hash: Sha256


class LockedPolicy(StrictModel):
    policy_id: Identifier
    version: Identifier
    content_hash: Sha256


class PolicySnapshot(StrictModel):
    snapshot_id: Identifier
    policies: list[LockedPolicy] = Field(min_length=1)
    snapshot_hash: Sha256


class PolicyChange(StrictModel):
    path: Identifier
    operation: Literal["ADD", "REMOVE", "REPLACE"]
    rationale: NonEmptyText


class PolicyProposal(StrictModel):
    proposal_id: Identifier
    proposal_revision: int = Field(ge=1)
    base_policy: LockedPolicy
    candidate_policy: Policy
    changes: list[PolicyChange] = Field(min_length=1)
    reason: NonEmptyText
    risks: list[NonEmptyText]

    @model_validator(mode="after")
    def candidate_is_new_version(self) -> PolicyProposal:
        if self.candidate_policy.policy_id != self.base_policy.policy_id:
            raise ValueError("candidate and base policy IDs must match")
        if self.candidate_policy.version == self.base_policy.version:
            raise ValueError("candidate policy must use a new version")
        if self.candidate_policy.status != "CANDIDATE":
            raise ValueError("proposed policy must have CANDIDATE status")
        return self


class ReplayMetrics(StrictModel):
    baseline_false_positives: int = Field(ge=0)
    candidate_false_positives: int = Field(ge=0)
    baseline_false_negatives: int = Field(ge=0)
    candidate_false_negatives: int = Field(ge=0)
    changed_cases: int = Field(ge=0)


class ReplayReport(StrictModel):
    replay_id: Identifier
    proposal_id: Identifier
    baseline_policy: LockedPolicy
    candidate_policy: LockedPolicy
    dataset_version: Identifier
    dataset_manifest_hash: Sha256
    metrics: ReplayMetrics
    recommendation: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    result_summary: NonEmptyText


class HumanApproval(StrictModel):
    approval_id: Identifier
    decision: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]
    proposal_artifact_id: Identifier
    replay_id: Identifier
    reviewer_id: Identifier
    comment: NonEmptyText
    created_at: datetime

    _created_at_tz = field_validator("created_at")(_require_timezone)


ArtifactPayload = (
    EvidenceBundle
    | RiskArgument
    | CounterArgument
    | DecisionRecord
    | AppealDecision
    | AttributionReport
    | PolicyProposal
    | ReplayReport
    | HumanApproval
)

_PAYLOAD_MODELS: dict[ArtifactType, type[StrictModel]] = {
    ArtifactType.EVIDENCE_BUNDLE: EvidenceBundle,
    ArtifactType.RISK_ARGUMENT: RiskArgument,
    ArtifactType.COUNTER_ARGUMENT: CounterArgument,
    ArtifactType.DECISION_RECORD: DecisionRecord,
    ArtifactType.APPEAL_DECISION: AppealDecision,
    ArtifactType.ATTRIBUTION_REPORT: AttributionReport,
    ArtifactType.POLICY_PROPOSAL: PolicyProposal,
    ArtifactType.REPLAY_REPORT: ReplayReport,
    ArtifactType.HUMAN_APPROVAL: HumanApproval,
}


class ArtifactEnvelope(StrictModel):
    artifact_id: Identifier
    artifact_type: ArtifactType
    schema_version: Literal["1.0"]
    aggregate_type: AggregateType
    aggregate_id: Identifier
    work_order_id: Identifier
    run_id: Identifier
    trace_id: Identifier
    producer_type: Literal["AGENT", "HUMAN", "SYSTEM"]
    producer_id: Identifier
    payload: ArtifactPayload
    content_hash: Sha256
    created_at: datetime

    _created_at_tz = field_validator("created_at")(_require_timezone)

    @model_validator(mode="before")
    @classmethod
    def parse_payload_for_declared_type(cls, data: Any) -> Any:
        if not isinstance(data, dict) or "artifact_type" not in data or "payload" not in data:
            return data
        try:
            artifact_type = ArtifactType(data["artifact_type"])
        except ValueError:
            return data
        expected_model = _PAYLOAD_MODELS[artifact_type]
        copied = dict(data)
        copied["payload"] = expected_model.model_validate(data["payload"])
        return copied

    @model_validator(mode="after")
    def payload_matches_artifact_type(self) -> ArtifactEnvelope:
        expected_model = _PAYLOAD_MODELS[self.artifact_type]
        if type(self.payload) is not expected_model:
            raise ValueError("artifact_type does not match payload type")
        return self


PolicyCondition.model_rebuild()
