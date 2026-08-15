"""Seed and execute the fixed eight-agent path against migrated PostgreSQL.

This is a stage-four acceptance entry, not an AgentTeams runtime. Aggregate and
WorkOrder creation remains a backend/kernel action; every domain artifact is
produced through FixedAgentRunner and accepted through the existing MCP service.
"""

from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.runner import FixedAgentRunner
from app.agents.mcp_gateway import FastMCPGateway
from app.core import JudgeFlowKernel
from app.mcp import JudgeFlowMCPService
from app.models.database import Appeal, Artifact, Case, DomainEvent, PolicyEvolution, PolicyRow, ReplayRun, WorkOrderRow


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-08-12T09:00:00+08:00"


def _load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _work(
    tag: str,
    code: str,
    aggregate_type: str,
    aggregate_id: str,
    step: str,
    assignee: str,
    output: str,
    refs: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "work_order_id": f"WO-{tag}-{code}",
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "step_type": step,
        "assignee_id": assignee,
        "input_refs": refs,
        "expected_artifact_type": output,
        "run_id": f"RUN-{tag}-{code}",
        "trace_id": f"TRACE-{tag}",
        "status": "PENDING",
        "retry_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _execute(kernel: JudgeFlowKernel, runner: FixedAgentRunner, data: dict[str, Any]):
    kernel.create_work_order(data)
    result = runner.run(data["assignee_id"], data["work_order_id"], data["run_id"], data["trace_id"])
    if result.status != "SUCCESS" or result.artifact_id is None or result.artifact is None:
        raise RuntimeError(f"{data['assignee_id']} failed: {result.error_code} {result.message}")
    return result


def _ids(tag: str) -> dict[str, str]:
    return {
        "case_id": f"CASE-{tag}",
        "appeal_id": f"APPEAL-{tag}",
        "evolution_id": "EVOLUTION-001" if tag == "PG1" else f"EVOLUTION-{tag}",
    }


def run_seed(session: Session, tag: str) -> dict[str, Any]:
    """Create one deterministic run, or verify and return an existing complete run."""

    identities = _ids(tag)
    if session.get(Case, identities["case_id"]) is not None:
        return verify_seed(session, tag)

    kernel = JudgeFlowKernel(session)
    runner = FixedAgentRunner(FastMCPGateway(JudgeFlowMCPService(session)))
    baseline = _load("fixtures/policies/baseline_policy.json")
    stored_baseline = session.get(PolicyRow, (baseline["policy_id"], baseline["version"]))
    if stored_baseline is None:
        kernel.add_policy(baseline)
    elif stored_baseline.content_hash != baseline["content_hash"]:
        raise RuntimeError("stored baseline policy does not match the fixed seed")

    case_input = deepcopy(_load("fixtures/case/main_case.json"))
    case_input.update(case_id=identities["case_id"], demo_run_id=f"DEMO-{tag}")
    case = kernel.create_case(
        case_id=identities["case_id"],
        demo_run_id=f"DEMO-{tag}",
        input_json=case_input,
        policy_snapshot=_load("fixtures/policies/policy_snapshot.json"),
    )
    kernel.advance_case(case.case_id, "NEW", "INVESTIGATING", trace_id=f"TRACE-{tag}")
    investigation = _execute(
        kernel,
        runner,
        _work(tag, "I", "CASE", case.case_id, "INVESTIGATION", "case-investigator", "EVIDENCE_BUNDLE", [
            {"ref_type": "CASE", "ref_id": case.case_id},
            {"ref_type": "EVIDENCE", "ref_id": "E-001"},
        ]),
    )
    shared = [
        {"ref_type": "ARTIFACT", "ref_id": investigation.artifact_id},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
    ]
    risk = _execute(kernel, runner, _work(tag, "R", "CASE", case.case_id, "RISK_ARGUMENT", "risk-prosecutor", "RISK_ARGUMENT", shared))
    counter = _execute(kernel, runner, _work(tag, "C", "CASE", case.case_id, "COUNTER_ARGUMENT", "counter-reviewer", "COUNTER_ARGUMENT", shared))
    decision = _execute(
        kernel,
        runner,
        _work(tag, "J", "CASE", case.case_id, "ADJUDICATION", "independent-judge", "DECISION_RECORD", shared + [
            {"ref_type": "ARTIFACT", "ref_id": risk.artifact_id},
            {"ref_type": "ARTIFACT", "ref_id": counter.artifact_id},
        ]),
    )

    appeal_request = deepcopy(_load("fixtures/appeal/appeal_request.json"))
    appeal_request.update(appeal_id=identities["appeal_id"], case_id=case.case_id)
    appeal = kernel.create_appeal(
        appeal_id=identities["appeal_id"],
        case_id=case.case_id,
        request_json=appeal_request,
        original_case_snapshot_json={
            "case_id": case.case_id,
            "decision_id": decision.artifact["payload"]["decision_id"],
            "evidence_ids": ["E-001"],
            "policy_snapshot": _load("fixtures/policies/policy_snapshot.json"),
        },
        allowed_evidence_ids=["E-APPEAL-001"],
    )
    kernel.advance_appeal(appeal.appeal_id, "NEW", "REVIEWING", trace_id=f"TRACE-{tag}")
    appeal_result = _execute(
        kernel,
        runner,
        _work(tag, "A", "APPEAL", appeal.appeal_id, "APPEAL_REVIEW", "appeal-reviewer", "APPEAL_DECISION", [
            {"ref_type": "APPEAL", "ref_id": appeal.appeal_id},
            {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"},
        ]),
    )

    evolution = kernel.create_policy_evolution(
        evolution_id=identities["evolution_id"],
        source_appeal_id=appeal.appeal_id,
        base_policy_id=baseline["policy_id"],
        base_policy_version=baseline["version"],
    )
    kernel.advance_policy_evolution(evolution.evolution_id, "NEW", "ATTRIBUTING", trace_id=f"TRACE-{tag}")
    attribution = _execute(
        kernel,
        runner,
        _work(tag, "AT", "POLICY_EVOLUTION", evolution.evolution_id, "ATTRIBUTION", "case-attributor", "ATTRIBUTION_REPORT", [
            {"ref_type": "ARTIFACT", "ref_id": appeal_result.artifact_id},
            {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
            {"ref_type": "REPLAY_DATASET", "ref_id": "v1"},
        ]),
    )
    proposal = _execute(
        kernel,
        runner,
        _work(tag, "P", "POLICY_EVOLUTION", evolution.evolution_id, "POLICY_DRAFTING", "policy-author", "POLICY_PROPOSAL", [
            {"ref_type": "ARTIFACT", "ref_id": attribution.artifact_id},
            {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
            {"ref_type": "REPLAY_DATASET", "ref_id": "v1"},
        ]),
    )
    _execute(
        kernel,
        runner,
        _work(tag, "RP", "POLICY_EVOLUTION", evolution.evolution_id, "REPLAY", "replay-analyst", "REPLAY_REPORT", [
            {"ref_type": "ARTIFACT", "ref_id": proposal.artifact_id},
            {"ref_type": "REPLAY_DATASET", "ref_id": "v1"},
        ]),
    )
    session.commit()
    return verify_seed(session, tag)


def verify_seed(session: Session, tag: str) -> dict[str, Any]:
    identities = _ids(tag)
    case = session.get(Case, identities["case_id"])
    evolution = session.get(PolicyEvolution, identities["evolution_id"])
    if case is None or evolution is None:
        raise RuntimeError(f"seed {tag} is absent or incomplete")
    works = list(session.scalars(select(WorkOrderRow).where(WorkOrderRow.trace_id == f"TRACE-{tag}").order_by(WorkOrderRow.work_order_id)))
    artifacts = list(session.scalars(select(Artifact).where(Artifact.trace_id == f"TRACE-{tag}").order_by(Artifact.artifact_type)))
    if len(works) != 8 or len(artifacts) != 8 or any(work.status.value != "SUCCEEDED" for work in works):
        raise RuntimeError(f"seed {tag} does not contain eight successful WorkOrders and Artifacts")
    replay = session.get(ReplayRun, evolution.current_replay_id)
    candidate = session.get(PolicyRow, (replay.candidate_policy_id, replay.candidate_policy_version)) if replay else None
    if replay is None or candidate is None or candidate.status.value != "CANDIDATE":
        raise RuntimeError(f"seed {tag} is missing its deterministic replay or candidate policy")
    return {
        "tag": tag,
        "case_id": case.case_id,
        "case_status": case.status.value,
        "evolution_id": evolution.evolution_id,
        "evolution_status": evolution.status.value,
        "work_orders": len(works),
        "artifacts": len(artifacts),
        "artifact_types": sorted(artifact.artifact_type.value for artifact in artifacts),
        "candidate_policy": f"{candidate.policy_id}/{candidate.version}",
        "replay_id": replay.replay_id,
        "recommendation": replay.recommendation.value,
    }


def database_summary(session: Session) -> dict[str, int]:
    return {
        "cases": session.scalar(select(func.count()).select_from(Case)) or 0,
        "appeals": session.scalar(select(func.count()).select_from(Appeal)) or 0,
        "policy_evolutions": session.scalar(select(func.count()).select_from(PolicyEvolution)) or 0,
        "work_orders": session.scalar(select(func.count()).select_from(WorkOrderRow)) or 0,
        "artifacts": session.scalar(select(func.count()).select_from(Artifact)) or 0,
        "policies": session.scalar(select(func.count()).select_from(PolicyRow)) or 0,
        "replay_runs": session.scalar(select(func.count()).select_from(ReplayRun)) or 0,
        "domain_events": session.scalar(select(func.count()).select_from(DomainEvent)) or 0,
    }


def main() -> None:
    from sqlalchemy import create_engine

    parser = argparse.ArgumentParser(description="Seed and verify the fixed stage-four PostgreSQL path")
    parser.add_argument("--tags", nargs="+", default=["PG1"], help="stable isolated run tags")
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        results = []
        with Session(engine) as session:
            for tag in args.tags:
                results.append(run_seed(session, tag))
            summary = database_summary(session)
        print(json.dumps({"runs": results, "database": summary}, ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
