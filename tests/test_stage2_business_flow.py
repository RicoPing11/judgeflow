from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, inspect, select, text, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core import (
    ConflictError,
    JudgeFlowKernel,
    StateTransitionError,
    ValidationError,
    canonical_hash,
    dataset_manifest_hash_value,
    policy_hash_value,
    snapshot_hash_value,
)
from app.models.database import (
    Appeal,
    AppealStatus,
    Artifact,
    Base,
    Case,
    CaseStatus,
    DomainEvent,
    EvolutionStatus,
    PolicyEvolution,
    PolicyRow,
    ReplayRun,
    WorkOrderRow,
    WorkOrderStatus,
)
from app.replay import PolicyExecutionError, replay_policies


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-12T09:00:00+08:00"


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_element: JSONB, _compiler: Any, **_kw: Any) -> str:
    """Test-only compatibility; production models and migration stay PostgreSQL-native."""

    return "JSON"


def load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def work_order(
    work_order_id: str,
    aggregate_type: str,
    aggregate_id: str,
    step_type: str,
    assignee: str,
    artifact_type: str,
    run_id: str,
) -> dict[str, Any]:
    return {
        "work_order_id": work_order_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "step_type": step_type,
        "assignee_id": assignee,
        "input_refs": [
            {
                "ref_type": {"CASE": "CASE", "APPEAL": "APPEAL", "POLICY_EVOLUTION": "ARTIFACT"}[
                    aggregate_type
                ],
                "ref_id": aggregate_id,
            }
        ],
        "expected_artifact_type": artifact_type,
        "run_id": run_id,
        "trace_id": "TRACE-STAGE2",
        "status": "PENDING",
        "retry_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }


def artifact(
    artifact_id: str,
    work: dict[str, Any],
    payload: dict[str, Any],
    *,
    producer_type: str = "AGENT",
) -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": work["expected_artifact_type"],
        "schema_version": "1.0",
        "aggregate_type": work["aggregate_type"],
        "aggregate_id": work["aggregate_id"],
        "work_order_id": work["work_order_id"],
        "run_id": work["run_id"],
        "trace_id": work["trace_id"],
        "producer_type": producer_type,
        "producer_id": work["assignee_id"],
        "payload": payload,
        "content_hash": canonical_hash(payload),
        "created_at": NOW,
    }


def seed_case(kernel: JudgeFlowKernel) -> Case:
    policy = load("fixtures/policies/baseline_policy.json")
    snapshot = load("fixtures/policies/policy_snapshot.json")
    case_input = load("fixtures/case/main_case.json")
    kernel.add_policy(policy)
    case = kernel.create_case(
        case_id="CASE-001",
        demo_run_id="DEMO-STAGE2",
        input_json=case_input,
        policy_snapshot=snapshot,
    )
    kernel.advance_case("CASE-001", "NEW", "INVESTIGATING")
    return case


def submit(
    kernel: JudgeFlowKernel,
    work: dict[str, Any],
    payload: dict[str, Any],
    artifact_id: str,
    *,
    producer_type: str = "AGENT",
) -> Artifact:
    kernel.create_work_order(work)
    return kernel.accept_artifact(artifact(artifact_id, work, payload, producer_type=producer_type))


