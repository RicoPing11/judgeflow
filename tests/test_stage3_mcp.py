from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.core import JudgeFlowKernel, canonical_hash, policy_hash_value
from app.mcp import JudgeFlowMCPService, create_mcp_server
from app.models.database import Appeal, Artifact, Base, Case, EvolutionStatus, PolicyEvolution, PolicyRow, ReplayRun


ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-08-12T09:00:00+08:00"


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_element: JSONB, _compiler: Any, **_kw: Any) -> str:
    return "JSON"


def load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as value:
        yield value


def work(
    work_order_id: str,
    aggregate_type: str,
    aggregate_id: str,
    step: str,
    assignee: str,
    artifact_type: str,
    refs: list[dict[str, str]],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "work_order_id": work_order_id,
        "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id,
        "step_type": step,
        "assignee_id": assignee,
        "input_refs": refs,
        "expected_artifact_type": artifact_type,
        "run_id": run_id or f"RUN-{work_order_id}",
        "trace_id": "TRACE-STAGE3",
        "status": "PENDING",
        "retry_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }


def envelope(work_data: dict[str, Any], payload: dict[str, Any], artifact_id: str = "ART-STAGE3") -> dict[str, Any]:
    return {
        "artifact_id": artifact_id,
        "artifact_type": work_data["expected_artifact_type"],
        "schema_version": "1.0",
        "aggregate_type": work_data["aggregate_type"],
        "aggregate_id": work_data["aggregate_id"],
        "work_order_id": work_data["work_order_id"],
        "run_id": work_data["run_id"],
        "trace_id": work_data["trace_id"],
        "producer_type": "AGENT",
        "producer_id": work_data["assignee_id"],
        "payload": payload,
        "content_hash": canonical_hash(payload),
        "created_at": NOW,
    }


def seed_case(kernel: JudgeFlowKernel) -> None:
    kernel.add_policy(load("fixtures/policies/baseline_policy.json"))
    kernel.create_case(
        case_id="CASE-001",
        demo_run_id="DEMO-STAGE3",
        input_json=load("fixtures/case/main_case.json"),
        policy_snapshot=load("fixtures/policies/policy_snapshot.json"),
    )
    kernel.advance_case("CASE-001", "NEW", "INVESTIGATING")


def call(service: JudgeFlowMCPService, method: str, work_data: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return getattr(service, method)(
        consumer=work_data["assignee_id"],
        work_order_id=work_data["work_order_id"],
        run_id=work_data["run_id"],
        trace_id=work_data["trace_id"],
        **extra,
    )


def test_fastmcp_registers_exactly_five_locally_callable_tools(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    data = work(
        "WO-FASTMCP", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE",
        [{"ref_type": "CASE", "ref_id": "CASE-001"}],
    )
    kernel.create_work_order(data)
    session.commit()
    service = JudgeFlowMCPService(session)
    server = create_mcp_server(service, "investigator")
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "work_order.get",
        "context.get",
        "evidence.search",
        "artifact.put",
        "replay.execute",
    }
    assert all("consumer" not in tool.inputSchema.get("properties", {}) for tool in tools)
    common = {"work_order_id": "WO-ABSENT", "run_id": "RUN", "trace_id": "TRACE"}
    # Invoke every handler through FastMCP itself (not merely the Python service methods).
    calls = {
        "work_order.get": common,
        "context.get": common,
        "evidence.search": {**common, "query_type": "CONTENT", "evidence_id": "E-ABSENT"},
        "artifact.put": {**common, "artifact": {}},
        "replay.execute": {
            **common,
            "replay_id": "REPLAY-ABSENT",
            "proposal_artifact_id": "ART-ABSENT",
            "dataset_version": "v1",
        },
    }
    for tool_name, arguments in calls.items():
        _content, result = asyncio.run(server.call_tool(tool_name, arguments))
        assert result["ok"] is False and result["error"]["code"] == "NOT_FOUND"
    success_args = {"work_order_id": data["work_order_id"], "run_id": data["run_id"], "trace_id": data["trace_id"]}
    for tool_name in ("work_order.get", "context.get"):
        _content, result = asyncio.run(server.call_tool(tool_name, success_args))
        assert result["ok"] is True
    intruder = create_mcp_server(service, "another-agent")
    _content, denied = asyncio.run(intruder.call_tool("work_order.get", success_args))
    assert denied["error"]["code"] == "PERMISSION_DENIED"


def test_work_order_and_context_require_exact_execution_identity(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    data = work(
        "WO-CONTEXT", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE",
        [{"ref_type": "CASE", "ref_id": "CASE-001"}, {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"}],
    )
    kernel.create_work_order(data)
    service = JudgeFlowMCPService(session)
    assert call(service, "work_order_get", data)["ok"]
    assert call(service, "context_get", data)["ok"]
    denied = service.context_get("another-agent", data["work_order_id"], data["run_id"], data["trace_id"])
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    denied_run = service.work_order_get("investigator", data["work_order_id"], "WRONG-RUN", data["trace_id"])
    assert denied_run["error"]["code"] == "PERMISSION_DENIED"


def test_appeal_context_is_snapshot_plus_whitelist_and_cannot_cross_it(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    # Minimal setup: stage-two kernel requires a decided case before freezing an appeal.
    case = session.get(__import__("app.models.database", fromlist=["Case"]).Case, "CASE-001")
    case.status = "DECIDED"
    request = load("fixtures/appeal/appeal_request.json")
    appeal = kernel.create_appeal(
        appeal_id="APPEAL-001", case_id="CASE-001", request_json=request,
        original_case_snapshot_json={
            "case_id": "CASE-001", "decision_id": "DR-001", "evidence_ids": ["E-001"],
            "policy_snapshot": load("fixtures/policies/policy_snapshot.json"),
        },
        allowed_evidence_ids=["E-APPEAL-001"],
    )
    appeal.status = "DECIDED"
    data = work(
        "WO-APPEAL", "APPEAL", "APPEAL-001", "APPEAL_REVIEW", "appeal-reviewer", "APPEAL_DECISION",
        [
            {"ref_type": "APPEAL", "ref_id": "APPEAL-001"},
            {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"},
            {"ref_type": "CASE", "ref_id": "CASE-001"},
            {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
        ],
    )
    kernel.create_work_order(data)
    session.commit()
    service = JudgeFlowMCPService(session)
    context = call(service, "context_get", data)
    assert not context["ok"] and context["error"]["code"] == "PERMISSION_DENIED"
    allowed = call(service, "evidence_search", data, query_type="APPEAL_SUBMISSION", evidence_id="E-APPEAL-001")
    denied = call(service, "evidence_search", data, query_type="CONTENT", evidence_id="E-001")
    assert allowed["ok"] and denied["error"]["code"] == "PERMISSION_DENIED"
    good = work(
        "WO-APPEAL-GOOD", "APPEAL", "APPEAL-001", "APPEAL_REVIEW", "appeal-reviewer", "APPEAL_DECISION",
        [{"ref_type": "APPEAL", "ref_id": "APPEAL-001"}, {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"}],
    )
    kernel.create_work_order(good)
    good_context = call(service, "context_get", good)
    assert good_context["ok"]
    appeal_value = next(item["value"] for item in good_context["data"]["materials"] if item["ref_type"] == "APPEAL")
    assert set(appeal_value) == {"request", "original_case_snapshot", "allowed_evidence_ids", "locked_policies"}
    assert appeal_value["allowed_evidence_ids"] == ["E-APPEAL-001"]
    assert appeal_value["locked_policies"][0]["version"] == "1.0"
    assert appeal_value["locked_policies"][0]["required_elements"]
    assert appeal_value["locked_policies"][0]["exceptions"]


def test_policy_author_context_never_exposes_scoring(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    case = session.get(Case, "CASE-001")
    case.status = "DECIDED"
    request = load("fixtures/appeal/appeal_request.json")
    kernel.create_appeal(
        appeal_id="APPEAL-POLICY", case_id="CASE-001", request_json=request,
        original_case_snapshot_json={"case_id": "CASE-001", "decision_id": "DR-001"},
        allowed_evidence_ids=["E-APPEAL-001"],
    )
    session.add(PolicyEvolution(
        evolution_id="EV-1", source_appeal_id="APPEAL-POLICY", status=EvolutionStatus.DRAFTING,
        state_version=0, base_policy_id="MINOR_DANGEROUS_ACT", base_policy_version="1.0",
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    ))
    session.flush()
    data = work(
        "WO-DRAFT", "POLICY_EVOLUTION", "EV-1", "POLICY_DRAFTING", "policy-author", "POLICY_PROPOSAL",
        [{"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"}, {"ref_type": "REPLAY_DATASET", "ref_id": "v1"}],
    )
    kernel.create_work_order(data)
    result = call(JudgeFlowMCPService(session), "context_get", data)
    dataset = next(item["value"] for item in result["data"]["materials"] if item["ref_type"] == "REPLAY_DATASET")
    policy_context = next(item["value"] for item in result["data"]["materials"] if item["ref_type"] == "POLICY_SNAPSHOT")
    samples = dataset["samples"]
    assert {item["split"] for item in samples} == {"TRIGGER", "DRAFTING"}
    assert policy_context["policies"][0]["required_elements"]
    assert policy_context["policies"][0]["exceptions"]
    assert "SCORING" not in json.dumps(result)


def test_fixed_evidence_distinguishes_found_missing_not_found_and_tool_failure(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    ids = ["E-APPEAL-001", "E-INCOMPLETE", "E-ABSENT", "E-TOOL-FAILURE"]
    data = work(
        "WO-EVIDENCE", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE",
        [{"ref_type": "EVIDENCE", "ref_id": item} for item in ids],
    )
    kernel.create_work_order(data)
    session.commit()
    service = JudgeFlowMCPService(session)
    server = create_mcp_server(service, "investigator")
    _content, found = asyncio.run(server.call_tool("evidence.search", {
        "work_order_id": data["work_order_id"], "run_id": data["run_id"], "trace_id": data["trace_id"],
        "query_type": "APPEAL_SUBMISSION", "evidence_id": ids[0],
    }))
    missing = call(service, "evidence_search", data, query_type="CONTENT", evidence_id=ids[1])
    absent = call(service, "evidence_search", data, query_type="CONTENT", evidence_id=ids[2])
    failed = call(service, "evidence_search", data, query_type="TRANSCRIPT", evidence_id=ids[3])
    assert found["ok"]
    assert [missing["error"]["code"], absent["error"]["code"], failed["error"]["code"]] == [
        "INCOMPLETE_DATA", "NOT_FOUND", "TIMEOUT"
    ]


def test_artifact_put_delegates_schema_execution_type_idempotency_and_conflict(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    data = work(
        "WO-PUT", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE",
        [{"ref_type": "CASE", "ref_id": "CASE-001"}],
    )
    kernel.create_work_order(data)
    session.commit()
    service = JudgeFlowMCPService(session)
    payload = load("fixtures/schema_examples/valid.json")["EvidenceBundle"]
    artifact = envelope(data, payload)
    bad_schema = copy.deepcopy(artifact)
    del bad_schema["payload"]["bundle_id"]
    bad_schema["content_hash"] = canonical_hash(bad_schema["payload"])
    assert call(service, "artifact_put", data, artifact=bad_schema)["error"]["code"] == "VALIDATION_ERROR"
    assert session.get(Artifact, "ART-STAGE3") is None
    bad_type = copy.deepcopy(artifact)
    bad_type["artifact_type"] = "RISK_ARGUMENT"
    assert call(service, "artifact_put", data, artifact=bad_type)["error"]["code"] == "VALIDATION_ERROR"
    bad_work = copy.deepcopy(artifact)
    bad_work["work_order_id"] = "WO-OTHER"
    assert call(service, "artifact_put", data, artifact=bad_work)["error"]["code"] == "VALIDATION_ERROR"
    server = create_mcp_server(service, "investigator")
    _content, first = asyncio.run(server.call_tool("artifact.put", {
        "work_order_id": data["work_order_id"], "run_id": data["run_id"], "trace_id": data["trace_id"],
        "artifact": artifact,
    }))
    second_artifact = copy.deepcopy(artifact)
    second_artifact["artifact_id"] = "ART-DIFFERENT-ID"
    second = call(service, "artifact_put", data, artifact=second_artifact)
    conflict_artifact = copy.deepcopy(artifact)
    conflict_artifact["payload"]["missing_information"] = ["different"]
    conflict_artifact["content_hash"] = canonical_hash(conflict_artifact["payload"])
    conflict = call(service, "artifact_put", data, artifact=conflict_artifact)
    assert first["ok"] and first["data"]["artifact_id"] == "ART-STAGE3"
    with Session(session.bind) as observer:
        assert observer.get(Artifact, "ART-STAGE3") is not None
    assert second["ok"] and second["data"] == {"artifact_id": "ART-STAGE3", "idempotent": True}
    assert conflict["error"]["code"] == "CONFLICT"

    for field, bad_value in [("run_id", "WRONG"), ("trace_id", "WRONG"), ("producer_id", "other")]:
        bad = copy.deepcopy(artifact)
        bad[field] = bad_value
        result = call(service, "artifact_put", data, artifact=bad)
        assert result["error"]["code"] in {"VALIDATION_ERROR", "PERMISSION_DENIED"}


def test_artifact_put_materializes_work_order_bound_envelope_from_payload(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    data = work(
        "WO-COMPACT", "CASE", "CASE-001", "INVESTIGATION", "investigator", "EVIDENCE_BUNDLE",
        [{"ref_type": "CASE", "ref_id": "CASE-001"}],
    )
    kernel.create_work_order(data)
    session.commit()
    payload = load("fixtures/schema_examples/valid.json")["EvidenceBundle"]

    result = call(JudgeFlowMCPService(session), "artifact_put", data, artifact=payload)

    assert result["ok"] and result["data"]["artifact_id"] == "ART-WO-COMPACT"
    row = session.get(Artifact, "ART-WO-COMPACT")
    assert row is not None
    assert row.work_order_id == data["work_order_id"]
    assert row.run_id == data["run_id"] and row.trace_id == data["trace_id"]
    assert row.producer_id == data["assignee_id"]
    assert row.content_hash == canonical_hash(payload)


def test_compact_policy_proposal_derives_candidate_hash_before_kernel_validation(session: Session) -> None:
    kernel = JudgeFlowKernel(session)
    seed_case(kernel)
    case = session.get(Case, "CASE-001")
    assert case is not None
    case.status = "DECIDED"
    request = load("fixtures/appeal/appeal_request.json")
    appeal = kernel.create_appeal(
        appeal_id="APPEAL-COMPACT-POLICY",
        case_id=case.case_id,
        request_json=request,
        original_case_snapshot_json={"case_id": case.case_id, "decision_id": "DR-COMPACT"},
        allowed_evidence_ids=["E-APPEAL-001"],
    )
    appeal.status = "DECIDED"
    evolution = kernel.create_policy_evolution(
        evolution_id="EV-COMPACT-POLICY",
        source_appeal_id="APPEAL-COMPACT-POLICY",
        base_policy_id="MINOR_DANGEROUS_ACT",
        base_policy_version="1.0",
    )
    evolution.status = "DRAFTING"
    data = work(
        "WO-COMPACT-POLICY", "POLICY_EVOLUTION", evolution.evolution_id,
        "POLICY_DRAFTING", "policy-author", "POLICY_PROPOSAL",
        [{"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"}],
    )
    kernel.create_work_order(data)
    session.commit()
    payload = copy.deepcopy(load("fixtures/schema_examples/valid.json")["PolicyProposal"])
    payload["candidate_policy"]["version"] = "2.0-compact-test"
    payload["candidate_policy"].pop("content_hash")

    result = call(JudgeFlowMCPService(session), "artifact_put", data, artifact=payload)

    assert result["ok"]
    row = session.get(Artifact, "ART-WO-COMPACT-POLICY")
    assert row is not None
    assert row.payload["candidate_policy"]["content_hash"] == policy_hash_value(
        row.payload["candidate_policy"]
    )


def test_replay_execute_uses_fixed_python_and_is_repeatable(session: Session) -> None:
    baseline = load("fixtures/policies/baseline_policy.json")
    proposal = load("fixtures/schema_examples/valid.json")["PolicyProposal"]
    now = datetime.now(UTC)
    evolution = PolicyEvolution(
        evolution_id="EV-REPLAY", source_appeal_id="APPEAL-FIXTURE", status=EvolutionStatus.REPLAYING,
        state_version=0, base_policy_id=baseline["policy_id"], base_policy_version=baseline["version"],
        current_proposal_artifact_id="ART-PROPOSAL", created_at=now, updated_at=now,
    )
    session.add(evolution)
    session.add(PolicyRow(
        policy_id=baseline["policy_id"], version=baseline["version"], status="BASELINE", title=baseline["title"],
        description=baseline["description"], dsl_json=baseline, content_hash=baseline["content_hash"], created_at=now,
    ))
    candidate = proposal["candidate_policy"]
    session.add(PolicyRow(
        policy_id=candidate["policy_id"], version=candidate["version"], status="CANDIDATE", title=candidate["title"],
        description=candidate["description"], dsl_json=candidate, content_hash=candidate["content_hash"],
        source_proposal_artifact_id="ART-PROPOSAL", created_at=now,
    ))
    session.add(Artifact(
        artifact_id="ART-PROPOSAL", aggregate_type="POLICY_EVOLUTION", aggregate_id="EV-REPLAY",
        work_order_id="WO-PLACEHOLDER", run_id="RUN-PLACEHOLDER", trace_id="TRACE-STAGE3", producer_type="AGENT",
        producer_id="policy-author", artifact_type="POLICY_PROPOSAL", schema_version="1.0", payload=proposal,
        content_hash=canonical_hash(proposal), created_at=now,
    ))
    # SQLite tests do not enforce the PostgreSQL FK; the fixed demo schema remains unchanged.
    session.flush()
    data = work(
        "WO-REPLAY", "POLICY_EVOLUTION", "EV-REPLAY", "REPLAY", "replay-analyst", "REPLAY_REPORT",
        [{"ref_type": "ARTIFACT", "ref_id": "ART-PROPOSAL"}, {"ref_type": "REPLAY_DATASET", "ref_id": "v1"}],
    )
    JudgeFlowKernel(session).create_work_order(data)
    session.commit()
    service = JudgeFlowMCPService(session)
    server = create_mcp_server(service, "replay-analyst")
    _content, first = asyncio.run(server.call_tool("replay.execute", {
        "work_order_id": data["work_order_id"], "run_id": data["run_id"], "trace_id": data["trace_id"],
        "replay_id": "REPLAY-A", "proposal_artifact_id": "ART-PROPOSAL", "dataset_version": "v1",
    }))
    second = call(service, "replay_execute", data, replay_id="REPLAY-B", proposal_artifact_id="ART-PROPOSAL", dataset_version="v1")
    # Repeat against a clean equivalent database is already guaranteed by replay_policies; here compare direct fixed computation.
    from app.replay import replay_policies
    expected = replay_policies(baseline, candidate, load("fixtures/replay/v1/dataset.json"))
    assert first["ok"] and second["ok"]
    with Session(session.bind) as observer:
        assert observer.get(ReplayRun, "REPLAY-A") is not None
        assert observer.get(ReplayRun, "REPLAY-B") is not None
    assert {key: value for key, value in first["data"].items() if key != "replay_id"} == {
        key: value for key, value in second["data"].items() if key != "replay_id"
    }
    assert first["data"]["metrics"] == expected["metrics"]
    assert first["data"]["case_results"] == expected["case_results"]
    assert first["data"]["recommendation"] == expected["recommendation"]
