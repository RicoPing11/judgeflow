"""Minimal stage-six backend facade over the existing Kernel and MCP path."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.mcp_gateway import FastMCPGateway
from app.agents.postgres_seed import run_seed
from app.agents.runner import FixedAgentRunner
from app.core import JudgeFlowKernel, canonical_hash
from app.mcp import JudgeFlowMCPService
from app.models.database import Appeal, Artifact, Case, PolicyEvolution, PolicyRow, ReplayRun, WorkOrderRow
from app.models.schemas import HumanApproval


ROOT = Path(__file__).resolve().parents[2]
RUN_ID_PATTERN = re.compile(r"^DEMO-STAGE6-[A-Z0-9-]{1,48}$")


class DemoNotFound(LookupError):
    pass


class DemoConflict(ValueError):
    pass


def _load(path: str) -> dict[str, Any]:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _tag(demo_run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(demo_run_id):
        raise DemoConflict("demo_run_id 必须使用 DEMO-STAGE6-<大写字母、数字或连字符>")
    return demo_run_id.removeprefix("DEMO-")


def _work(tag: str, code: str, aggregate_type: str, aggregate_id: str, step: str,
          assignee: str, output: str, refs: list[dict[str, str]]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "work_order_id": f"WO-{tag}-{code}", "aggregate_type": aggregate_type,
        "aggregate_id": aggregate_id, "step_type": step, "assignee_id": assignee,
        "input_refs": refs, "expected_artifact_type": output,
        "run_id": f"RUN-{tag}-{code}", "trace_id": f"TRACE-{tag}",
        "status": "PENDING", "retry_count": 0, "created_at": now, "updated_at": now,
    }


class DemoService:
    """Only the fixed stage-six demo commands and projection."""

    def __init__(self, session: Session):
        self.session = session
        self.kernel = JudgeFlowKernel(session)
        self.mcp = JudgeFlowMCPService(session)
        self.runner = FixedAgentRunner(FastMCPGateway(self.mcp))

    def start(self, demo_run_id: str) -> dict[str, Any]:
        tag = _tag(demo_run_id)
        case_id = f"CASE-{tag}"
        if self.session.get(Case, case_id) is not None:
            return self.view(demo_run_id)
        run_seed(self.session, tag)
        self._run_non_policy_branch(tag, case_id)
        self.session.commit()
        return self.view(demo_run_id)

    def approve(self, demo_run_id: str) -> dict[str, Any]:
        tag = _tag(demo_run_id)
        evolution = self.session.get(PolicyEvolution, f"EVOLUTION-{tag}")
        if evolution is None:
            raise DemoNotFound("规则问题演进不存在")
        if _value(evolution.status) == "APPROVED":
            return self.view(demo_run_id)
        if _value(evolution.status) != "AWAITING_APPROVAL":
            raise DemoConflict("规则演进尚未到达 Human 审批步骤")
        if not evolution.current_proposal_artifact_id or not evolution.current_replay_id:
            raise DemoConflict("Human 审批缺少已绑定的候选规则或回放")

        data = _work(tag, "H", "POLICY_EVOLUTION", evolution.evolution_id, "REPLAY",
                     "policy-owner", "HUMAN_APPROVAL", [
            {"ref_type": "ARTIFACT", "ref_id": evolution.current_proposal_artifact_id},
            {"ref_type": "REPLAY_DATASET", "ref_id": "v1"},
        ])
        work = self.kernel.create_work_order(data)
        payload = HumanApproval.model_validate({
            "approval_id": f"HA-{tag}", "decision": "APPROVE",
            "proposal_artifact_id": evolution.current_proposal_artifact_id,
            "replay_id": evolution.current_replay_id, "reviewer_id": "policy-owner",
            "comment": "仅批准比赛 Demo 候选版本，不发布生产规则。",
            "created_at": datetime.now(UTC).isoformat(),
        }).model_dump(mode="json")
        envelope = {
            "artifact_id": f"ART-{work.work_order_id}", "artifact_type": "HUMAN_APPROVAL",
            "schema_version": "1.0", "aggregate_type": "POLICY_EVOLUTION",
            "aggregate_id": evolution.evolution_id, "work_order_id": work.work_order_id,
            "run_id": work.run_id, "trace_id": work.trace_id, "producer_type": "HUMAN",
            "producer_id": "policy-owner", "payload": payload,
            "content_hash": canonical_hash(payload), "created_at": datetime.now(UTC).isoformat(),
        }
        self.kernel.record_human_approval(envelope)
        self.session.commit()
        return self.view(demo_run_id)

    def create_failure(self, demo_run_id: str) -> dict[str, Any]:
        tag = _tag(demo_run_id)
        failure_tag = f"{tag}-FAIL"
        case_id = f"CASE-{failure_tag}"
        if self.session.get(Case, case_id) is None:
            baseline = _load("fixtures/policies/baseline_policy.json")
            if self.session.get(PolicyRow, (baseline["policy_id"], baseline["version"])) is None:
                self.kernel.add_policy(baseline)
            case_input = deepcopy(_load("fixtures/case/main_case.json"))
            case_input.update(case_id=case_id, demo_run_id=f"DEMO-{failure_tag}")
            self.kernel.create_case(case_id=case_id, demo_run_id=f"DEMO-{failure_tag}",
                                    input_json=case_input,
                                    policy_snapshot=_load("fixtures/policies/policy_snapshot.json"))
            self.kernel.advance_case(case_id, "NEW", "INVESTIGATING", trace_id=f"TRACE-{failure_tag}")
            data = _work(failure_tag, "I", "CASE", case_id, "INVESTIGATION", "case-investigator",
                         "EVIDENCE_BUNDLE", [
                {"ref_type": "CASE", "ref_id": case_id},
                {"ref_type": "EVIDENCE", "ref_id": "E-TOOL-FAILURE"},
            ])
            work = self.kernel.create_work_order(data)
            result = self.mcp.evidence_search("case-investigator", work.work_order_id, work.run_id,
                                              work.trace_id, "TRANSCRIPT", "E-TOOL-FAILURE")
            error = result.get("error") or {}
            if result.get("ok") or error.get("code") != "TIMEOUT":
                raise DemoConflict("固定失败场景未返回预期 TIMEOUT")
            self.kernel.fail_work_order(work.work_order_id, "TIMEOUT")
            self.session.commit()
        return self._failure_view(f"DEMO-{failure_tag}")

    def view(self, demo_run_id: str) -> dict[str, Any]:
        tag = _tag(demo_run_id)
        case = self.session.get(Case, f"CASE-{tag}")
        if case is None:
            raise DemoNotFound("Demo 运行不存在")
        appeals = list(self.session.scalars(
            select(Appeal).where(Appeal.case_id == case.case_id).order_by(Appeal.appeal_id)
        ))
        appeal_ids = [item.appeal_id for item in appeals]
        evolutions = list(self.session.scalars(
            select(PolicyEvolution)
            .where(PolicyEvolution.source_appeal_id.in_(appeal_ids or ["-"]))
            .order_by(PolicyEvolution.evolution_id)
        ))
        aggregate_ids = [case.case_id, *appeal_ids, *(item.evolution_id for item in evolutions)]
        works = list(self.session.scalars(
            select(WorkOrderRow).where(WorkOrderRow.aggregate_id.in_(aggregate_ids))
            .order_by(WorkOrderRow.created_at, WorkOrderRow.work_order_id)
        ))
        artifacts = list(self.session.scalars(
            select(Artifact).where(Artifact.aggregate_id.in_(aggregate_ids))
            .order_by(Artifact.created_at, Artifact.artifact_id)
        ))
        artifact_by_type: dict[str, Artifact] = {}
        for item in artifacts:
            if item.aggregate_id == case.case_id or not _value(item.artifact_type) in artifact_by_type:
                artifact_by_type[_value(item.artifact_type)] = item
        policy_evolution = next((item for item in evolutions if _value(item.status) != "CLOSED"), None)
        non_policy = next((item for item in evolutions if _value(item.status) == "CLOSED"), None)
        replay = (self.session.get(ReplayRun, policy_evolution.current_replay_id)
                  if policy_evolution and policy_evolution.current_replay_id else None)
        proposal = next((row for row in artifacts if _value(row.artifact_type) == "POLICY_PROPOSAL"), None)
        baseline = self.session.get(PolicyRow, ("MINOR_DANGEROUS_ACT", "1.0"))
        active = next((item for item in works if _value(item.status) in {"PENDING", "RUNNING"}), None)
        return {
            "ok": True, "state": "SUCCESS" if not active else _value(active.status),
            "demo_run_id": demo_run_id,
            "case": {
                "case_id": case.case_id, "status": _value(case.status),
                "summary": case.input_json.get("summary"),
                "content_type": case.input_json.get("content_type"),
                "policy_snapshot_hash": case.policy_snapshot_hash,
            },
            "current": {
                "step": _value(active.step_type) if active else "COMPLETED",
                "agent": active.assignee if active else None,
                "status": _value(active.status) if active else "SUCCEEDED",
            },
            "artifacts": {kind: self._artifact_data(row) for kind, row in artifact_by_type.items()},
            "artifact_list": [self._artifact_data(row) for row in artifacts],
            "appeals": [{
                "appeal_id": item.appeal_id, "status": _value(item.status),
                "original_case_snapshot": item.original_case_snapshot_json,
                "allowed_evidence_ids": item.allowed_evidence_ids,
            } for item in appeals],
            "evolutions": [{
                "evolution_id": item.evolution_id, "status": _value(item.status),
                "source_appeal_id": item.source_appeal_id,
                "current_proposal_artifact_id": item.current_proposal_artifact_id,
                "current_replay_id": item.current_replay_id,
            } for item in evolutions],
            "policy": {
                "baseline": self._policy_data(baseline),
                "candidate": proposal.payload["candidate_policy"] if proposal else None,
                "diff": proposal.payload["changes"] if proposal else [], "published": False,
            },
            "replay": self._replay_data(replay),
            "non_policy_branch": {
                "evolution_id": non_policy.evolution_id if non_policy else None,
                "status": _value(non_policy.status) if non_policy else "NOT_STARTED",
                "attribution": next((
                    row.payload.get("attribution") for row in artifacts
                    if row.aggregate_id == (non_policy.evolution_id if non_policy else None)
                    and _value(row.artifact_type) == "ATTRIBUTION_REPORT"
                ), None),
                "downstream_count": sum(
                    1 for row in works
                    if row.aggregate_id == (non_policy.evolution_id if non_policy else None)
                    and _value(row.step_type) in {"POLICY_DRAFTING", "REPLAY"}
                ),
            },
            "work_orders": [self._work_data(item) for item in works],
            "execution_evidence": {
                "work_order_count": len(works), "artifact_count": len(artifacts),
                "trace_ids": sorted({item.trace_id for item in works}),
            },
        }

    def overview(self, start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        """Read-only aggregate for the two fixed Agent chains and business signals."""

        works = list(self.session.scalars(select(WorkOrderRow).order_by(WorkOrderRow.created_at)))
        cases = list(self.session.scalars(select(Case).order_by(Case.created_at)))
        artifacts = list(self.session.scalars(select(Artifact).order_by(Artifact.created_at)))
        works = [row for row in works if self._in_range(row.created_at, start, end)]
        cases = [row for row in cases if self._in_range(row.created_at, start, end)]
        artifacts = [row for row in artifacts if self._in_range(row.created_at, start, end)]

        agent_ids = [
            "case-investigator", "risk-prosecutor", "counter-reviewer", "independent-judge",
            "appeal-reviewer", "case-attributor", "policy-author", "replay-analyst",
        ]
        agents = []
        for agent_id in agent_ids:
            rows = [row for row in works if row.assignee == agent_id]
            ended = [row for row in rows if _value(row.status) in {"SUCCEEDED", "FAILED"}]
            elapsed = [(row.updated_at - row.created_at).total_seconds() * 1000 for row in ended]
            succeeded = sum(_value(row.status) == "SUCCEEDED" for row in ended)
            agents.append({
                "agent_id": agent_id,
                "task_count": len(rows),
                "completed_count": len(ended),
                "failed_count": sum(_value(row.status) == "FAILED" for row in ended),
                "success_rate": round(succeeded / len(ended), 4) if ended else None,
                "average_response_ms": round(sum(elapsed) / len(elapsed)) if elapsed else None,
                "average_task_duration_ms": round(sum(elapsed) / len(elapsed)) if elapsed else None,
                "input_tokens": None,
                "output_tokens": None,
            })

        trend: dict[str, int] = {"VIOLATION": 0, "NO_VIOLATION": 0, "INSUFFICIENT_EVIDENCE": 0, "OVERTURN": 0}
        trend_by_day: dict[str, dict[str, int]] = {}
        policy_hits: dict[tuple[str, str], int] = {}
        for artifact in artifacts:
            kind = _value(artifact.artifact_type)
            payload = artifact.payload
            if kind == "DECISION_RECORD" and payload.get("outcome") in trend:
                trend[payload["outcome"]] += 1
                day = artifact.created_at.date().isoformat()
                trend_by_day.setdefault(day, {key: 0 for key in trend})[payload["outcome"]] += 1
            elif kind == "APPEAL_DECISION" and payload.get("outcome") == "OVERTURN":
                trend["OVERTURN"] += 1
                day = artifact.created_at.date().isoformat()
                trend_by_day.setdefault(day, {key: 0 for key in trend})["OVERTURN"] += 1
            for conclusion in payload.get("conclusions", []):
                ref = conclusion.get("policy_ref") or {}
                if ref.get("policy_id") and ref.get("version"):
                    key = (ref["policy_id"], ref["version"])
                    policy_hits[key] = policy_hits.get(key, 0) + 1
        return {
            "ok": True,
            "state": "SUCCESS",
            "source": "REAL",
            "range": {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None},
            "chains": {
                "adjudication": agent_ids[:4],
                "policy_evolution": agent_ids[4:],
            },
            "agents": agents,
            "business": {
                "case_count": len(cases),
                "decision_trend": trend,
                "decision_series": [
                    {"date": day, **counts} for day, counts in sorted(trend_by_day.items())
                ],
                "policy_hits": [
                    {"policy_id": key[0], "version": key[1], "count": count}
                    for key, count in sorted(policy_hits.items(), key=lambda item: (-item[1], item[0]))
                ],
            },
            "notes": {
                "tokens": "当前数据模型没有可靠 Token 或费用记录。",
                "success_rate": "成功率仅以已结束 WorkOrder 为分母。",
                "task_duration": "任务历时按 WorkOrder 创建到结束计算，包含排队与等待，不等同于模型响应时间。",
            },
        }

    def list_cases(self, q: str = "", status: str = "", policy_id: str = "", page: int = 1,
                   page_size: int = 20) -> dict[str, Any]:
        rows = list(self.session.scalars(select(Case).order_by(Case.created_at.desc(), Case.case_id)))
        items = [self._case_summary(row) for row in rows]
        query = q.casefold().strip()
        if query:
            items = [item for item in items if query in " ".join([
                item["case_id"], item.get("summary") or "", item["display_status"],
                *[ref["policy_id"] for ref in item["policies"]],
                *[ref.get("title") or "" for ref in item["policies"]],
            ]).casefold()]
        if status:
            items = [item for item in items if item["status"] == status or item["display_status"] == status]
        if policy_id:
            items = [item for item in items if any(ref["policy_id"] == policy_id for ref in item["policies"])]
        return self._page(items, page, page_size)

    def case_detail(self, case_id: str) -> dict[str, Any]:
        case = self.session.get(Case, case_id)
        if case is None:
            raise DemoNotFound("案件不存在")
        appeals = list(self.session.scalars(select(Appeal).where(Appeal.case_id == case_id).order_by(Appeal.created_at)))
        appeal_ids = [row.appeal_id for row in appeals]
        evolutions = list(self.session.scalars(select(PolicyEvolution).where(
            PolicyEvolution.source_appeal_id.in_(appeal_ids or ["-"])
        ).order_by(PolicyEvolution.created_at)))
        aggregate_ids = [case_id, *appeal_ids, *(row.evolution_id for row in evolutions)]
        works = list(self.session.scalars(select(WorkOrderRow).where(
            WorkOrderRow.aggregate_id.in_(aggregate_ids)
        ).order_by(WorkOrderRow.created_at, WorkOrderRow.work_order_id)))
        artifacts = list(self.session.scalars(select(Artifact).where(
            Artifact.aggregate_id.in_(aggregate_ids)
        ).order_by(Artifact.created_at, Artifact.artifact_id)))
        artifact_by_work = {row.work_order_id: row for row in artifacts}
        attribution_by_evolution = {
            row.aggregate_id: row.payload.get("attribution") for row in artifacts
            if _value(row.artifact_type) == "ATTRIBUTION_REPORT"
        }
        primary_evolutions = [row for row in evolutions if attribution_by_evolution.get(row.evolution_id) in {"POLICY_GAP", "POLICY_CONFLICT"}]
        flow = []
        for work in works:
            artifact = artifact_by_work.get(work.work_order_id)
            flow.append({
                **self._work_data(work),
                "input_refs": work.input_refs,
                "artifact": self._artifact_data(artifact) if artifact else None,
            })
        return {
            "ok": True, "state": "SUCCESS", "source": "REAL",
            "case": self._case_summary(case),
            "policy_snapshot_hash": case.policy_snapshot_hash,
            "has_appeal": bool(appeals),
            "has_policy_evolution": bool(primary_evolutions),
            "appeals": [{
                "appeal_id": row.appeal_id, "status": _value(row.status),
                "allowed_evidence_ids": row.allowed_evidence_ids,
                "original_case_snapshot": row.original_case_snapshot_json,
                "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat(),
            } for row in appeals],
            "evolutions": [{
                "evolution_id": row.evolution_id, "status": _value(row.status),
                "source_appeal_id": row.source_appeal_id,
                "attribution": attribution_by_evolution.get(row.evolution_id),
                "current_proposal_artifact_id": row.current_proposal_artifact_id,
                "current_replay_id": row.current_replay_id,
            } for row in evolutions],
            "flow": flow,
        }

    def list_policies(self, q: str = "", status: str = "", page: int = 1,
                      page_size: int = 20) -> dict[str, Any]:
        rows = list(self.session.scalars(select(PolicyRow).order_by(PolicyRow.policy_id, PolicyRow.created_at.desc())))
        cases = list(self.session.scalars(select(Case)))
        grouped: dict[str, list[PolicyRow]] = {}
        for row in rows:
            grouped.setdefault(row.policy_id, []).append(row)
        items = []
        for policy_id, versions in grouped.items():
            matching = [row for row in versions if not status or _value(row.status) == status]
            if not matching:
                continue
            representative = next((row for row in versions if _value(row.status) == "BASELINE"), versions[0])
            citations = sum(any(ref.get("policy_id") == policy_id for ref in self._snapshot_policies(case)) for case in cases)
            item = {
                "policy_id": policy_id, "title": representative.title, "description": representative.description,
                "baseline_version": next((row.version for row in versions if _value(row.status) == "BASELINE"), None),
                "candidate_versions": [row.version for row in versions if _value(row.status) == "CANDIDATE"],
                "version_count": len(versions), "citation_count": citations,
                "updated_at": max(row.created_at for row in versions).isoformat(),
            }
            if not q or q.casefold() in " ".join((policy_id, item["title"], item["description"])).casefold():
                items.append(item)
        items.sort(key=lambda item: (-item["citation_count"], item["policy_id"]))
        return self._page(items, page, page_size)

    def policy_detail(self, policy_id: str, version: str | None = None,
                      start: datetime | None = None, end: datetime | None = None) -> dict[str, Any]:
        rows = list(self.session.scalars(select(PolicyRow).where(
            PolicyRow.policy_id == policy_id
        ).order_by(PolicyRow.created_at.desc())))
        if not rows:
            raise DemoNotFound("规则不存在")
        selected = next((row for row in rows if row.version == version), None) if version else next(
            (row for row in rows if _value(row.status) == "BASELINE"), rows[0]
        )
        if selected is None:
            raise DemoNotFound("规则版本不存在")
        cases = [self._case_summary(row) for row in self.session.scalars(select(Case).order_by(Case.created_at.desc()))
                 if any(ref.get("policy_id") == policy_id for ref in self._snapshot_policies(row))]
        ranged_cases = [row for row in cases if self._in_range(datetime.fromisoformat(row["created_at"]), start, end)]
        versions = []
        for row in rows:
            proposal = self.session.get(Artifact, row.source_proposal_artifact_id) if row.source_proposal_artifact_id else None
            versions.append({
                **self._policy_data(row),
                "source_proposal_artifact_id": row.source_proposal_artifact_id,
                "changes": proposal.payload.get("changes", []) if proposal else [],
                "created_at": row.created_at.isoformat(),
            })
        return {
            "ok": True, "state": "SUCCESS", "source": "REAL",
            "policy": self._policy_data(selected),
            "versions": versions,
            "citation_count": len(cases), "recent_cases": cases[:10],
            "usage": {
                "range": {"from": start.isoformat() if start else None, "to": end.isoformat() if end else None},
                "citation_count": len(ranged_cases),
                "latest_case": ranged_cases[0] if ranged_cases else None,
                "condition_hit_count": None,
                "exception_hit_count": None,
            },
        }

    def list_approvals(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        evolutions = list(self.session.scalars(select(PolicyEvolution).where(
            PolicyEvolution.status == "AWAITING_APPROVAL"
        ).order_by(PolicyEvolution.updated_at.desc())))
        items = []
        for evolution in evolutions:
            appeal = self.session.get(Appeal, evolution.source_appeal_id)
            replay = self.session.get(ReplayRun, evolution.current_replay_id) if evolution.current_replay_id else None
            proposal = self.session.get(Artifact, evolution.current_proposal_artifact_id) if evolution.current_proposal_artifact_id else None
            if appeal is None:
                continue
            case = self.session.get(Case, appeal.case_id)
            base_policy = self.session.get(PolicyRow, (evolution.base_policy_id, evolution.base_policy_version))
            demo_run_id = case.demo_run_id if case else None
            approval_actionable = bool(demo_run_id and RUN_ID_PATTERN.fullmatch(demo_run_id))
            items.append({
                "evolution_id": evolution.evolution_id, "case_id": appeal.case_id,
                "demo_run_id": demo_run_id if approval_actionable else None,
                "approval_actionable": approval_actionable,
                "case_summary": case.input_json.get("summary") if case else None,
                "appeal_reason": appeal.request_json.get("reason"),
                "status": _value(evolution.status), "base_policy_id": evolution.base_policy_id,
                "base_policy_version": evolution.base_policy_version,
                "base_policy": self._policy_data(base_policy),
                "proposal_artifact_id": evolution.current_proposal_artifact_id,
                "replay_id": evolution.current_replay_id,
                "changes": proposal.payload.get("changes", []) if proposal else [],
                "candidate_policy": proposal.payload.get("candidate_policy") if proposal else None,
                "replay_impact": self._replay_impact(replay),
                "updated_at": evolution.updated_at.isoformat(),
            })
        return self._page(items, page, page_size)

    def replay_detail(self, replay_id: str, impact: str = "", q: str = "", page: int = 1,
                      page_size: int = 20) -> dict[str, Any]:
        replay = self.session.get(ReplayRun, replay_id)
        if replay is None:
            raise DemoNotFound("回放不存在")
        results = []
        dataset_samples = {
            row.get("sample_id"): row for row in _load("fixtures/replay/v1/dataset.json").get("samples", [])
        }
        for row in replay.case_results_json:
            direction = self._impact_direction(row)
            sample = dataset_samples.get(row.get("sample_id"), {})
            item = {
                **row, "impact": direction, "case_id": row.get("case_id") or row.get("sample_id"),
                "record_type": "CASE" if row.get("case_id") else "REPLAY_SAMPLE",
                "facts": sample.get("facts"),
            }
            haystack = " ".join(str(value) for value in item.values()).casefold()
            if (not impact or direction == impact) and (not q or q.casefold() in haystack):
                results.append(item)
        response = self._page(results, page, page_size)
        response.update({
            "replay": self._replay_data(replay),
            "impact_summary": self._replay_impact(replay),
            "time_range": None,
            "time_range_note": "固定回放集未记录每条样本的业务时间，不能推断回放时间范围。",
        })
        evolution = self.session.get(PolicyEvolution, replay.evolution_id)
        appeal = self.session.get(Appeal, evolution.source_appeal_id) if evolution else None
        candidate = self.session.get(PolicyRow, (replay.candidate_policy_id, replay.candidate_policy_version))
        response["context"] = {
            "source_case_id": appeal.case_id if appeal else None,
            "policy_id": replay.candidate_policy_id,
            "policy_title": candidate.title if candidate else replay.candidate_policy_id,
            "candidate_version": replay.candidate_policy_version,
        }
        return response

    @staticmethod
    def _in_range(value: datetime, start: datetime | None, end: datetime | None) -> bool:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return (start is None or value >= start) and (end is None or value <= end)

    @staticmethod
    def _snapshot_policies(case: Case) -> list[dict[str, Any]]:
        return list((case.policy_snapshot_json or {}).get("policies") or [])

    def _case_summary(self, case: Case) -> dict[str, Any]:
        policies = []
        for ref in self._snapshot_policies(case):
            policy = self.session.get(PolicyRow, (ref.get("policy_id"), ref.get("version")))
            policies.append({**ref, "title": policy.title if policy else None})
        base_status = _value(case.status)
        status = base_status
        appeal_ids = list(self.session.scalars(select(Appeal.appeal_id).where(Appeal.case_id == case.case_id)))
        evolutions = list(self.session.scalars(select(PolicyEvolution).where(
            PolicyEvolution.source_appeal_id.in_(appeal_ids or ["-"])
        )))
        evolution_states = {_value(row.status) for row in evolutions}
        if "AWAITING_APPROVAL" in evolution_states:
            status = "AWAITING_APPROVAL"
        elif evolution_states & {"ATTRIBUTING", "DRAFTING", "REPLAYING"}:
            status = "POLICY_EVOLUTION"
        elif appeal_ids and any(
            _value(row.status) == "REVIEWING"
            for row in self.session.scalars(select(Appeal).where(Appeal.appeal_id.in_(appeal_ids)))
        ):
            status = "APPEAL_REVIEWING"
        display_status = {
            "NEW": "未开始", "INVESTIGATING": "调查中", "ARGUING": "正反审议中",
            "ADJUDICATING": "独立裁决中", "DECIDED": "已裁决",
            "APPEAL_REVIEWING": "申诉中", "POLICY_EVOLUTION": "规则演进中",
            "AWAITING_APPROVAL": "待人工审批",
        }[status]
        return {
            "case_id": case.case_id, "demo_run_id": case.demo_run_id,
            "status": status, "case_status": base_status, "display_status": display_status,
            "summary": case.input_json.get("summary"), "content_type": case.input_json.get("content_type"),
            "policies": policies, "confidence": None,
            "created_at": case.created_at.isoformat(), "updated_at": case.updated_at.isoformat(),
        }

    @staticmethod
    def _page(items: list[dict[str, Any]], page: int, page_size: int) -> dict[str, Any]:
        page = max(1, page)
        page_size = max(1, min(100, page_size))
        total = len(items)
        start = (page - 1) * page_size
        return {
            "ok": True, "state": "SUCCESS", "source": "REAL", "items": items[start:start + page_size],
            "pagination": {"page": page, "page_size": page_size, "total": total,
                           "pages": max(1, (total + page_size - 1) // page_size)},
        }

    @classmethod
    def _replay_impact(cls, replay: ReplayRun | None) -> dict[str, Any] | None:
        if replay is None:
            return None
        directions = [cls._impact_direction(row) for row in replay.case_results_json]
        lighter = directions.count("LIGHTER")
        heavier = directions.count("HEAVIER")
        affected = lighter + heavier
        return {
            "total": len(directions), "unaffected": len(directions) - affected,
            "affected": affected, "lighter": lighter, "heavier": heavier,
            "completed_at": replay.created_at.isoformat(),
        }

    @staticmethod
    def _impact_direction(row: dict[str, Any]) -> str:
        baseline = row.get("baseline_outcome")
        candidate = row.get("candidate_outcome")
        if baseline == candidate:
            return "UNAFFECTED"
        if baseline == "VIOLATION" and candidate == "NO_VIOLATION":
            return "LIGHTER"
        if baseline == "NO_VIOLATION" and candidate == "VIOLATION":
            return "HEAVIER"
        return "CHANGED"

    def _run_non_policy_branch(self, tag: str, case_id: str) -> None:
        case = self.session.get(Case, case_id)
        decision = self.session.scalar(select(Artifact).where(
            Artifact.aggregate_type == "CASE", Artifact.aggregate_id == case_id,
            Artifact.artifact_type == "DECISION_RECORD",
        ))
        if case is None or decision is None:
            raise DemoConflict("非规则分支缺少已裁决原案")
        appeal_id = f"APPEAL-{tag}-NP"
        appeal_request = deepcopy(_load("fixtures/appeal/appeal_request.json"))
        appeal_request.update(appeal_id=appeal_id, case_id=case_id,
                              reason="新增证据仅补齐原案材料缺口，不涉及规则表达。")
        self.kernel.create_appeal(
            appeal_id=appeal_id, case_id=case_id, request_json=appeal_request,
            original_case_snapshot_json={
                "case_id": case_id, "decision_id": decision.payload["decision_id"],
                "evidence_ids": ["E-001"], "policy_snapshot": case.policy_snapshot_json,
            }, allowed_evidence_ids=["E-APPEAL-001"],
        )
        self.kernel.advance_appeal(appeal_id, "NEW", "REVIEWING", trace_id=f"TRACE-{tag}-NP")
        appeal_result = self._execute(_work(
            f"{tag}-NP", "A", "APPEAL", appeal_id, "APPEAL_REVIEW", "appeal-reviewer",
            "APPEAL_DECISION", [
                {"ref_type": "APPEAL", "ref_id": appeal_id},
                {"ref_type": "EVIDENCE", "ref_id": "E-APPEAL-001"},
            ],
        ))
        evolution_id = f"EVOLUTION-{tag}-NP"
        self.kernel.create_policy_evolution(
            evolution_id=evolution_id, source_appeal_id=appeal_id,
            base_policy_id="MINOR_DANGEROUS_ACT", base_policy_version="1.0",
        )
        self.kernel.advance_policy_evolution(evolution_id, "NEW", "ATTRIBUTING",
                                             trace_id=f"TRACE-{tag}-NP")
        self._execute(_work(
            f"{tag}-NP", "AT", "POLICY_EVOLUTION", evolution_id, "ATTRIBUTION",
            "case-attributor", "ATTRIBUTION_REPORT", [
                {"ref_type": "ARTIFACT", "ref_id": appeal_result["artifact_id"]},
                {"ref_type": "POLICY_SNAPSHOT", "ref_id": "PS-001"},
                {"ref_type": "REPLAY_DATASET", "ref_id": "v1"},
            ],
        ))

    def _execute(self, data: dict[str, Any]) -> dict[str, Any]:
        self.kernel.create_work_order(data)
        result = self.runner.run(data["assignee_id"], data["work_order_id"],
                                 data["run_id"], data["trace_id"])
        if result.status != "SUCCESS" or result.artifact_id is None:
            raise DemoConflict(f"{data['assignee_id']} 执行失败：{result.error_code}")
        return {"artifact_id": result.artifact_id, "artifact": result.artifact}

    def _failure_view(self, failure_demo_run_id: str) -> dict[str, Any]:
        case = self.session.scalar(select(Case).where(Case.demo_run_id == failure_demo_run_id))
        if case is None:
            raise DemoNotFound("失败场景不存在")
        work = self.session.scalar(select(WorkOrderRow).where(WorkOrderRow.aggregate_id == case.case_id))
        return {
            "ok": True, "state": "FAILURE", "demo_run_id": failure_demo_run_id,
            "case_id": case.case_id, "case_status": _value(case.status),
            "work_order": self._work_data(work),
            "message": "固定转写工具 TIMEOUT；执行失败，不能解释为业务无数据。",
        }

    @staticmethod
    def _artifact_data(row: Artifact) -> dict[str, Any]:
        return {
            "artifact_id": row.artifact_id, "artifact_type": _value(row.artifact_type),
            "work_order_id": row.work_order_id, "run_id": row.run_id,
            "trace_id": row.trace_id, "producer_id": row.producer_id,
            "producer_type": _value(row.producer_type), "payload": row.payload,
            "content_hash": row.content_hash,
        }

    @staticmethod
    def _work_data(row: WorkOrderRow) -> dict[str, Any]:
        status = _value(row.status)
        return {
            "work_order_id": row.work_order_id, "aggregate_type": _value(row.aggregate_type),
            "aggregate_id": row.aggregate_id, "step_type": _value(row.step_type),
            "assignee": row.assignee, "status": status,
            "display_state": {"PENDING": "NOT_STARTED", "RUNNING": "RUNNING",
                              "SUCCEEDED": "SUCCESS", "FAILED": "FAILURE"}[status],
            "run_id": row.run_id, "trace_id": row.trace_id,
            "error_code": row.error_code, "attempt": row.attempt,
        }

    @staticmethod
    def _policy_data(row: PolicyRow | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "policy_id": row.policy_id, "version": row.version, "status": _value(row.status),
            "title": row.title, "description": row.description, "dsl": row.dsl_json,
            "content_hash": row.content_hash,
        }

    @staticmethod
    def _replay_data(row: ReplayRun | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "replay_id": row.replay_id, "dataset_version": row.dataset_version,
            "dataset_manifest_hash": row.dataset_manifest_hash,
            "baseline_policy": f"{row.baseline_policy_id}:{row.baseline_policy_version}",
            "candidate_policy": f"{row.candidate_policy_id}:{row.candidate_policy_version}",
            "metrics": row.metrics_json, "recommendation": _value(row.recommendation),
            "case_results": row.case_results_json,
        }
