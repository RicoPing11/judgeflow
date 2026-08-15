from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.agents import AGENT_SPECS, FixedAgentRunner
from app.core import JudgeFlowKernel, canonical_hash
from app.mcp import JudgeFlowMCPService
from app.models.database import Artifact, Base, CaseStatus, EvolutionStatus, ReplayRun
from app.models.schemas import ArtifactEnvelope, ArtifactType, AttributionReport


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-12T09:00:00+08:00"


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_element: JSONB, _compiler: Any, **_kw: Any) -> str:
    return "JSON"


def load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def work(
    work_order_id: str,
    aggregate_type: str,
    aggregate_id: str,
    step: str,
    assignee: str,
    output: str,
    refs: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "work_order_id": work_order_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "step_type": step,
        "assignee_id": assignee,
        "input_refs": refs,
        "expected_artifact_type": output,
        "run_id": f"RUN-{work_order_id}",
        "trace_id": "TRACE-STAGE4",
        "status": "PENDING",
        "retry_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }


def execute(kernel: JudgeFlowKernel, runner: FixedAgentRunner, data: dict[str, Any]):
    kernel.create_work_order(data)
    result = runner.run(data["assignee_id"], data["work_order_id"], data["run_id"], data["trace_id"])
    assert result.status == "SUCCESS", result
    assert ArtifactEnvelope.model_validate(result.artifact).artifact_type == ArtifactType(data["expected_artifact_type"])
    return result


def run_fixed_chain() -> dict[str, Any]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        kernel = JudgeFlowKernel(session)
        service = JudgeFlowMCPService(session)
        runner = FixedAgentRunner(service)
        kernel.add_policy(load("fixtures/policies/baseline_policy.json"))
        case = kernel.create_case(
            case_id="CASE-001",
            demo_run_id="DEMO-STAGE4",
            input_json=load("fixtures/case/main_case.json"),
            policy_snapshot=load("fixtures/policies/policy_snapshot.json"),
        )
        kernel.advance_case("CASE-001", "NEW", "INVESTIGATING", trace_id="TRACE-STAGE4")

        investigation = work(
            "WO-I", "CASE", "CASE-001", "INVESTIGATION", "case-investigator", "EVIDENCE_BUNDLE",
            [{"ref_type": "CASE", "ref_id": "CASE-001"}, {"ref_type": "EVIDENCE", "ref_id": "E-001"}],
        )
        evidence = execute(kernel, runner, investigation)
        assert case.status == CaseStatus.ARGUING

        base_refs = [
            {"ref_type": "ARTIFACT", "ref_id": evidence.artifact_id},
            {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
        ]
        risk = execute(kernel, runner, work("WO-R", "CASE", "CASE-001", "RISK_ARGUMENT", "risk-prosecutor", "RISK_ARGUMENT", base_refs))
        counter = execute(kernel, runner, work("WO-C", "CASE", "CASE-001", "COUNTER_ARGUMENT", "counter-reviewer", "COUNTER_ARGUMENT", base_refs))
        assert case.status == CaseStatus.ADJUDICATING
        decision = execute(
            kernel,
            runner,
            work(
                "WO-J", "CASE", "CASE-001", "ADJUDICATION", "independent-judge", "DECISION_RECORD",
                base_refs + [{"ref_type": "ARTIFACT", "ref_id": risk.artifact_id}, {"ref_type": "ARTIFACT", "ref_id": counter.artifact_id}],
            ),
        )
        assert case.status == CaseStatus.DECIDED

        appeal_data = load("fixtures/appeal/appeal_request.json")
        appeal = kernel.create_appeal(
            appeal_id="APPEAL-001",
            case_id="CASE-001",
            request_json=appeal_data,
            original_case_snapshot_json={
                "case_id": "CASE-001",
                "decision_id": decision.artifact["payload"]["decision_id"],
                "evidence_ids": ["E-001"],
                "policy_snapshot": load("fixtures/policies/policy_snapshot.json"),
            },
            allowed_evidence_ids=["E-APPEAL-001"],
        )
        kernel.advance_appeal("APPEAL-001", "NEW", "REVIEWING", trace_id="TRACE-STAGE4")
        appeal_result = execute(
            kernel,
            runner,
            work(
                "WO-A", "APPEAL", "APPEAL-001", "APPEAL_REVIEW", "appeal-reviewer", "APPEAL_DECISION",
                [{"ref_type": "APPEAL", "ref_id": "APPEAL-001"}, {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"}],
            ),
        )

        evolution = kernel.create_policy_evolution(
            evolution_id="EVOLUTION-001",
            source_appeal_id=appeal.appeal_id,
            base_policy_id="MINOR_DANGEROUS_ACT",
            base_policy_version="1.0",
        )
        kernel.advance_policy_evolution("EVOLUTION-001", "NEW", "ATTRIBUTING", trace_id="TRACE-STAGE4")
        attribution = execute(
            kernel,
            runner,
            work(
                "WO-AT", "POLICY_EVOLUTION", "EVOLUTION-001", "ATTRIBUTION", "case-attributor", "ATTRIBUTION_REPORT",
                [{"ref_type": "ARTIFACT", "ref_id": appeal_result.artifact_id}, {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"}, {"ref_type": "REPLAY_DATASET", "ref_id": "v1"}],
            ),
        )
        proposal = execute(
            kernel,
            runner,
            work(
                "WO-P", "POLICY_EVOLUTION", "EVOLUTION-001", "POLICY_DRAFTING", "policy-author", "POLICY_PROPOSAL",
                [{"ref_type": "ARTIFACT", "ref_id": attribution.artifact_id}, {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"}, {"ref_type": "REPLAY_DATASET", "ref_id": "v1"}],
            ),
        )
        replay = execute(
            kernel,
            runner,
            work(
                "WO-RP", "POLICY_EVOLUTION", "EVOLUTION-001", "REPLAY", "replay-analyst", "REPLAY_REPORT",
                [{"ref_type": "ARTIFACT", "ref_id": proposal.artifact_id}, {"ref_type": "REPLAY_DATASET", "ref_id": "v1"}],
            ),
        )
        replay_row = session.get(ReplayRun, replay.artifact["payload"]["replay_id"])
        assert replay.artifact["payload"]["metrics"] == replay_row.metrics_json
        assert replay.artifact["payload"]["recommendation"] == replay_row.recommendation.value
        assert evolution.status == EvolutionStatus.AWAITING_APPROVAL
        artifacts = list(session.query(Artifact).all())
        return {
            "types": [row.artifact_type.value for row in artifacts],
            "payload_hashes": [canonical_hash(row.payload) for row in artifacts],
            "decision": decision.artifact["payload"],
            "replay": replay.artifact["payload"],
        }


def test_eight_agent_contracts_have_exact_skill_tool_and_output_permissions() -> None:
    assert set(AGENT_SPECS) == {
        "case-investigator", "risk-prosecutor", "counter-reviewer", "independent-judge",
        "appeal-reviewer", "case-attributor", "policy-author", "replay-analyst",
    }
    assert {spec.output_type for spec in AGENT_SPECS.values()} == set(ArtifactType) - {ArtifactType.HUMAN_APPROVAL}
    assert AGENT_SPECS["case-investigator"].allowed_tools == ("work_order.get", "context.get", "evidence.search", "artifact.put")
    assert AGENT_SPECS["replay-analyst"].allowed_tools == ("work_order.get", "context.get", "replay.execute", "artifact.put")
    for agent_id, spec in AGENT_SPECS.items():
        assert spec.skill in {"case-investigation", "case-deliberation", "policy-evolution", "case-replay"}
        assert spec.allowed_tools == tuple(dict.fromkeys(spec.allowed_tools))
        assert "artifact.put" in spec.allowed_tools
        assert spec.prohibited, agent_id


def test_first_decision_and_full_eight_agent_chain_is_repeatable_three_times() -> None:
    runs = [run_fixed_chain() for _ in range(3)]
    expected_types = [
        "EVIDENCE_BUNDLE", "RISK_ARGUMENT", "COUNTER_ARGUMENT", "DECISION_RECORD",
        "APPEAL_DECISION", "ATTRIBUTION_REPORT", "POLICY_PROPOSAL", "REPLAY_REPORT",
    ]
    assert all(run["types"] == expected_types for run in runs)
    assert runs[0] == runs[1] == runs[2]


class FakeGateway:
    def __init__(self, agent_id: str, *, context: dict[str, Any] | None = None, error_code: str | None = None):
        self.spec = AGENT_SPECS[agent_id]
        self.context = context if context is not None else {"aggregate_id": "X", "materials": []}
        self.error_code = error_code

    def work_order_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str):
        return {"ok": True, "data": {"work_order_id": work_order_id, "aggregate_type": "CASE" if self.spec.step_type in {"INVESTIGATION", "RISK_ARGUMENT", "COUNTER_ARGUMENT", "ADJUDICATION"} else "APPEAL" if self.spec.step_type == "APPEAL_REVIEW" else "POLICY_EVOLUTION", "aggregate_id": "X", "step_type": self.spec.step_type, "assignee_id": self.spec.agent_id, "input_refs": [{"ref_type": "CASE", "ref_id": "X"}], "expected_artifact_type": self.spec.output_type, "run_id": run_id, "trace_id": trace_id, "status": "PENDING", "retry_count": 0, "created_at": NOW, "updated_at": NOW}, "error": None}

    def context_get(self, *_args):
        if self.error_code:
            return {"ok": False, "data": None, "error": {"code": self.error_code, "message": "fixed failure"}}
        return {"ok": True, "data": self.context, "error": None}

    def evidence_search(self, *_args):
        return {"ok": False, "data": None, "error": {"code": "TIMEOUT", "message": "fixed timeout"}}

    def artifact_put(self, *_args):
        raise AssertionError("missing/failure paths must not submit an artifact")

    def replay_execute(self, *_args):
        raise AssertionError("missing/failure paths must not execute replay")


@pytest.mark.parametrize("agent_id", list(AGENT_SPECS))
def test_each_agent_has_direct_missing_information_path(agent_id: str) -> None:
    result = FixedAgentRunner(FakeGateway(agent_id)).run(agent_id, "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "MISSING_INFORMATION"
    assert result.error_code == "INCOMPLETE_DATA"
    assert result.artifact is None


@pytest.mark.parametrize(
    ("agent_id", "code"),
    zip(AGENT_SPECS, ["NOT_FOUND", "INCOMPLETE_DATA", "TIMEOUT", "PERMISSION_DENIED", "VALIDATION_ERROR", "CONFLICT", "TIMEOUT", "NOT_FOUND"]),
)
def test_each_agent_preserves_direct_tool_failure(agent_id: str, code: str) -> None:
    result = FixedAgentRunner(FakeGateway(agent_id, error_code=code)).run(agent_id, "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "TOOL_FAILURE"
    assert result.error_code == code
    assert result.artifact is None


def test_wrong_executor_run_and_trace_are_rejected_by_existing_mcp_authorization() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        kernel = JudgeFlowKernel(session)
        kernel.add_policy(load("fixtures/policies/baseline_policy.json"))
        kernel.create_case(case_id="CASE-001", demo_run_id="D", input_json=load("fixtures/case/main_case.json"), policy_snapshot=load("fixtures/policies/policy_snapshot.json"))
        kernel.advance_case("CASE-001", "NEW", "INVESTIGATING")
        data = work("WO-I", "CASE", "CASE-001", "INVESTIGATION", "case-investigator", "EVIDENCE_BUNDLE", [{"ref_type": "CASE", "ref_id": "CASE-001"}, {"ref_type": "EVIDENCE", "ref_id": "E-001"}])
        kernel.create_work_order(data)
        service = JudgeFlowMCPService(session)
        assert not service.work_order_get("risk-prosecutor", "WO-I", data["run_id"], data["trace_id"])["ok"]
        assert not service.work_order_get("case-investigator", "WO-I", "WRONG-RUN", data["trace_id"])["ok"]
        assert not service.work_order_get("case-investigator", "WO-I", data["run_id"], "WRONG-TRACE")["ok"]
        wrong_executor = FixedAgentRunner(service).run("risk-prosecutor", "WO-I", data["run_id"], data["trace_id"])
        wrong_run = FixedAgentRunner(service).run("case-investigator", "WO-I", "WRONG-RUN", data["trace_id"])
        assert wrong_executor.status == wrong_run.status == "TOOL_FAILURE"
        assert wrong_executor.error_code == wrong_run.error_code == "PERMISSION_DENIED"
        denied_put = service.artifact_put("risk-prosecutor", "WO-I", data["run_id"], data["trace_id"], {})
        assert denied_put["ok"] is False
        assert denied_put["error"]["code"] == "PERMISSION_DENIED"


def test_risk_and_counter_reject_each_others_artifacts_before_submission() -> None:
    evidence = load("fixtures/schema_examples/valid.json")["EvidenceBundle"]
    policy = load("fixtures/policies/baseline_policy.json")
    snapshot = {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001", "value": {"snapshot": load("fixtures/policies/policy_snapshot.json"), "policies": [policy]}}
    cases = [("risk-prosecutor", "COUNTER_ARGUMENT"), ("counter-reviewer", "RISK_ARGUMENT")]
    for agent_id, forbidden in cases:
        context = {"aggregate_id": "X", "materials": [{"ref_type": "ARTIFACT", "ref_id": "E", "artifact_type": "EVIDENCE_BUNDLE", "value": evidence}, snapshot, {"ref_type": "ARTIFACT", "ref_id": "F", "artifact_type": forbidden, "value": {}}]}
        result = FixedAgentRunner(FakeGateway(agent_id, context=context)).run(agent_id, "WO-X", "RUN-X", "TRACE-X")
        assert result.status == "TOOL_FAILURE"
        assert result.error_code == "PERMISSION_DENIED"


def test_independent_judge_waits_for_both_accepted_arguments() -> None:
    examples = load("fixtures/schema_examples/valid.json")
    policy = load("fixtures/policies/baseline_policy.json")
    materials = [
        {"ref_type": "ARTIFACT", "ref_id": "E", "artifact_type": "EVIDENCE_BUNDLE", "value": examples["EvidenceBundle"]},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001", "value": {"snapshot": {}, "policies": [policy]}},
        {"ref_type": "ARTIFACT", "ref_id": "R", "artifact_type": "RISK_ARGUMENT", "value": examples["RiskArgument"]},
    ]
    result = FixedAgentRunner(FakeGateway("independent-judge", context={"aggregate_id": "X", "materials": materials})).run("independent-judge", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "MISSING_INFORMATION"
    assert "both accepted" in result.message


def test_appeal_agent_rejects_live_case_material_even_if_a_bad_gateway_exposes_it() -> None:
    context = {"aggregate_id": "X", "materials": [{"ref_type": "CASE", "ref_id": "CASE-LIVE", "value": {}}]}
    result = FixedAgentRunner(FakeGateway("appeal-reviewer", context=context)).run("appeal-reviewer", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "TOOL_FAILURE"
    assert result.error_code == "PERMISSION_DENIED"


def test_investigator_keeps_fact_inference_missing_and_tool_failure_separate() -> None:
    agent_id = "case-investigator"
    gateway = FakeGateway(agent_id, context={"aggregate_id": "X", "materials": [{"ref_type": "CASE", "ref_id": "X", "value": {}}]})
    gateway.spec = AGENT_SPECS[agent_id]
    original_get = gateway.work_order_get

    def work_with_two_refs(*args):
        result = original_get(*args)
        result["data"]["input_refs"] = [{"ref_type": "CASE", "ref_id": "X"}, {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"}, {"ref_type": "EVIDENCE", "ref_id": "E-TOOL-FAILURE"}]
        return result

    gateway.work_order_get = work_with_two_refs  # type: ignore[method-assign]

    def mixed_search(_consumer, _wo, _run, _trace, _query, evidence_id):
        if evidence_id == "E-TOOL-FAILURE":
            return {"ok": False, "data": None, "error": {"code": "TIMEOUT", "message": "转写超时"}}
        data = load("fixtures/appeal/new_evidence.json")
        data["observable_facts"] = [
            {"fact_key": "ADULT_SUPERVISION", "value": True, "statement": "安全方案记录了成人全程监督。"},
            {"fact_key": "EDUCATIONAL_CONTEXT", "value": True, "statement": "安全方案记录了教学实验目的。"},
        ]
        return {"ok": True, "data": data, "error": None}

    gateway.evidence_search = mixed_search  # type: ignore[method-assign]
    captured: dict[str, Any] = {}

    def accept(_consumer, _wo, _run, _trace, artifact):
        captured.update(copy.deepcopy(artifact))
        return {"ok": True, "data": {"artifact_id": artifact["artifact_id"]}, "error": None}

    gateway.artifact_put = accept  # type: ignore[method-assign]
    result = FixedAgentRunner(gateway).run(agent_id, "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "SUCCESS"
    payload = captured["payload"]
    assert payload["observable_facts"] and all(row["record_type"] == "OBSERVED_FACT" for row in payload["observable_facts"])
    assert payload["agent_inferences"] and all(row["record_type"] == "AGENT_INFERENCE" for row in payload["agent_inferences"])
    assert payload["tool_failures"][0]["error_code"] == "TIMEOUT"
    assert payload["missing_information"] == []


def test_policy_author_rejects_scoring_and_non_policy_attribution_stops_drafting() -> None:
    policy = load("fixtures/policies/baseline_policy.json")
    attribution = {"report_id": "AR-X", "attribution": "EVIDENCE_GAP", "findings": ["证据不足。"], "appeal_decision_id": "AD-X", "policy_change_recommended": False}
    materials = [
        {"ref_type": "ARTIFACT", "ref_id": "AR", "artifact_type": "ATTRIBUTION_REPORT", "value": attribution},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001", "value": {"snapshot": {}, "policies": [policy]}},
    ]
    result = FixedAgentRunner(FakeGateway("policy-author", context={"aggregate_id": "X", "materials": materials})).run("policy-author", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "MISSING_INFORMATION"
    assert "closed" in result.message

    policy_issue = copy.deepcopy(attribution)
    policy_issue.update(attribution="POLICY_GAP", policy_change_recommended=True)
    scoring = {"ref_type": "REPLAY_DATASET", "ref_id": "v1", "value": {"samples": [{"split": "SCORING"}]}}
    materials[0]["value"] = policy_issue
    materials.append(scoring)
    result = FixedAgentRunner(FakeGateway("policy-author", context={"aggregate_id": "X", "materials": materials})).run("policy-author", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "TOOL_FAILURE"
    assert result.error_code == "PERMISSION_DENIED"


def test_attributor_can_conclude_non_policy_issue_and_policy_author_then_refuses_draft() -> None:
    runner = FixedAgentRunner(FakeGateway("case-attributor"))
    payload = runner._build_case_attributor(  # direct fixed-input contract check
        {"run_id": "RUN-NON-POLICY"},
        [{"ref_type": "ARTIFACT", "ref_id": "AD", "artifact_type": "APPEAL_DECISION", "value": {"appeal_decision_id": "AD-001", "outcome": "OVERTURN", "conclusions": [{"statement": "新增证据补齐了原案材料缺口。"}]}}],
    )
    report = AttributionReport.model_validate(payload)
    assert report.attribution == "EVIDENCE_GAP"
    assert report.policy_change_recommended is False

    policy = load("fixtures/policies/baseline_policy.json")
    materials = [
        {"ref_type": "ARTIFACT", "ref_id": "AR", "artifact_type": "ATTRIBUTION_REPORT", "value": report.model_dump(mode="json")},
        {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001", "value": {"snapshot": {}, "policies": [policy]}},
    ]
    result = FixedAgentRunner(FakeGateway("policy-author", context={"aggregate_id": "X", "materials": materials})).run("policy-author", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "MISSING_INFORMATION"
    assert result.artifact is None


def test_replay_agent_does_not_mutate_python_case_results_or_labels() -> None:
    examples = load("fixtures/schema_examples/valid.json")
    proposal = examples["PolicyProposal"]
    replay_data = {
        "replay_id": "REPLAY-RUN-X",
        "proposal_artifact_id": "ART-P",
        "baseline_policy": {"policy_id": proposal["base_policy"]["policy_id"], "version": proposal["base_policy"]["version"]},
        "candidate_policy": {"policy_id": proposal["candidate_policy"]["policy_id"], "version": proposal["candidate_policy"]["version"]},
        "dataset_version": "v1",
        "dataset_manifest_hash": examples["ReplayReport"]["dataset_manifest_hash"],
        "metrics": copy.deepcopy(examples["ReplayReport"]["metrics"]),
        "case_results": [{"sample_id": "S-1", "expected_outcome": "NO_VIOLATION", "baseline_outcome": "VIOLATION", "candidate_outcome": "NO_VIOLATION"}],
        "recommendation": "PASS",
    }
    original = copy.deepcopy(replay_data)
    context = {"aggregate_id": "X", "materials": [
        {"ref_type": "ARTIFACT", "ref_id": "ART-P", "artifact_type": "POLICY_PROPOSAL", "value": proposal},
        {"ref_type": "REPLAY_DATASET", "ref_id": "v1", "value": {"dataset_version": "v1", "samples": []}},
    ]}
    gateway = FakeGateway("replay-analyst", context=context)
    gateway.replay_execute = lambda *_args: {"ok": True, "data": replay_data, "error": None}  # type: ignore[method-assign]
    captured: dict[str, Any] = {}
    gateway.artifact_put = lambda _c, _w, _r, _t, artifact: captured.update(copy.deepcopy(artifact)) or {"ok": True, "data": {"artifact_id": artifact["artifact_id"]}, "error": None}  # type: ignore[method-assign]
    result = FixedAgentRunner(gateway).run("replay-analyst", "WO-X", "RUN-X", "TRACE-X")
    assert result.status == "SUCCESS"
    assert replay_data == original
    assert replay_data["case_results"] == original["case_results"]
    assert captured["payload"]["metrics"] == original["metrics"]
    assert captured["payload"]["recommendation"] == original["recommendation"]