def complete_case(kernel: JudgeFlowKernel) -> Case:
    examples = load("fixtures/schema_examples/valid.json")
    case = seed_case(kernel)
    steps = [
        ("WO-E", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE", "RUN-E", "EvidenceBundle", "ART-E"),
        ("WO-R", "RISK_ARGUMENT", "risk", "RISK_ARGUMENT", "RUN-R", "RiskArgument", "ART-R"),
        ("WO-C", "COUNTER_ARGUMENT", "counter", "COUNTER_ARGUMENT", "RUN-C", "CounterArgument", "ART-C"),
        ("WO-D", "ADJUDICATION", "judge", "DECISION_RECORD", "RUN-D", "DecisionRecord", "ART-D"),
    ]
    for work_id, step, assignee, kind, run_id, payload_name, artifact_id in steps:
        work = work_order(work_id, "CASE", case.case_id, step, assignee, kind, run_id)
        submit(kernel, work, examples[payload_name], artifact_id)
        if kind == "RISK_ARGUMENT":
            assert case.status == CaseStatus.ARGUING
    assert case.status == CaseStatus.DECIDED
    return case


def complete_to_approval(kernel: JudgeFlowKernel) -> tuple[PolicyEvolution, dict[str, Any]]:
    examples = load("fixtures/schema_examples/valid.json")
    complete_case(kernel)
    appeal_input = load("fixtures/appeal/appeal_request.json")
    appeal = kernel.create_appeal(
        appeal_id="APPEAL-001",
        case_id="CASE-001",
        request_json=appeal_input,
        original_case_snapshot_json={"case_id": "CASE-001", "decision_id": "DR-001"},
        allowed_evidence_ids=appeal_input["allowed_new_evidence_ids"],
    )
    kernel.advance_appeal(appeal.appeal_id, "NEW", "REVIEWING")
    appeal_work = work_order(
        "WO-A", "APPEAL", appeal.appeal_id, "APPEAL_REVIEW", "appeal-reviewer", "APPEAL_DECISION", "RUN-A"
    )
    submit(kernel, appeal_work, examples["AppealDecision"], "ART-A")
    evolution = kernel.create_policy_evolution(
        evolution_id="EVOLUTION-001",
        source_appeal_id=appeal.appeal_id,
        base_policy_id="MINOR_DANGEROUS_ACT",
        base_policy_version="1.0",
    )
    kernel.advance_policy_evolution(evolution.evolution_id, "NEW", "ATTRIBUTING")
    attribution_work = work_order(
        "WO-AT",
        "POLICY_EVOLUTION",
        evolution.evolution_id,
        "ATTRIBUTION",
        "attributor",
        "ATTRIBUTION_REPORT",
        "RUN-AT",
    )
    submit(kernel, attribution_work, examples["AttributionReport"], "ART-AT")
    proposal_work = work_order(
        "WO-P",
        "POLICY_EVOLUTION",
        evolution.evolution_id,
        "POLICY_DRAFTING",
        "policy-author",
        "POLICY_PROPOSAL",
        "RUN-P",
    )
    submit(kernel, proposal_work, examples["PolicyProposal"], "ART-PP-001")
    replay = kernel.execute_replay(
        replay_id="REPLAY-001",
        evolution_id=evolution.evolution_id,
        proposal_artifact_id="ART-PP-001",
        dataset=load("fixtures/replay/v1/dataset.json"),
    )
    assert replay.metrics_json == examples["ReplayReport"]["metrics"]
    replay_work = work_order(
        "WO-RP",
        "POLICY_EVOLUTION",
        evolution.evolution_id,
        "REPLAY",
        "replay-analyst",
        "REPLAY_REPORT",
        "RUN-RP",
    )
    submit(kernel, replay_work, examples["ReplayReport"], "ART-RP")
    assert evolution.status == EvolutionStatus.AWAITING_APPROVAL
    return evolution, examples


def test_full_fixed_flow_runs_without_agents_mcp_matrix_or_frontend(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    evolution, examples = complete_to_approval(kernel)
    approval_work = work_order(
        "WO-H",
        "POLICY_EVOLUTION",
        evolution.evolution_id,
        "REPLAY",
        "policy-owner-demo",
        "HUMAN_APPROVAL",
        "RUN-H",
    )
    kernel.create_work_order(approval_work)
    approval_envelope = artifact(
        "ART-H",
        approval_work,
        examples["HumanApproval"],
        producer_type="HUMAN",
    )
    kernel.record_human_approval(approval_envelope)
    session.commit()
    assert evolution.status == EvolutionStatus.APPROVED
    assert session.get(PolicyRow, ("MINOR_DANGEROUS_ACT", "2.0-demo-r1")).status.value == "CANDIDATE"
    assert session.get(WorkOrderRow, "WO-H").status == WorkOrderStatus.SUCCEEDED


def test_public_transitions_cannot_bypass_accepted_artifacts(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    with pytest.raises(StateTransitionError):
        kernel.advance_case("CASE-001", "INVESTIGATING", "ARGUING")
    with pytest.raises(StateTransitionError):
        kernel.advance_case("CASE-001", "NEW", "DECIDED")


def test_failed_work_order_does_not_advance_and_retry_creates_new_run_and_artifact(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    case = seed_case(kernel)
    examples = load("fixtures/schema_examples/valid.json")
    failed_work = work_order(
        "WO-FAIL", "CASE", case.case_id, "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE", "RUN-FAIL"
    )
    kernel.create_work_order(failed_work)
    kernel.fail_work_order("WO-FAIL", "TIMEOUT")
    assert case.status == CaseStatus.INVESTIGATING
    retry = kernel.retry_work_order("WO-FAIL", work_order_id="WO-RETRY", run_id="RUN-RETRY")
    retry_data = copy.deepcopy(failed_work)
    retry_data.update(work_order_id=retry.work_order_id, run_id=retry.run_id)
    accepted = kernel.accept_artifact(artifact("ART-RETRY", retry_data, examples["EvidenceBundle"]))
    assert retry.attempt == 2
    assert accepted.run_id == "RUN-RETRY"
    assert case.status == CaseStatus.ARGUING


def test_artifact_schema_assignee_type_producer_hash_and_idempotency(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    examples = load("fixtures/schema_examples/valid.json")
    work = work_order("WO-1", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE", "RUN-1")
    kernel.create_work_order(work)
    good = artifact("ART-1", work, examples["EvidenceBundle"])
    bad_hash = copy.deepcopy(good)
    bad_hash["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError, match="hash"):
        kernel.accept_artifact(bad_hash)
    wrong_producer = copy.deepcopy(good)
    wrong_producer["producer_type"] = "SYSTEM"
    with pytest.raises(ValidationError, match="agent"):
        kernel.accept_artifact(wrong_producer)
    wrong_assignee = copy.deepcopy(good)
    wrong_assignee["producer_id"] = "someone-else"
    with pytest.raises(ValidationError, match="assignee"):
        kernel.accept_artifact(wrong_assignee)
    wrong_expected_type = copy.deepcopy(good)
    wrong_expected_type["artifact_type"] = "RISK_ARGUMENT"
    wrong_expected_type["payload"] = examples["RiskArgument"]
    wrong_expected_type["content_hash"] = canonical_hash(wrong_expected_type["payload"])
    with pytest.raises(ValidationError, match="expectation"):
        kernel.accept_artifact(wrong_expected_type)
    first = kernel.accept_artifact(good)
    assert kernel.accept_artifact(copy.deepcopy(good)).artifact_id == first.artifact_id
    conflicting = copy.deepcopy(good)
    conflicting["payload"]["bundle_id"] = "OTHER"
    conflicting["content_hash"] = canonical_hash(conflicting["payload"])
    with pytest.raises(ConflictError):
        kernel.accept_artifact(conflicting)


def test_work_order_creation_requires_fresh_pending_request(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    running = work_order("WO-X", "CASE", "CASE-001", "INVESTIGATION", "i", "EVIDENCE_BUNDLE", "RUN-X")
    running["status"] = "RUNNING"
    with pytest.raises(ValidationError, match="PENDING"):
        kernel.create_work_order(running)
    retried = work_order("WO-Y", "CASE", "CASE-001", "INVESTIGATION", "i", "EVIDENCE_BUNDLE", "RUN-Y")
    retried["retry_count"] = 1
    with pytest.raises(ValidationError, match="retry_count"):
        kernel.create_work_order(retried)


def test_case_artifacts_create_only_the_authorized_next_work_orders(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    examples = load("fixtures/schema_examples/valid.json")
    investigation = work_order(
        "WO-AUTO-I",
        "CASE",
        "CASE-001",
        "INVESTIGATION",
        "case-investigator",
        "EVIDENCE_BUNDLE",
        "RUN-AUTO-I",
    )
    investigation["input_refs"] = [
        {"ref_type": "CASE", "ref_id": "CASE-001"},
        {"ref_type": "EVIDENCE", "ref_id": "E-001"},
    ]
    kernel.create_work_order(investigation)
    evidence_envelope = artifact("ART-AUTO-I", investigation, examples["EvidenceBundle"])
    kernel.accept_artifact(evidence_envelope)

    risk = session.get(WorkOrderRow, "WO-AUTO-R")
    counter = session.get(WorkOrderRow, "WO-AUTO-C")
    assert risk is not None and counter is not None
    assert risk.run_id == "RUN-AUTO-R" and counter.run_id == "RUN-AUTO-C"
    assert risk.assignee == "risk-prosecutor" and counter.assignee == "counter-reviewer"
    expected_shared_refs = [
        {"ref_type": "ARTIFACT", "ref_id": "ART-AUTO-I"},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
    ]
    assert risk.input_refs == counter.input_refs == expected_shared_refs

    # An idempotent replay repairs orchestration without duplicating WorkOrders.
    kernel.accept_artifact(copy.deepcopy(evidence_envelope))
    assert session.query(WorkOrderRow).filter(WorkOrderRow.aggregate_id == "CASE-001").count() == 3

    def as_request(row: WorkOrderRow) -> dict[str, Any]:
        return {
            "work_order_id": row.work_order_id,
            "aggregate_type": str(row.aggregate_type),
            "aggregate_id": row.aggregate_id,
            "step_type": str(row.step_type),
            "assignee_id": row.assignee,
            "input_refs": row.input_refs,
            "expected_artifact_type": str(row.expected_artifact_type),
            "run_id": row.run_id,
            "trace_id": row.trace_id,
        }

    kernel.accept_artifact(artifact("ART-AUTO-R", as_request(risk), examples["RiskArgument"]))
    assert session.get(WorkOrderRow, "WO-AUTO-J") is None
    kernel.accept_artifact(artifact("ART-AUTO-C", as_request(counter), examples["CounterArgument"]))
    judge = session.get(WorkOrderRow, "WO-AUTO-J")
    assert judge is not None
    assert judge.assignee == "independent-judge" and judge.run_id == "RUN-AUTO-J"
    assert judge.input_refs == [
        {"ref_type": "ARTIFACT", "ref_id": "ART-AUTO-I"},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
        {"ref_type": "ARTIFACT", "ref_id": "ART-AUTO-R"},
        {"ref_type": "ARTIFACT", "ref_id": "ART-AUTO-C"},
    ]


def test_policy_snapshot_artifact_policy_replay_and_event_are_immutable(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    evolution, _examples = complete_to_approval(kernel)
    session.commit()
    case = session.get(Case, "CASE-001")
    locked = copy.deepcopy(case.policy_snapshot_json)
    case.policy_snapshot_json = {"changed": True}
    with pytest.raises(ValueError, match="snapshot"):
        session.flush()
    session.rollback()
    case = session.get(Case, "CASE-001")
    assert case.policy_snapshot_json == locked
    for row, attribute in [
        (session.scalar(select(Artifact)), "content_hash"),
        (session.scalar(select(PolicyRow)), "title"),
        (session.scalar(select(ReplayRun)), "recommendation"),
        (session.scalar(select(DomainEvent)), "trace_id"),
    ]:
        setattr(row, attribute, "changed")
        with pytest.raises(ValueError, match="immutable"):
            session.flush()
        session.rollback()
    assert evolution.evolution_id == "EVOLUTION-001"


def test_bulk_update_delete_cannot_change_frozen_case_or_appeal_data(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    complete_case(kernel)
    appeal = kernel.create_appeal(
        appeal_id="APPEAL-001",
        case_id="CASE-001",
        request_json={},
        original_case_snapshot_json={"case_id": "CASE-001"},
        allowed_evidence_ids=["E-APPEAL-001"],
    )
    with pytest.raises(ValueError, match="snapshot"):
        session.execute(update(Case).values(policy_snapshot_hash="sha256:" + "0" * 64))
    with pytest.raises(ValueError, match="whitelist"):
        session.execute(update(Appeal).values(allowed_evidence_ids=[]))
    with pytest.raises(ValueError, match="snapshot"):
        session.execute(delete(Case))
    with pytest.raises(ValueError, match="whitelist"):
        session.execute(delete(Appeal))
    assert appeal.allowed_evidence_ids == ["E-APPEAL-001"]


def test_policy_snapshot_and_replay_manifest_hashes_are_recomputed(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    policy = load("fixtures/policies/baseline_policy.json")
    assert policy["content_hash"] == policy_hash_value(policy)
    bad_policy = copy.deepcopy(policy)
    bad_policy["title"] = "tampered"
    with pytest.raises(ValidationError, match="policy content hash"):
        kernel.add_policy(bad_policy)
    kernel.add_policy(policy)
    snapshot = load("fixtures/policies/policy_snapshot.json")
    assert snapshot["snapshot_hash"] == snapshot_hash_value(snapshot)
    bad_snapshot = copy.deepcopy(snapshot)
    bad_snapshot["snapshot_id"] = "tampered"
    with pytest.raises(ValidationError, match="snapshot hash"):
        kernel.create_case(case_id="CASE-X", demo_run_id="D", input_json={}, policy_snapshot=bad_snapshot)
    dataset = load("fixtures/replay/v1/dataset.json")
    assert dataset["manifest_hash"] == dataset_manifest_hash_value(dataset)
    dataset["samples"][0]["facts"]["MINOR_PRESENT"] = False
    candidate = load("fixtures/schema_examples/valid.json")["PolicyProposal"]["candidate_policy"]
    with pytest.raises(PolicyExecutionError, match="manifest hash"):
        replay_policies(policy, candidate, dataset)


def test_replay_metrics_only_score_scoring_split() -> None:
    baseline = load("fixtures/policies/baseline_policy.json")
    candidate = load("fixtures/schema_examples/valid.json")["PolicyProposal"]["candidate_policy"]
    result = replay_policies(baseline, candidate, load("fixtures/replay/v1/dataset.json"))
    assert result["metrics"] == {
        "baseline_false_positives": 0,
        "candidate_false_positives": 0,
        "baseline_false_negatives": 0,
        "candidate_false_negatives": 0,
        "changed_cases": 0,
    }
    trigger = next(row for row in result["case_results"] if row["split"] == "TRIGGER")
    assert trigger["baseline_outcome"] != trigger["candidate_outcome"]
    assert result["recommendation"] == "INCONCLUSIVE"


def test_online_empty_postgresql_migration_when_database_url_is_provided() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL 未设置；在线空 PostgreSQL 迁移未验证")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                # PostgreSQL DDL is transactional: this isolated schema is rolled back,
                # so the online empty-schema proof leaves no tables or test database behind.
                connection.execute(text("CREATE SCHEMA judgeflow_stage2_migration_test"))
                connection.execute(text("SET LOCAL search_path TO judgeflow_stage2_migration_test"))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                business_tables = set(
                    inspect(connection).get_table_names(schema="judgeflow_stage2_migration_test")
                ) - {"alembic_version"}
                assert business_tables == set(Base.metadata.tables)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
