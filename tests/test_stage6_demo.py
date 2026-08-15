from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.demo import DemoService
from app.models.database import Artifact, Base, Case, PolicyEvolution, PolicyRow, WorkOrderRow


ROOT = Path(__file__).resolve().parents[1]


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(_element: JSONB, _compiler: Any, **_kw: Any) -> str:
    return "JSON"


def test_stage6_three_isolated_complete_runs_are_idempotent_and_keep_baseline() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for number in range(1, 4):
            demo_run_id = f"DEMO-STAGE6-TEST-{number:03d}"
            service = DemoService(session)
            before = service.start(demo_run_id)
            assert before["case"]["status"] == "DECIDED"
            assert before["replay"] is not None
            assert before["non_policy_branch"] == {
                "evolution_id": f"EVOLUTION-STAGE6-TEST-{number:03d}-NP",
                "status": "CLOSED",
                "attribution": "EVIDENCE_GAP",
                "downstream_count": 0,
            }
            after = service.approve(demo_run_id)
            assert next(item for item in after["evolutions"] if not item["evolution_id"].endswith("-NP"))["status"] == "APPROVED"
            assert after["artifacts"]["HUMAN_APPROVAL"]["payload"]["decision"] == "APPROVE"
            assert after["policy"]["published"] is False
            counts = (after["execution_evidence"]["work_order_count"], after["execution_evidence"]["artifact_count"])
            assert counts == (11, 11)
            assert len(after["artifact_list"]) == 11
            assert service.start(demo_run_id)["execution_evidence"]["artifact_count"] == counts[1]
            assert service.approve(demo_run_id)["execution_evidence"]["artifact_count"] == counts[1]
        baseline = session.get(PolicyRow, ("MINOR_DANGEROUS_ACT", "1.0"))
        assert baseline is not None and baseline.status.value == "BASELINE"
    engine.dispose()


def test_stage6_failure_is_explicit_and_does_not_create_artifact() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        result = DemoService(session).create_failure("DEMO-STAGE6-FAILURE-TEST")
        assert result["state"] == "FAILURE"
        assert result["case_status"] == "INVESTIGATING"
        assert result["work_order"]["display_state"] == "FAILURE"
        assert result["work_order"]["error_code"] == "TIMEOUT"
        assert session.scalar(select(func.count()).select_from(Artifact)) == 0
        assert DemoService(session).create_failure("DEMO-STAGE6-FAILURE-TEST") == result
    engine.dispose()


def test_stage6_page_has_required_fixed_demo_surfaces() -> None:
    html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")
    for text in (
        "调查证据", "风险意见", "反证意见", "独立裁决", "原裁决快照", "申诉新增证据",
        "申诉结果", "规则问题归因", "非规则反例", "候选规则 Diff", "确定性回放",
        "Human 审批结果", "WorkOrder", "run_id", "trace_id", "候选规则未发布",
    ):
        assert text in html
    assert all(state in html + javascript for state in ("未开始", "运行中", "成功", "执行失败", "业务无数据"))
    assert "fetch(" in javascript
    assert "WebSocket" not in javascript and "postgres" not in javascript.lower() and "matrix" not in javascript.lower()


def test_stage6_only_uses_existing_eight_tables() -> None:
    assert len(Base.metadata.tables) == 8
    assert {model.__tablename__ for model in (Case, PolicyEvolution, WorkOrderRow, Artifact)} <= set(Base.metadata.tables)


def test_product_read_models_are_real_paginated_and_do_not_write() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        service = DemoService(session)
        run_id = "DEMO-STAGE6-PRODUCT-001"
        run = service.start(run_id)
        before = (run["execution_evidence"]["work_order_count"], run["execution_evidence"]["artifact_count"])

        overview = service.overview()
        assert overview["source"] == "REAL"
        assert overview["chains"]["adjudication"] == [
            "case-investigator", "risk-prosecutor", "counter-reviewer", "independent-judge",
        ]
        assert all(agent["input_tokens"] is None for agent in overview["agents"])
        assert all(agent["output_tokens"] is None for agent in overview["agents"])
        assert all("average_task_duration_ms" in agent for agent in overview["agents"])
        assert overview["business"]["decision_series"]

        cases = service.list_cases(q="PRODUCT", policy_id="MINOR_DANGEROUS_ACT", page=1, page_size=20)
        assert cases["pagination"]["total"] == 1
        assert cases["items"][0]["confidence"] is None
        case = service.case_detail("CASE-STAGE6-PRODUCT-001")
        assert case["has_appeal"] is True and case["has_policy_evolution"] is True
        risk = next(item for item in case["flow"] if item["step_type"] == "RISK_ARGUMENT")
        counter = next(item for item in case["flow"] if item["step_type"] == "COUNTER_ARGUMENT")
        assert risk["work_order_id"] != counter["work_order_id"]
        evidence_evolution = next(item for item in case["evolutions"] if item["attribution"] == "EVIDENCE_GAP")
        assert evidence_evolution["source_appeal_id"].endswith("-NP")
        assert not any(
            item["aggregate_id"] == evidence_evolution["evolution_id"]
            and item["step_type"] in {"POLICY_DRAFTING", "REPLAY"}
            for item in case["flow"]
        )

        policies = service.list_policies(q="未成年人", status="BASELINE")
        assert policies["pagination"]["total"] == 1
        policy = service.policy_detail("MINOR_DANGEROUS_ACT")
        assert policy["citation_count"] == 1
        assert policy["usage"]["citation_count"] == 1
        assert any(item["status"] == "BASELINE" for item in policy["versions"])
        candidate = next(item for item in policy["versions"] if item["status"] == "CANDIDATE")
        assert candidate["changes"]

        approvals = service.list_approvals()
        assert approvals["pagination"]["total"] == 1
        approval = approvals["items"][0]
        assert approval["approval_actionable"] is True
        assert approval["demo_run_id"] == run_id
        assert approval["base_policy"]["status"] == "BASELINE"
        assert approval["appeal_reason"]
        impact = approval["replay_impact"]
        assert impact["total"] == impact["unaffected"] + impact["affected"]
        assert impact["affected"] == impact["lighter"] + impact["heavier"]
        replay = service.replay_detail(approval["replay_id"])
        assert replay["time_range"] is None
        assert replay["pagination"]["total"] == 3
        assert replay["context"]["source_case_id"] == "CASE-STAGE6-PRODUCT-001"

        after = service.view(run_id)["execution_evidence"]
        assert (after["work_order_count"], after["artifact_count"]) == before

        case_row = session.get(Case, "CASE-STAGE6-PRODUCT-001")
        assert case_row is not None
        case_row.demo_run_id = "DEMO-PG1"
        session.flush()
        historical = service.list_approvals()["items"][0]
        assert historical["approval_actionable"] is False
        assert historical["demo_run_id"] is None
    engine.dispose()
