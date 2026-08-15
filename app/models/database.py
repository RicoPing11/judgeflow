"""SQLAlchemy 2.x persistence model for the fixed JudgeFlow demo flow."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class CaseStatus(StrEnum):
    NEW = "NEW"
    INVESTIGATING = "INVESTIGATING"
    ARGUING = "ARGUING"
    ADJUDICATING = "ADJUDICATING"
    DECIDED = "DECIDED"


class AppealStatus(StrEnum):
    NEW = "NEW"
    REVIEWING = "REVIEWING"
    DECIDED = "DECIDED"


class EvolutionStatus(StrEnum):
    NEW = "NEW"
    ATTRIBUTING = "ATTRIBUTING"
    DRAFTING = "DRAFTING"
    REPLAYING = "REPLAYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class WorkOrderStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AggregateKind(StrEnum):
    CASE = "CASE"
    APPEAL = "APPEAL"
    POLICY_EVOLUTION = "POLICY_EVOLUTION"


class WorkStepType(StrEnum):
    INVESTIGATION = "INVESTIGATION"
    RISK_ARGUMENT = "RISK_ARGUMENT"
    COUNTER_ARGUMENT = "COUNTER_ARGUMENT"
    ADJUDICATION = "ADJUDICATION"
    APPEAL_REVIEW = "APPEAL_REVIEW"
    ATTRIBUTION = "ATTRIBUTION"
    POLICY_DRAFTING = "POLICY_DRAFTING"
    REPLAY = "REPLAY"


class ArtifactKind(StrEnum):
    EVIDENCE_BUNDLE = "EVIDENCE_BUNDLE"
    RISK_ARGUMENT = "RISK_ARGUMENT"
    COUNTER_ARGUMENT = "COUNTER_ARGUMENT"
    DECISION_RECORD = "DECISION_RECORD"
    APPEAL_DECISION = "APPEAL_DECISION"
    ATTRIBUTION_REPORT = "ATTRIBUTION_REPORT"
    POLICY_PROPOSAL = "POLICY_PROPOSAL"
    REPLAY_REPORT = "REPLAY_REPORT"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"


class PolicyStatus(StrEnum):
    BASELINE = "BASELINE"
    CANDIDATE = "CANDIDATE"


class ProducerType(StrEnum):
    AGENT = "AGENT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class ReplayRecommendation(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class EventType(StrEnum):
    STATE_TRANSITION = "STATE_TRANSITION"
    ARTIFACT_ACCEPTED = "ARTIFACT_ACCEPTED"
    WORK_ORDER_FAILED = "WORK_ORDER_FAILED"
    WORK_ORDER_RETRIED = "WORK_ORDER_RETRIED"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"
    MCP_CALL = "MCP_CALL"


class DomainState(StrEnum):
    NEW = "NEW"
    INVESTIGATING = "INVESTIGATING"
    ARGUING = "ARGUING"
    ADJUDICATING = "ADJUDICATING"
    DECIDED = "DECIDED"
    REVIEWING = "REVIEWING"
    ATTRIBUTING = "ATTRIBUTING"
    DRAFTING = "DRAFTING"
    REPLAYING = "REPLAYING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


def _enum(enum_type: type[StrEnum], name: str) -> Enum:
    return Enum(enum_type, name=name, native_enum=True, validate_strings=True)


class Base(DeclarativeBase):
    pass


class Case(Base):
    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    demo_run_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus, "case_status"), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_snapshot_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("state_version >= 0", name="ck_cases_state_version"),)
    __mapper_args__ = {"version_id_col": state_version, "version_id_generator": False}


class Appeal(Base):
    __tablename__ = "appeals"

    appeal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), nullable=False)
    status: Mapped[AppealStatus] = mapped_column(_enum(AppealStatus, "appeal_status"), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    original_case_snapshot_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    original_case_snapshot_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    allowed_evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("state_version >= 0", name="ck_appeals_state_version"),)
    __mapper_args__ = {"version_id_col": state_version, "version_id_generator": False}


class PolicyEvolution(Base):
    __tablename__ = "policy_evolutions"

    evolution_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_appeal_id: Mapped[str] = mapped_column(ForeignKey("appeals.appeal_id"), nullable=False)
    status: Mapped[EvolutionStatus] = mapped_column(_enum(EvolutionStatus, "evolution_status"), nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    base_policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    base_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    current_proposal_artifact_id: Mapped[str | None] = mapped_column(String(128))
    current_replay_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (CheckConstraint("state_version >= 0", name="ck_policy_evolutions_state_version"),)
    __mapper_args__ = {"version_id_col": state_version, "version_id_generator": False}


class WorkOrderRow(Base):
    __tablename__ = "work_orders"

    work_order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    aggregate_type: Mapped[AggregateKind] = mapped_column(_enum(AggregateKind, "aggregate_type"), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    step_type: Mapped[WorkStepType] = mapped_column(_enum(WorkStepType, "work_step_type"), nullable=False)
    assignee: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_artifact_type: Mapped[ArtifactKind] = mapped_column(_enum(ArtifactKind, "artifact_type"), nullable=False)
    input_refs: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[WorkOrderStatus] = mapped_column(_enum(WorkOrderStatus, "work_order_status"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("attempt >= 1", name="ck_work_orders_attempt"),
    )


class Artifact(Base):
    __tablename__ = "artifacts"

    artifact_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    aggregate_type: Mapped[AggregateKind] = mapped_column(_enum(AggregateKind, "aggregate_type"), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    work_order_id: Mapped[str] = mapped_column(ForeignKey("work_orders.work_order_id"), nullable=False)
    run_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    producer_type: Mapped[ProducerType] = mapped_column(_enum(ProducerType, "producer_type"), nullable=False)
    producer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_type: Mapped[ArtifactKind] = mapped_column(_enum(ArtifactKind, "artifact_type"), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # This is the artifact.put idempotency key. A retry has another work order/run.
        UniqueConstraint("work_order_id", "run_id", "artifact_type", name="uq_artifacts_idempotency"),
    )


class PolicyRow(Base):
    __tablename__ = "policies"

    policy_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[PolicyStatus] = mapped_column(_enum(PolicyStatus, "policy_status"), nullable=False)
    title: Mapped[str] = mapped_column(String(2000), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    dsl_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    source_proposal_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.artifact_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReplayRun(Base):
    __tablename__ = "replay_runs"

    replay_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    evolution_id: Mapped[str] = mapped_column(ForeignKey("policy_evolutions.evolution_id"), nullable=False)
    proposal_artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.artifact_id"), nullable=False)
    baseline_policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    baseline_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    case_results_json: Mapped[list] = mapped_column(JSONB, nullable=False)
    recommendation: Mapped[ReplayRecommendation] = mapped_column(
        _enum(ReplayRecommendation, "replay_recommendation"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["baseline_policy_id", "baseline_policy_version"], ["policies.policy_id", "policies.version"]
        ),
        ForeignKeyConstraint(
            ["candidate_policy_id", "candidate_policy_version"], ["policies.policy_id", "policies.version"]
        ),
    )


class DomainEvent(Base):
    __tablename__ = "domain_events"

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[EventType] = mapped_column(_enum(EventType, "event_type"), nullable=False)
    aggregate_type: Mapped[AggregateKind] = mapped_column(_enum(AggregateKind, "aggregate_type"), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_state: Mapped[DomainState | None] = mapped_column(_enum(DomainState, "domain_state"))
    to_state: Mapped[DomainState | None] = mapped_column(_enum(DomainState, "domain_state"))
    work_order_id: Mapped[str | None] = mapped_column(ForeignKey("work_orders.work_order_id"))
    run_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

IMMUTABLE_MODELS = (Artifact, PolicyRow, ReplayRun, DomainEvent)


@event.listens_for(Session, "before_flush")
def reject_immutable_changes(session: Session, *_: object) -> None:
    """Prevent ORM updates/deletes of append-only records."""

    for instance in session.dirty.union(session.deleted):
        if isinstance(instance, IMMUTABLE_MODELS):
            raise ValueError(f"{instance.__class__.__name__} records are immutable")
        if isinstance(instance, Case):
            if instance in session.deleted:
                raise ValueError("case policy snapshot is immutable")
            state = inspect(instance)
            if state.attrs.policy_snapshot_json.history.has_changes() or state.attrs.policy_snapshot_hash.history.has_changes():
                raise ValueError("case policy snapshot is immutable")
        if isinstance(instance, Appeal):
            if instance in session.deleted:
                raise ValueError("appeal case snapshot and evidence whitelist are immutable")
            state = inspect(instance)
            if (
                state.attrs.original_case_snapshot_json.history.has_changes()
                or state.attrs.original_case_snapshot_hash.history.has_changes()
                or state.attrs.allowed_evidence_ids.history.has_changes()
            ):
                raise ValueError("appeal case snapshot and evidence whitelist are immutable")


@event.listens_for(Session, "do_orm_execute")
def reject_bulk_immutable_changes(execute_state: object) -> None:
    """Apply the same append-only rule to SQLAlchemy bulk UPDATE/DELETE calls."""

    if not (execute_state.is_update or execute_state.is_delete):
        return
    table = getattr(execute_state.statement, "table", None)
    if table is not None and table.name in {"artifacts", "policies", "replay_runs", "domain_events"}:
        raise ValueError(f"{table.name} records are immutable")
    values = getattr(execute_state.statement, "_values", {})
    changed_columns = {getattr(column, "key", str(column)) for column in values}
    if table is not None and table.name == "cases" and changed_columns & {
        "policy_snapshot_json",
        "policy_snapshot_hash",
    }:
        raise ValueError("case policy snapshot is immutable")
    if execute_state.is_delete and table is not None and table.name == "cases":
        raise ValueError("case policy snapshot is immutable")
    if table is not None and table.name == "appeals" and changed_columns & {
        "original_case_snapshot_json",
        "original_case_snapshot_hash",
        "allowed_evidence_ids",
    }:
        raise ValueError("appeal case snapshot and evidence whitelist are immutable")
    if execute_state.is_delete and table is not None and table.name == "appeals":
        raise ValueError("appeal case snapshot and evidence whitelist are immutable")
