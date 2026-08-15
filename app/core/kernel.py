"""The fixed JudgeFlow demo business flow, without HTTP, MCP, or agents."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.database import (
    Appeal,
    AppealStatus,
    Artifact,
    Case,
    CaseStatus,
    DomainEvent,
    EventType,
    EvolutionStatus,
    PolicyEvolution,
    PolicyRow,
    ReplayRun,
    WorkOrderRow,
    WorkOrderStatus,
)
from app.models.schemas import (
    ArtifactEnvelope,
    ArtifactType,
    AttributionReport,
    HumanApproval,
    Policy,
    PolicyProposal,
    PolicySnapshot,
    ReplayReport,
    WorkOrder,
)
from app.core.hashing import canonical_hash, policy_hash_value, snapshot_hash_value
from app.replay.executor import replay_policies


class StateTransitionError(ValueError):
    pass


class ConflictError(ValueError):
    pass


class ValidationError(ValueError):
    pass


CASE_TRANSITIONS = {
    CaseStatus.NEW: CaseStatus.INVESTIGATING,
    CaseStatus.INVESTIGATING: CaseStatus.ARGUING,
    CaseStatus.ARGUING: CaseStatus.ADJUDICATING,
    CaseStatus.ADJUDICATING: CaseStatus.DECIDED,
}
APPEAL_TRANSITIONS = {
    AppealStatus.NEW: AppealStatus.REVIEWING,
    AppealStatus.REVIEWING: AppealStatus.DECIDED,
}
EVOLUTION_TRANSITIONS: dict[EvolutionStatus, set[EvolutionStatus]] = {
    EvolutionStatus.NEW: {EvolutionStatus.ATTRIBUTING},
    EvolutionStatus.ATTRIBUTING: {EvolutionStatus.DRAFTING, EvolutionStatus.CLOSED},
    EvolutionStatus.DRAFTING: {EvolutionStatus.REPLAYING},
    EvolutionStatus.REPLAYING: {EvolutionStatus.AWAITING_APPROVAL},
    EvolutionStatus.AWAITING_APPROVAL: {EvolutionStatus.APPROVED, EvolutionStatus.REJECTED},
}

STEP_ARTIFACTS = {
    "INVESTIGATION": {("CASE", "EVIDENCE_BUNDLE")},
    "RISK_ARGUMENT": {("CASE", "RISK_ARGUMENT")},
    "COUNTER_ARGUMENT": {("CASE", "COUNTER_ARGUMENT")},
    "ADJUDICATION": {("CASE", "DECISION_RECORD")},
    "APPEAL_REVIEW": {("APPEAL", "APPEAL_DECISION")},
    "ATTRIBUTION": {("POLICY_EVOLUTION", "ATTRIBUTION_REPORT")},
    "POLICY_DRAFTING": {("POLICY_EVOLUTION", "POLICY_PROPOSAL")},
    # Stage-one WorkStep deliberately has no APPROVAL. The existing REPLAY step is
    # the one minimal convention for both deterministic report and bound Human approval.
    "REPLAY": {("POLICY_EVOLUTION", "REPLAY_REPORT"), ("POLICY_EVOLUTION", "HUMAN_APPROVAL")},
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class JudgeFlowKernel:
    """Small transaction-oriented service for only the competition demo path."""

    def __init__(self, session: Session):
        self.session = session

    def add_policy(self, policy_data: dict[str, Any] | Policy, source_artifact_id: str | None = None) -> PolicyRow:
        policy = policy_data if isinstance(policy_data, Policy) else Policy.model_validate(policy_data)
        dumped_policy = policy.model_dump(mode="json", exclude_none=True)
        if policy.content_hash != policy_hash_value(dumped_policy):
            raise ValidationError("policy content hash mismatch")
        if self.session.get(PolicyRow, (policy.policy_id, policy.version)) is not None:
            raise ConflictError("policy version already exists and is immutable")
        if policy.status == "BASELINE" and source_artifact_id is not None:
            raise ValidationError("baseline policy cannot reference a proposal artifact")
        if policy.status == "CANDIDATE" and source_artifact_id is None:
            raise ValidationError("candidate policy must reference its proposal artifact")
        row = PolicyRow(
            policy_id=policy.policy_id,
            version=policy.version,
            status=policy.status,
            title=policy.title,
            description=policy.description,
            dsl_json=policy.model_dump(mode="json"),
            content_hash=policy.content_hash,
            source_proposal_artifact_id=source_artifact_id,
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_case(
        self,
        *,
        case_id: str,
        demo_run_id: str,
        input_json: dict[str, Any],
        policy_snapshot: dict[str, Any] | PolicySnapshot,
    ) -> Case:
        snapshot = (
            policy_snapshot
            if isinstance(policy_snapshot, PolicySnapshot)
            else PolicySnapshot.model_validate(policy_snapshot)
        )
        dumped_snapshot = snapshot.model_dump(mode="json")
        if snapshot.snapshot_hash != snapshot_hash_value(dumped_snapshot):
            raise ValidationError("policy snapshot hash mismatch")
        for locked in snapshot.policies:
            policy = self.session.get(PolicyRow, (locked.policy_id, locked.version))
            if policy is None or policy.content_hash != locked.content_hash:
                raise ValidationError("policy snapshot references a missing or mismatched immutable version")
        now = utcnow()
        row = Case(
            case_id=case_id,
            demo_run_id=demo_run_id,
            status=CaseStatus.NEW,
            state_version=0,
            input_json=input_json,
            policy_snapshot_json=dumped_snapshot,
            policy_snapshot_hash=snapshot.snapshot_hash,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_appeal(
        self,
        *,
        appeal_id: str,
        case_id: str,
        request_json: dict[str, Any],
        original_case_snapshot_json: dict[str, Any],
        allowed_evidence_ids: list[str],
    ) -> Appeal:
        case = self._required(Case, case_id)
        if case.status != CaseStatus.DECIDED:
            raise StateTransitionError("appeal requires a decided case")
        now = utcnow()
        row = Appeal(
            appeal_id=appeal_id,
            case_id=case_id,
            status=AppealStatus.NEW,
            state_version=0,
            request_json=request_json,
            original_case_snapshot_json=original_case_snapshot_json,
            original_case_snapshot_hash=canonical_hash(original_case_snapshot_json),
            allowed_evidence_ids=allowed_evidence_ids,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def create_policy_evolution(
        self,
        *,
        evolution_id: str,
        source_appeal_id: str,
        base_policy_id: str,
        base_policy_version: str,
    ) -> PolicyEvolution:
        appeal = self._required(Appeal, source_appeal_id)
        if appeal.status != AppealStatus.DECIDED:
            raise StateTransitionError("policy evolution requires a decided appeal")
        self._required(PolicyRow, (base_policy_id, base_policy_version))
        now = utcnow()
        row = PolicyEvolution(
            evolution_id=evolution_id,
            source_appeal_id=source_appeal_id,
            status=EvolutionStatus.NEW,
            state_version=0,
            base_policy_id=base_policy_id,
            base_policy_version=base_policy_version,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def advance_case(self, case_id: str, from_state: str, to_state: str, *, trace_id: str = "SYSTEM") -> Case:
        row = self._required(Case, case_id)
        current, target = CaseStatus(from_state), CaseStatus(to_state)
        if (
            row.status != current
            or (current, target) != (CaseStatus.NEW, CaseStatus.INVESTIGATING)
        ):
            raise StateTransitionError(f"illegal case transition {from_state} -> {to_state}")
        self._apply_transition(row, "CASE", case_id, current.value, target.value, trace_id)
        return row

    def advance_appeal(
        self, appeal_id: str, from_state: str, to_state: str, *, trace_id: str = "SYSTEM"
    ) -> Appeal:
        row = self._required(Appeal, appeal_id)
        current, target = AppealStatus(from_state), AppealStatus(to_state)
        if row.status != current or (current, target) != (AppealStatus.NEW, AppealStatus.REVIEWING):
            raise StateTransitionError(f"illegal appeal transition {from_state} -> {to_state}")
        self._apply_transition(row, "APPEAL", appeal_id, current.value, target.value, trace_id)
        return row

    def advance_policy_evolution(
        self, evolution_id: str, from_state: str, to_state: str, *, trace_id: str = "SYSTEM"
    ) -> PolicyEvolution:
        row = self._required(PolicyEvolution, evolution_id)
        current, target = EvolutionStatus(from_state), EvolutionStatus(to_state)
        if row.status != current or (current, target) != (EvolutionStatus.NEW, EvolutionStatus.ATTRIBUTING):
            raise StateTransitionError(f"illegal policy evolution transition {from_state} -> {to_state}")
        self._apply_transition(row, "POLICY_EVOLUTION", evolution_id, current.value, target.value, trace_id)
        return row

    def create_work_order(self, data: dict[str, Any] | WorkOrder) -> WorkOrderRow:
        work = data if isinstance(data, WorkOrder) else WorkOrder.model_validate(data)
        self._validate_work_order_aggregate(work.aggregate_type.value, work.aggregate_id)
        if work.status != "PENDING" or work.retry_count != 0:
            raise ValidationError("new work order must be PENDING with retry_count 0")
        if (work.aggregate_type.value, work.expected_artifact_type.value) not in STEP_ARTIFACTS[
            work.step_type.value
        ]:
            raise ValidationError("work order step, aggregate, and expected artifact type do not match")
        input_refs = [item.model_dump(mode="json") for item in work.input_refs]
        existing = self.session.get(WorkOrderRow, work.work_order_id)
        if existing is not None:
            same_request = (
                str(existing.aggregate_type) == work.aggregate_type.value
                and existing.aggregate_id == work.aggregate_id
                and str(existing.step_type) == work.step_type.value
                and existing.assignee == work.assignee_id
                and str(existing.expected_artifact_type) == work.expected_artifact_type.value
                and existing.input_refs == input_refs
                and existing.run_id == work.run_id
                and existing.trace_id == work.trace_id
            )
            if same_request:
                return existing
            raise ConflictError("work_order_id already belongs to another execution")
        run_owner = self.session.scalar(select(WorkOrderRow).where(WorkOrderRow.run_id == work.run_id))
        if run_owner is not None:
            raise ConflictError("run_id already belongs to another work order")
        row = WorkOrderRow(
            work_order_id=work.work_order_id,
            aggregate_type=work.aggregate_type.value,
            aggregate_id=work.aggregate_id,
            step_type=work.step_type.value,
            assignee=work.assignee_id,
            expected_artifact_type=work.expected_artifact_type.value,
            input_refs=input_refs,
            status=work.status,
            attempt=work.retry_count + 1,
            run_id=work.run_id,
            trace_id=work.trace_id,
            created_at=work.created_at,
            updated_at=work.updated_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def mark_work_order_running(self, work_order_id: str) -> WorkOrderRow:
        row = self._required(WorkOrderRow, work_order_id)
        if row.status != WorkOrderStatus.PENDING:
            raise StateTransitionError("only a pending work order can start")
        row.status = WorkOrderStatus.RUNNING
        row.updated_at = utcnow()
        self.session.flush()
        return row

    def fail_work_order(self, work_order_id: str, error_code: str) -> WorkOrderRow:
        row = self._required(WorkOrderRow, work_order_id)
        if row.status not in {WorkOrderStatus.PENDING, WorkOrderStatus.RUNNING}:
            raise StateTransitionError("only an active work order can fail")
        row.status = WorkOrderStatus.FAILED
        row.error_code = error_code
        row.updated_at = utcnow()
        self._append_event(
            EventType.WORK_ORDER_FAILED,
            row.aggregate_type,
            row.aggregate_id,
            row.trace_id,
            {"error_code": error_code},
            work_order_id=row.work_order_id,
            run_id=row.run_id,
        )
        self.session.flush()
        return row

    def retry_work_order(self, failed_id: str, *, work_order_id: str, run_id: str) -> WorkOrderRow:
        failed = self._required(WorkOrderRow, failed_id)
        if failed.status != WorkOrderStatus.FAILED:
            raise StateTransitionError("only a failed work order can be retried")
        if run_id == failed.run_id:
            raise ValidationError("retry must use a new run_id")
        now = utcnow()
        retry = WorkOrderRow(
            work_order_id=work_order_id,
            aggregate_type=failed.aggregate_type,
            aggregate_id=failed.aggregate_id,
            step_type=failed.step_type,
            assignee=failed.assignee,
            expected_artifact_type=failed.expected_artifact_type,
            input_refs=failed.input_refs,
            status=WorkOrderStatus.PENDING,
            attempt=failed.attempt + 1,
            run_id=run_id,
            trace_id=failed.trace_id,
            created_at=now,
            updated_at=now,
        )
        self.session.add(retry)
        self._append_event(
            EventType.WORK_ORDER_RETRIED,
            failed.aggregate_type,
            failed.aggregate_id,
            failed.trace_id,
            {"failed_work_order_id": failed_id, "retry_work_order_id": work_order_id},
            work_order_id=work_order_id,
            run_id=run_id,
        )
        self.session.flush()
        return retry

    def accept_artifact(self, data: dict[str, Any] | ArtifactEnvelope) -> Artifact:
        try:
            envelope = data if isinstance(data, ArtifactEnvelope) else ArtifactEnvelope.model_validate(data)
        except PydanticValidationError as exc:
            raise ValidationError("artifact failed Pydantic schema validation") from exc
        work = self._required(WorkOrderRow, envelope.work_order_id)
        payload_json = envelope.payload.model_dump(mode="json", exclude_none=True)
        if envelope.content_hash != canonical_hash(payload_json):
            raise ValidationError("artifact payload hash mismatch")
        existing = self.session.scalar(
            select(Artifact).where(
                Artifact.work_order_id == envelope.work_order_id,
                Artifact.run_id == envelope.run_id,
                Artifact.artifact_type == envelope.artifact_type.value,
            )
        )
        if existing is not None:
            if existing.content_hash == envelope.content_hash and existing.payload == payload_json:
                if str(existing.aggregate_type) == "CASE":
                    self._advance_after_artifact(work, existing, envelope.payload)
                    self.session.flush()
                return existing
            raise ConflictError("same idempotency key was submitted with different content")
        if self.session.get(Artifact, envelope.artifact_id) is not None:
            raise ConflictError("artifact_id already belongs to another immutable artifact")
        if work.status not in {WorkOrderStatus.PENDING, WorkOrderStatus.RUNNING}:
            raise StateTransitionError("work order is not accepting a new artifact")
        if (
            work.aggregate_type != envelope.aggregate_type.value
            or work.aggregate_id != envelope.aggregate_id
            or work.run_id != envelope.run_id
            or work.trace_id != envelope.trace_id
        ):
            raise ValidationError("artifact does not match its work order execution")
        if work.expected_artifact_type != envelope.artifact_type.value:
            raise ValidationError("artifact type does not match work order expectation")
        if envelope.producer_id != work.assignee:
            raise ValidationError("artifact producer is not the work order assignee")
        if envelope.artifact_type == ArtifactType.HUMAN_APPROVAL and envelope.producer_type != "HUMAN":
            raise ValidationError("human approval must be produced by a human")
        if envelope.artifact_type != ArtifactType.HUMAN_APPROVAL and envelope.producer_type != "AGENT":
            raise ValidationError("non-approval work order artifacts must be produced by an agent")
        artifact = Artifact(
            artifact_id=envelope.artifact_id,
            aggregate_type=envelope.aggregate_type.value,
            aggregate_id=envelope.aggregate_id,
            work_order_id=envelope.work_order_id,
            run_id=envelope.run_id,
            trace_id=envelope.trace_id,
            producer_type=envelope.producer_type,
            producer_id=envelope.producer_id,
            artifact_type=envelope.artifact_type.value,
            schema_version=envelope.schema_version,
            payload=payload_json,
            content_hash=envelope.content_hash,
            created_at=envelope.created_at,
        )
        self.session.add(artifact)
        work.status = WorkOrderStatus.SUCCEEDED
        work.error_code = None
        work.updated_at = utcnow()
        self.session.flush()
        self._append_event(
            EventType.ARTIFACT_ACCEPTED,
            work.aggregate_type,
            work.aggregate_id,
            work.trace_id,
            {"artifact_id": artifact.artifact_id, "artifact_type": artifact.artifact_type},
            work_order_id=work.work_order_id,
            run_id=work.run_id,
        )
        self._advance_after_artifact(work, artifact, envelope.payload)
        self.session.flush()
        return artifact

    def execute_replay(
        self,
        *,
        replay_id: str,
        evolution_id: str,
        proposal_artifact_id: str,
        dataset: dict[str, Any],
    ) -> ReplayRun:
        evolution = self._required(PolicyEvolution, evolution_id)
        if evolution.status != EvolutionStatus.REPLAYING:
            raise StateTransitionError("replay requires an evolution in REPLAYING")
        if evolution.current_proposal_artifact_id != proposal_artifact_id:
            raise ValidationError("replay must use the evolution current proposal")
        proposal_artifact = self._required(Artifact, proposal_artifact_id)
        proposal = PolicyProposal.model_validate(proposal_artifact.payload)
        baseline = self._required(PolicyRow, (evolution.base_policy_id, evolution.base_policy_version))
        candidate = self._required(
            PolicyRow, (proposal.candidate_policy.policy_id, proposal.candidate_policy.version)
        )
        if baseline.content_hash != proposal.base_policy.content_hash:
            raise ValidationError("proposal baseline hash does not match immutable policy")
        result = replay_policies(baseline.dsl_json, candidate.dsl_json, dataset)
        row = ReplayRun(
            replay_id=replay_id,
            evolution_id=evolution_id,
            proposal_artifact_id=proposal_artifact_id,
            baseline_policy_id=baseline.policy_id,
            baseline_policy_version=baseline.version,
            candidate_policy_id=candidate.policy_id,
            candidate_policy_version=candidate.version,
            dataset_version=result["dataset_version"],
            dataset_manifest_hash=result["dataset_manifest_hash"],
            metrics_json=result["metrics"],
            case_results_json=result["case_results"],
            recommendation=result["recommendation"],
            created_at=utcnow(),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def record_human_approval(self, data: dict[str, Any] | ArtifactEnvelope) -> Artifact:
        """Validate a bound Human WorkOrder through the same Artifact acceptance path."""

        envelope = data if isinstance(data, ArtifactEnvelope) else ArtifactEnvelope.model_validate(data)
        if envelope.artifact_type != ArtifactType.HUMAN_APPROVAL:
            raise ValidationError("record_human_approval requires a HUMAN_APPROVAL envelope")
        return self.accept_artifact(envelope)

    def _advance_after_artifact(self, work: WorkOrderRow, artifact: Artifact, payload: Any) -> None:
        if work.aggregate_type == "CASE":
            case = self._required(Case, work.aggregate_id)
            if artifact.artifact_type == ArtifactType.EVIDENCE_BUNDLE:
                if case.status == CaseStatus.INVESTIGATING:
                    self._apply_transition(
                        case, "CASE", case.case_id, "INVESTIGATING", "ARGUING", work.trace_id
                    )
                elif case.status not in {
                    CaseStatus.ARGUING,
                    CaseStatus.ADJUDICATING,
                    CaseStatus.DECIDED,
                }:
                    raise StateTransitionError("accepted evidence bundle has an incompatible case state")
                shared_refs = [
                    {"ref_type": "ARTIFACT", "ref_id": artifact.artifact_id},
                    {
                        "ref_type": "POLICY_SNAPSHOT",
                        "ref_id": case.policy_snapshot_json["snapshot_id"],
                    },
                ]
                self._create_next_work_order(
                    source=work,
                    suffix="R",
                    step_type="RISK_ARGUMENT",
                    assignee="risk-prosecutor",
                    expected_artifact_type="RISK_ARGUMENT",
                    input_refs=shared_refs,
                )
                self._create_next_work_order(
                    source=work,
                    suffix="C",
                    step_type="COUNTER_ARGUMENT",
                    assignee="counter-reviewer",
                    expected_artifact_type="COUNTER_ARGUMENT",
                    input_refs=shared_refs,
                )
            elif artifact.artifact_type in {ArtifactType.RISK_ARGUMENT, ArtifactType.COUNTER_ARGUMENT}:
                arguments = list(
                    self.session.scalars(
                        select(Artifact).where(
                            Artifact.aggregate_type == "CASE",
                            Artifact.aggregate_id == case.case_id,
                            Artifact.artifact_type.in_(["RISK_ARGUMENT", "COUNTER_ARGUMENT"]),
                        ).order_by(Artifact.artifact_type)
                    )
                )
                by_type = {str(item.artifact_type): item for item in arguments}
                if set(by_type) == {"RISK_ARGUMENT", "COUNTER_ARGUMENT"}:
                    if case.status == CaseStatus.ARGUING:
                        self._apply_transition(
                            case, "CASE", case.case_id, "ARGUING", "ADJUDICATING", work.trace_id
                        )
                    elif case.status not in {CaseStatus.ADJUDICATING, CaseStatus.DECIDED}:
                        raise StateTransitionError("accepted arguments have an incompatible case state")
                    evidence = self.session.scalar(
                        select(Artifact).where(
                            Artifact.aggregate_type == "CASE",
                            Artifact.aggregate_id == case.case_id,
                            Artifact.artifact_type == "EVIDENCE_BUNDLE",
                        )
                    )
                    if evidence is None:
                        raise ValidationError("adjudication requires an accepted evidence bundle")
                    self._create_next_work_order(
                        source=work,
                        suffix="J",
                        step_type="ADJUDICATION",
                        assignee="independent-judge",
                        expected_artifact_type="DECISION_RECORD",
                        input_refs=[
                            {"ref_type": "ARTIFACT", "ref_id": evidence.artifact_id},
                            {
                                "ref_type": "POLICY_SNAPSHOT",
                                "ref_id": case.policy_snapshot_json["snapshot_id"],
                            },
                            {"ref_type": "ARTIFACT", "ref_id": by_type["RISK_ARGUMENT"].artifact_id},
                            {"ref_type": "ARTIFACT", "ref_id": by_type["COUNTER_ARGUMENT"].artifact_id},
                        ],
                    )
            elif artifact.artifact_type == ArtifactType.DECISION_RECORD:
                if case.status == CaseStatus.ADJUDICATING:
                    self._apply_transition(
                        case, "CASE", case.case_id, "ADJUDICATING", "DECIDED", work.trace_id
                    )
                elif case.status != CaseStatus.DECIDED:
                    raise StateTransitionError("accepted decision has an incompatible case state")
        elif work.aggregate_type == "APPEAL" and artifact.artifact_type == ArtifactType.APPEAL_DECISION:
            appeal = self._required(Appeal, work.aggregate_id)
            self._require_state(appeal, AppealStatus.REVIEWING)
            self._apply_transition(appeal, "APPEAL", appeal.appeal_id, "REVIEWING", "DECIDED", work.trace_id)
        elif work.aggregate_type == "POLICY_EVOLUTION":
            evolution = self._required(PolicyEvolution, work.aggregate_id)
            if artifact.artifact_type == ArtifactType.ATTRIBUTION_REPORT:
                report = payload if isinstance(payload, AttributionReport) else AttributionReport.model_validate(payload)
                target = "DRAFTING" if report.policy_change_recommended else "CLOSED"
                self._require_state(evolution, EvolutionStatus.ATTRIBUTING)
                self._apply_transition(
                    evolution, "POLICY_EVOLUTION", evolution.evolution_id, "ATTRIBUTING", target, work.trace_id
                )
            elif artifact.artifact_type == ArtifactType.POLICY_PROPOSAL:
                proposal = payload if isinstance(payload, PolicyProposal) else PolicyProposal.model_validate(payload)
                if (
                    proposal.base_policy.policy_id != evolution.base_policy_id
                    or proposal.base_policy.version != evolution.base_policy_version
                ):
                    raise ValidationError("proposal does not use the evolution base policy")
                self.add_policy(proposal.candidate_policy, artifact.artifact_id)
                evolution.current_proposal_artifact_id = artifact.artifact_id
                self._require_state(evolution, EvolutionStatus.DRAFTING)
                self._apply_transition(
                    evolution, "POLICY_EVOLUTION", evolution.evolution_id, "DRAFTING", "REPLAYING", work.trace_id
                )
            elif artifact.artifact_type == ArtifactType.REPLAY_REPORT:
                report = payload if isinstance(payload, ReplayReport) else ReplayReport.model_validate(payload)
                replay = self._required(ReplayRun, report.replay_id)
                if replay.proposal_artifact_id != evolution.current_proposal_artifact_id:
                    raise ValidationError("replay does not bind the current proposal")
                proposal = PolicyProposal.model_validate(
                    self._required(Artifact, replay.proposal_artifact_id).payload
                )
                baseline = self._required(
                    PolicyRow, (replay.baseline_policy_id, replay.baseline_policy_version)
                )
                candidate = self._required(
                    PolicyRow, (replay.candidate_policy_id, replay.candidate_policy_version)
                )
                if (
                    report.proposal_id != proposal.proposal_id
                    or report.baseline_policy.policy_id != baseline.policy_id
                    or report.baseline_policy.version != baseline.version
                    or report.baseline_policy.content_hash != baseline.content_hash
                    or report.candidate_policy.policy_id != candidate.policy_id
                    or report.candidate_policy.version != candidate.version
                    or report.candidate_policy.content_hash != candidate.content_hash
                    or report.dataset_version != replay.dataset_version
                    or report.dataset_manifest_hash != replay.dataset_manifest_hash
                    or report.metrics.model_dump(mode="json") != replay.metrics_json
                    or report.recommendation != replay.recommendation
                ):
                    raise ValidationError("replay report does not match the deterministic Python replay run")
                evolution.current_replay_id = replay.replay_id
                self._require_state(evolution, EvolutionStatus.REPLAYING)
                self._apply_transition(
                    evolution,
                    "POLICY_EVOLUTION",
                    evolution.evolution_id,
                    "REPLAYING",
                    "AWAITING_APPROVAL",
                    work.trace_id,
                )
            elif artifact.artifact_type == ArtifactType.HUMAN_APPROVAL:
                approval = payload if isinstance(payload, HumanApproval) else HumanApproval.model_validate(payload)
                self._require_state(evolution, EvolutionStatus.AWAITING_APPROVAL)
                if (
                    evolution.current_proposal_artifact_id != approval.proposal_artifact_id
                    or evolution.current_replay_id != approval.replay_id
                ):
                    raise ValidationError("approval is not bound to the current proposal and replay")
                replay = self._required(ReplayRun, approval.replay_id)
                if replay.proposal_artifact_id != approval.proposal_artifact_id:
                    raise ValidationError("approval proposal and replay do not match")
                target = "APPROVED" if approval.decision == "APPROVE" else "REJECTED"
                self._apply_transition(
                    evolution,
                    "POLICY_EVOLUTION",
                    evolution.evolution_id,
                    "AWAITING_APPROVAL",
                    target,
                    work.trace_id,
                )
                self._append_event(
                    EventType.HUMAN_APPROVAL,
                    "POLICY_EVOLUTION",
                    evolution.evolution_id,
                    work.trace_id,
                    {"approval_id": approval.approval_id, "decision": approval.decision},
                    work_order_id=work.work_order_id,
                    run_id=work.run_id,
                )

    @staticmethod
    def _derived_execution_id(source_id: str, source_suffix: str, target_suffix: str) -> str:
        marker = f"-{source_suffix}"
        if source_id.endswith(marker):
            return f"{source_id[:-len(marker)]}-{target_suffix}"
        return f"{source_id}-{target_suffix}"

    def _create_next_work_order(
        self,
        *,
        source: WorkOrderRow,
        suffix: str,
        step_type: str,
        assignee: str,
        expected_artifact_type: str,
        input_refs: list[dict[str, str]],
    ) -> WorkOrderRow:
        source_suffix = str(source.step_type)[0] if str(source.step_type) != "ADJUDICATION" else "J"
        work_order_id = self._derived_execution_id(source.work_order_id, source_suffix, suffix)
        existing = self.session.get(WorkOrderRow, work_order_id)
        if existing is not None:
            return existing
        now = utcnow()
        return self.create_work_order(
            {
                "work_order_id": work_order_id,
                "aggregate_type": str(source.aggregate_type),
                "aggregate_id": source.aggregate_id,
                "step_type": step_type,
                "assignee_id": assignee,
                "input_refs": input_refs,
                "expected_artifact_type": expected_artifact_type,
                "run_id": self._derived_execution_id(source.run_id, source_suffix, suffix),
                "trace_id": source.trace_id,
                "status": "PENDING",
                "retry_count": 0,
                "created_at": now,
                "updated_at": now,
            }
        )

    def _apply_transition(
        self,
        row: Any,
        aggregate_type: str,
        aggregate_id: str,
        from_state: str,
        to_state: str,
        trace_id: str,
    ) -> None:
        row.status = to_state
        row.state_version += 1
        row.updated_at = utcnow()
        self._append_event(
            EventType.STATE_TRANSITION,
            aggregate_type,
            aggregate_id,
            trace_id,
            {"state_version": row.state_version},
            from_state=from_state,
            to_state=to_state,
        )
        self.session.flush()

    def _append_event(
        self,
        event_type: EventType,
        aggregate_type: str,
        aggregate_id: str,
        trace_id: str,
        payload: dict[str, Any],
        *,
        from_state: str | None = None,
        to_state: str | None = None,
        work_order_id: str | None = None,
        run_id: str | None = None,
    ) -> DomainEvent:
        now = utcnow()
        identity = {
            "event_type": event_type.value,
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "trace_id": trace_id,
            "payload": payload,
            "at": now.isoformat(),
        }
        row = DomainEvent(
            event_id=f"EV-{hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]}",
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            from_state=from_state,
            to_state=to_state,
            work_order_id=work_order_id,
            run_id=run_id,
            trace_id=trace_id,
            payload_json=payload,
            payload_hash=canonical_hash(payload),
            created_at=now,
        )
        self.session.add(row)
        return row

    def _validate_work_order_aggregate(self, aggregate_type: str, aggregate_id: str) -> None:
        model = {"CASE": Case, "APPEAL": Appeal, "POLICY_EVOLUTION": PolicyEvolution}[aggregate_type]
        self._required(model, aggregate_id)

    @staticmethod
    def _require_state(row: Any, expected: StrEnum) -> None:
        if row.status != expected:
            raise StateTransitionError(f"aggregate must be in {expected.value}")

    def _required(self, model: type[Any], identity: Any) -> Any:
        row = self.session.get(model, identity)
        if row is None:
            raise ValidationError(f"{model.__name__} not found")
        return row
