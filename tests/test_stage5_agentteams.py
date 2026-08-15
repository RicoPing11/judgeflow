from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path

import pytest
from mcp.server.fastmcp import Context
from mcp.shared.context import RequestContext
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.agentteams.contracts import BUSINESS_ROOMS, COORDINATOR_SPECS, MatrixIDMessage, route_target
from app.agentteams.mcp_http import _identity, create_http_mcp_server
from app.agents.specs import AGENT_SPECS


ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "agentteams" / "resources"


def _resource(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _message(**overrides) -> dict:
    data = {
        "message_type": "WORK_ORDER_ASSIGNED",
        "work_order_id": "WO-S5-I",
        "aggregate_type": "CASE",
        "aggregate_id": "CASE-S5",
        "step_type": "INVESTIGATION",
        "assignee": "case-investigator",
        "run_id": "RUN-S5-I",
        "trace_id": "TRACE-S5",
        "status": "PENDING",
    }
    data.update(overrides)
    return data


def test_exact_native_agentteams_resource_set_and_lifecycle() -> None:
    manager = _resource(RESOURCES / "manager.yaml")
    human = _resource(RESOURCES / "human-policy-owner.yaml")
    workers = [_resource(path) for path in sorted((RESOURCES / "workers").glob("*.yaml"))]
    teams = [_resource(RESOURCES / name) for name in ("team-adjudication.yaml", "team-policy.yaml")]

    assert manager["apiVersion"] == "agentteams.io/v1beta1"
    assert manager["kind"] == "Manager"
    assert manager["metadata"]["name"] == "judgeflow-manager"
    assert manager["spec"]["image"].endswith("agentteams-manager-copaw:v1.2.0")
    assert human["kind"] == "Human" and human["metadata"]["name"] == "policy-owner"
    assert len(workers) == 10
    assert {item["metadata"]["name"] for item in workers} == set(AGENT_SPECS) | {
        "adjudication-team-leader",
        "policy-evolution-team-leader",
    }
    assert all(item["spec"]["state"] == "Sleeping" for item in workers)
    assert all(item["spec"]["image"] == "judgeflow-copaw-worker:stage5-v2" for item in workers)
    assert all(item["apiVersion"] == "agentteams.io/v1beta1" and item["kind"] == "Worker" for item in workers)
    assert {item["metadata"]["name"] for item in teams} == {"judgeflow-adjudication", "judgeflow-policy"}
    for team in teams:
        roles = [member["role"] for member in team["spec"]["workerMembers"]]
        assert roles.count("team_leader") == 1

    dockerfile = (ROOT / "agentteams" / "copaw-worker-matrix.Dockerfile").read_text(encoding="utf-8")
    assert "agentteams-copaw-worker:v1.2.0" in dockerfile
    assert "ENV LLM_MAX_CONCURRENT=1" in dockerfile
    assert 'COPAW_WORKING_DIR="${INSTALL_DIR}/${WORKER_NAME}/.copaw"' in dockerfile


def test_three_business_rooms_have_exact_members_and_native_provisioners() -> None:
    rooms = json.loads((ROOT / "fixtures" / "agentteams" / "rooms.json").read_text(encoding="utf-8"))["rooms"]
    assert set(rooms) == set(BUSINESS_ROOMS)
    for name, expected in BUSINESS_ROOMS.items():
        assert tuple(rooms[name]["members"]) == expected
    assert rooms["judgeflow-control"]["provisioner"] == "matrix-client-v3"
    assert rooms["judgeflow-adjudication"]["provisioner"] == "agentteams-team"
    assert rooms["judgeflow-policy"]["provisioner"] == "agentteams-team"


def test_coordinators_have_no_mcp_or_domain_output_authority() -> None:
    resources = [_resource(RESOURCES / "manager.yaml")] + [
        _resource(RESOURCES / "workers" / "adjudication-team-leader.yaml"),
        _resource(RESOURCES / "workers" / "policy-evolution-team-leader.yaml"),
    ]
    assert set(COORDINATOR_SPECS) == {item["metadata"]["name"] for item in resources}
    for item in resources:
        assert "mcpServers" not in item["spec"]
        rules = item["spec"]["agents"]
        assert "不得生成" in rules
        assert "访问数据库" in rules
        assert "推进" in rules


def test_matrix_protocol_rejects_payload_free_text_and_invalid_conditionals() -> None:
    message = MatrixIDMessage.model_validate(_message())
    assert route_target(message) == "adjudication-team-leader"
    assert route_target(MatrixIDMessage.model_validate(_message(aggregate_type="APPEAL"))) == "appeal-reviewer"
    assert route_target(MatrixIDMessage.model_validate(_message(aggregate_type="POLICY_EVOLUTION"))) == "policy-evolution-team-leader"

    for forbidden in ("payload", "case_text", "evidence", "reasoning", "conversation", "scoring_labels"):
        with pytest.raises(ValidationError):
            MatrixIDMessage.model_validate(_message(**{forbidden: "not allowed"}))
    with pytest.raises(ValidationError):
        MatrixIDMessage.model_validate(_message(status="FAILED"))
    with pytest.raises(ValidationError):
        MatrixIDMessage.model_validate(_message(error_code="TIMEOUT"))
    with pytest.raises(ValidationError):
        MatrixIDMessage.model_validate(_message(proposal_artifact_id="ART-P"))


def test_human_reminder_must_bind_proposal_and_replay() -> None:
    reminder = MatrixIDMessage.model_validate(
        _message(
            message_type="HUMAN_APPROVAL_REQUIRED",
            aggregate_type="POLICY_EVOLUTION",
            aggregate_id="EVOLUTION-S5",
            step_type="REPLAY",
            assignee="policy-owner",
            status="AWAITING_APPROVAL",
            proposal_artifact_id="ART-S5-P",
            replay_id="REPLAY-S5",
        )
    )
    assert reminder.proposal_artifact_id == "ART-S5-P" and reminder.replay_id == "REPLAY-S5"
    with pytest.raises(ValidationError):
        MatrixIDMessage.model_validate(reminder.model_dump(exclude={"replay_id"}))


def _context(headers: list[tuple[bytes, bytes]]) -> Context:
    request = Request({"type": "http", "method": "POST", "path": "/mcp", "headers": headers})
    return Context(
        request_context=RequestContext(
            request_id=1,
            meta=None,
            session=None,  # type: ignore[arg-type]
            lifespan_context=None,
            request=request,
        )
    )


def test_http_transport_trusts_native_gateway_consumer_header() -> None:
    ctx = _context([(b"x-mse-consumer", b"worker-case-investigator")])
    assert _identity(ctx) == "case-investigator"
    with pytest.raises(PermissionError):
        _identity(_context([(b"x-mse-consumer", b"manager")]))


def test_streamable_http_adapter_exposes_exactly_existing_five_tools() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with pytest.raises(RuntimeError):
        create_http_mcp_server(lambda: Session(engine))
    server = create_http_mcp_server(lambda: Session(engine), trust_native_gateway=True)
    tools = asyncio.run(server.list_tools())
    assert {tool.name for tool in tools} == {
        "work_order.get",
        "context.get",
        "evidence.search",
        "artifact.put",
        "replay.execute",
    }
    assert all("consumer" not in tool.inputSchema.get("properties", {}) for tool in tools)
    engine.dispose()


def test_domain_workers_use_native_direct_proxy_and_reuse_four_existing_skills() -> None:
    domain_workers = {
        path.stem: _resource(path)
        for path in (RESOURCES / "workers").glob("*.yaml")
        if path.stem in AGENT_SPECS
    }
    assert set(domain_workers) == set(AGENT_SPECS)
    expected_url = "http://aigw-local.agentteams.io:8080/mcp-servers/mcp-judgeflow/mcp"
    assert all(item["spec"]["mcpServers"] == [
        {"name": "judgeflow", "url": expected_url, "transport": "http"}
    ] for item in domain_workers.values())

    expected_skills = {"case-investigation", "case-deliberation", "policy-evolution", "case-replay"}
    package_dir = ROOT / "agentteams" / "packages"
    assert {path.stem for path in package_dir.glob("*.zip")} == expected_skills
    for skill in expected_skills:
        with zipfile.ZipFile(package_dir / f"{skill}.zip") as package:
            names = set(package.namelist())
            assert "manifest.json" in names
            assert f"skills/{skill}/SKILL.md" in names


def test_case_investigation_skill_is_a_bounded_four_step_mcp_flow() -> None:
    text = (ROOT / "skills" / "case-investigation" / "SKILL.md").read_text(encoding="utf-8")
    assert all(tool in text for tool in ("work_order.get", "context.get", "evidence.search", "artifact.put"))
    assert 'query_type="CONTENT"' in text
    assert 'query_type="APPEAL_SUBMISSION"' in text
    assert "禁止试探或枚举其他" in text
    assert "查询返回后立即" in text
    assert "ART-<work_order_id>" in text
