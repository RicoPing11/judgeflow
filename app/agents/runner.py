"""Deterministic local driver for exactly the eight stage-four agents.

This module is deliberately not a generic agent runtime. It binds one WorkOrder
execution to the existing five-tool MCP service and creates only stage-one
ArtifactEnvelope payloads for the fixed competition path.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from app.agents.specs import AGENT_SPECS, AgentSpec
from app.core import canonical_hash, policy_hash_value
from app.models.schemas import ArtifactEnvelope, Policy


FIXED_TIME = datetime(2026, 8, 12, 1, 40, tzinfo=UTC)


class MCPGateway(Protocol):
    def work_order_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]: ...
    def context_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]: ...
    def evidence_search(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, query_type: str, evidence_id: str) -> dict[str, Any]: ...
    def artifact_put(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, artifact: dict[str, Any]) -> dict[str, Any]: ...
    def replay_execute(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, replay_id: str, proposal_artifact_id: str, dataset_version: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AgentRunResult:
    status: str
    agent_id: str
    artifact_id: str | None = None
    artifact: dict[str, Any] | None = None
    error_code: str | None = None
    message: str | None = None


class MissingInformation(ValueError):
    pass


class ToolInvocationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FixedAgentRunner:
    """Run one authorized WorkOrder using a fixed domain-agent contract."""

    def __init__(self, gateway: MCPGateway):
        self.gateway = gateway

    def run(self, agent_id: str, work_order_id: str, run_id: str, trace_id: str) -> AgentRunResult:
        spec = AGENT_SPECS[agent_id]
        work_result = self.gateway.work_order_get(agent_id, work_order_id, run_id, trace_id)
        failure = self._tool_failure(spec, work_result)
        if failure:
            return failure
        work = work_result["data"]
        mismatch = self._validate_assignment(spec, work, work_order_id, run_id, trace_id)
        if mismatch:
            return mismatch
        context_result = self.gateway.context_get(agent_id, work_order_id, run_id, trace_id)
        failure = self._tool_failure(spec, context_result)
        if failure:
            return failure
        context = context_result["data"]
        try:
            payload = self._build_payload(spec, work, context)
        except ToolInvocationError as exc:
            return AgentRunResult("TOOL_FAILURE", agent_id, error_code=exc.code, message=str(exc))
        except MissingInformation as exc:
            return AgentRunResult("MISSING_INFORMATION", agent_id, error_code="INCOMPLETE_DATA", message=str(exc))
        except ValueError as exc:
            return AgentRunResult("TOOL_FAILURE", agent_id, error_code="PERMISSION_DENIED", message=str(exc))
        artifact = self._envelope(spec, work, payload)
        put_result = self.gateway.artifact_put(agent_id, work_order_id, run_id, trace_id, artifact)
        failure = self._tool_failure(spec, put_result)
        if failure:
            return failure
        return AgentRunResult("SUCCESS", agent_id, put_result["data"]["artifact_id"], artifact)

    @staticmethod
    def _validate_assignment(spec: AgentSpec, work: dict[str, Any], work_order_id: str, run_id: str, trace_id: str) -> AgentRunResult | None:
        expected = (spec.agent_id, spec.step_type, spec.output_type, work_order_id, run_id, trace_id)
        actual = (work.get("assignee_id"), work.get("step_type"), work.get("expected_artifact_type"), work.get("work_order_id"), work.get("run_id"), work.get("trace_id"))
        if actual != expected:
            return AgentRunResult("TOOL_FAILURE", spec.agent_id, error_code="PERMISSION_DENIED", message="WorkOrder identity, step, output, run, or trace does not match the agent contract")
        return None

    @staticmethod
    def _tool_failure(spec: AgentSpec, result: dict[str, Any]) -> AgentRunResult | None:
        if result.get("ok"):
            return None
        error = result.get("error") or {}
        return AgentRunResult("TOOL_FAILURE", spec.agent_id, error_code=error.get("code", "VALIDATION_ERROR"), message=error.get("message", "MCP tool failed"))

    def _build_payload(self, spec: AgentSpec, work: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        materials = context.get("materials")
        if not isinstance(materials, list) or not materials:
            raise MissingInformation("authorized context contains no materials")
        builder = getattr(self, f"_build_{spec.agent_id.replace('-', '_')}")
        return builder(work, materials)

    def _build_case_investigator(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        evidence_refs = [item for item in work["input_refs"] if item["ref_type"] == "EVIDENCE"]
        if not evidence_refs:
            raise MissingInformation("investigation WorkOrder authorizes no evidence")
        raw, facts, missing, failures = [], [], [], []
        for index, ref in enumerate(evidence_refs, start=1):
            query_type = "APPEAL_SUBMISSION" if ref["ref_id"].startswith("E-APPEAL") else "CONTENT"
            result = self.gateway.evidence_search(work["assignee_id"], work["work_order_id"], work["run_id"], work["trace_id"], query_type, ref["ref_id"])
            if not result.get("ok"):
                error = result.get("error") or {}
                code = error.get("code", "VALIDATION_ERROR")
                failures.append({"failure_id": f"TF-{work['run_id']}-{index}", "tool_name": "evidence.search", "error_code": code, "message": error.get("message", "证据工具失败。"), "retryable": code == "TIMEOUT", "occurred_at": FIXED_TIME.isoformat()})
                if code == "INCOMPLETE_DATA":
                    fields = (result.get("data") or {}).get("missing_fields", [])
                    missing.append(f"证据 {ref['ref_id']} 缺少字段：{', '.join(fields) or '未说明'}。")
                continue
            data = result["data"]
            raw.append({"evidence_id": data["evidence_id"], "source_type": data["source_type"], "source_ref": data["source_ref"], "content_hash": data["content_hash"], "collected_at": data["collected_at"]})
            observed = data.get("observable_facts")
            if not isinstance(observed, list) or not observed:
                missing.append(f"证据 {data['evidence_id']} 未提供可观察事实。")
                continue
            for fact_index, fact in enumerate(observed, start=1):
                if not all(key in fact for key in ("fact_key", "value", "statement")):
                    missing.append(f"证据 {data['evidence_id']} 的可观察事实字段不完整。")
                    continue
                facts.append({"record_type": "OBSERVED_FACT", "fact_id": f"F-{work['run_id']}-{index}-{fact_index}", "fact_key": fact["fact_key"], "value": fact["value"], "statement": fact["statement"], "evidence_ids": [data["evidence_id"]]})
        if not raw and failures:
            raise ToolInvocationError(failures[0]["error_code"], failures[0]["message"])
        if not raw:
            raise MissingInformation("all authorized evidence queries failed or were incomplete")
        if not facts:
            raise MissingInformation("authorized evidence contains no usable observable facts")
        return {"bundle_id": f"EB-{work['run_id']}", "raw_evidence_refs": raw, "observable_facts": facts, "agent_inferences": [{"record_type": "AGENT_INFERENCE", "inference_id": f"I-{work['run_id']}", "statement": "现有观察事实需要按锁定规则进一步审查。", "basis_fact_ids": [fact["fact_id"] for fact in facts], "confidence": 0.8}], "missing_information": missing, "tool_failures": failures}

    def _case_materials(self, materials: list[dict[str, Any]], *, forbidden: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        artifact_materials = [item for item in materials if item.get("ref_type") == "ARTIFACT"]
        if forbidden and any(item.get("artifact_type") == forbidden for item in artifact_materials):
            raise ValueError(f"agent contract forbids reading {forbidden}")
        evidence = next((item["value"] for item in artifact_materials if item.get("artifact_type") == "EVIDENCE_BUNDLE"), None)
        policy = next((item["value"]["policies"][0] for item in materials if item.get("ref_type") == "POLICY_SNAPSHOT"), None)
        if not evidence or not policy:
            raise MissingInformation("accepted evidence bundle and locked policy are required")
        return evidence, policy

    @staticmethod
    def _first_evidence(evidence: dict[str, Any]) -> str:
        refs = evidence.get("raw_evidence_refs") or []
        if not refs:
            raise MissingInformation("evidence bundle has no raw evidence")
        return refs[0]["evidence_id"]

    @staticmethod
    def _evidence_for_clauses(evidence: dict[str, Any], clauses: set[str]) -> str:
        facts = [fact for fact in evidence.get("observable_facts", []) if fact.get("fact_key") in clauses and fact.get("value") is True]
        covered = {fact["fact_key"] for fact in facts}
        if not clauses <= covered:
            raise MissingInformation(f"accepted evidence does not support clauses: {', '.join(sorted(clauses - covered))}")
        common = set(facts[0].get("evidence_ids", []))
        for fact in facts[1:]:
            common &= set(fact.get("evidence_ids", []))
        if not common:
            raise MissingInformation("no single accepted evidence item supports all cited clauses")
        return sorted(common)[0]

    def _build_risk_prosecutor(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        evidence, policy = self._case_materials(materials, forbidden="COUNTER_ARGUMENT")
        return {"argument_id": f"RA-{work['run_id']}", "conclusions": [{"conclusion_id": f"RC-{work['run_id']}", "statement": "已验收观察事实支持未成年人出现与危险行为要件。", "evidence_id": self._evidence_for_clauses(evidence, {"MINOR_PRESENT", "DANGEROUS_ACT"}), "policy_ref": {"policy_id": policy["policy_id"], "version": policy["version"], "clause_ids": ["MINOR_PRESENT", "DANGEROUS_ACT"]}}], "uncertainties": ["受控教学例外需由独立反证审查核对。"]}

    def _build_counter_reviewer(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        evidence, policy = self._case_materials(materials, forbidden="RISK_ARGUMENT")
        evidence_id = self._evidence_for_clauses(evidence, {"DANGEROUS_ACT"})
        return {"argument_id": f"CA-{work['run_id']}", "conclusions": [{"conclusion_id": f"CC-{work['run_id']}", "statement": "同一危险行为证据没有包含足以确认或排除受控教学例外的观察事实。", "evidence_id": evidence_id, "policy_ref": {"policy_id": policy["policy_id"], "version": policy["version"], "clause_ids": ["CONTROLLED_EDUCATION"]}}], "alternative_explanations": ["缺失的成人监督或教学背景材料可能改变例外审查结果。"]}

    def _build_independent_judge(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        evidence, policy = self._case_materials(materials)
        artifacts = {item.get("artifact_type"): item["value"] for item in materials if item.get("ref_type") == "ARTIFACT"}
        if "RISK_ARGUMENT" not in artifacts or "COUNTER_ARGUMENT" not in artifacts:
            raise MissingInformation("both accepted risk and counter arguments are required")
        return {"decision_id": f"DR-{work['run_id']}", "outcome": "INSUFFICIENT_EVIDENCE", "conclusions": [{"conclusion_id": f"DC-{work['run_id']}", "statement": "正反意见均有依据，现有已验收证据不足以排除或确认全部规则条件。", "evidence_id": self._first_evidence(evidence), "policy_ref": {"policy_id": policy["policy_id"], "version": policy["version"], "clause_ids": ["MINOR_PRESENT", "DANGEROUS_ACT", "CONTROLLED_EDUCATION"]}}], "considered_argument_ids": [artifacts["RISK_ARGUMENT"]["argument_id"], artifacts["COUNTER_ARGUMENT"]["argument_id"]]}

    def _build_appeal_reviewer(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        if any(item.get("ref_type") not in {"APPEAL", "EVIDENCE"} for item in materials):
            raise ValueError("appeal agent received live or non-whitelisted context")
        appeal = next((item["value"] for item in materials if item.get("ref_type") == "APPEAL"), None)
        evidence = next((item for item in materials if item.get("ref_type") == "EVIDENCE"), None)
        if not appeal or not evidence or not appeal.get("locked_policies"):
            raise MissingInformation("frozen appeal snapshot, locked policy, and whitelisted evidence are required")
        policy = appeal["locked_policies"][0]
        observed = evidence["value"].get("observable_facts", [])
        keys = {fact.get("fact_key") for fact in observed if fact.get("value") is True}
        if not {"ADULT_SUPERVISION", "EDUCATIONAL_CONTEXT"} <= keys:
            raise MissingInformation("whitelisted evidence does not support the controlled education finding")
        original = appeal["original_case_snapshot"].get("decision_id") or appeal["request"].get("original_decision_id")
        if not original:
            raise MissingInformation("frozen original decision id is missing")
        request_reason = str(appeal["request"].get("reason", ""))
        statement = (
            "白名单新增证据补齐原案材料缺口，受控教学例外成立。"
            if "补齐原案材料缺口" in request_reason
            else "白名单新增证据支持受控教学例外，并揭示基线例外表达不清。"
        )
        return {"appeal_decision_id": f"AD-{work['run_id']}", "outcome": "OVERTURN", "conclusions": [{"conclusion_id": f"AC-{work['run_id']}", "statement": statement, "evidence_id": evidence["ref_id"], "policy_ref": {"policy_id": policy["policy_id"], "version": policy["version"], "clause_ids": ["CONTROLLED_EDUCATION"]}}], "original_decision_id": original, "new_evidence_ids": [evidence["ref_id"]]}

    def _build_case_attributor(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        appeal = next((item["value"] for item in materials if item.get("artifact_type") == "APPEAL_DECISION"), None)
        if not appeal:
            raise MissingInformation("accepted appeal decision is required")
        datasets = [item["value"] for item in materials if item.get("ref_type") == "REPLAY_DATASET"]
        if any(sample.get("split") == "SCORING" for data in datasets for sample in data.get("samples", [])):
            raise ValueError("attribution agent cannot read SCORING")
        statements = " ".join(item.get("statement", "") for item in appeal.get("conclusions", []))
        if "规则冲突" in statements:
            attribution = "POLICY_CONFLICT"
        elif "表达不清" in statements or "规则缺口" in statements:
            attribution = "POLICY_GAP"
        elif "Agent" in statements or "推理错误" in statements:
            attribution = "AGENT_ERROR"
        else:
            attribution = "EVIDENCE_GAP"
        is_policy_issue = attribution in {"POLICY_GAP", "POLICY_CONFLICT"}
        return {"report_id": f"AR-{work['run_id']}", "attribution": attribution, "findings": ["改判材料确认规则表达存在缺口。" if is_policy_issue else "改判由证据或推理原因导致，不应进入规则编写。"], "appeal_decision_id": appeal["appeal_decision_id"], "policy_change_recommended": is_policy_issue}

    def _build_policy_author(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        attribution = next((item["value"] for item in materials if item.get("artifact_type") == "ATTRIBUTION_REPORT"), None)
        policy = next((item["value"]["policies"][0] for item in materials if item.get("ref_type") == "POLICY_SNAPSHOT"), None)
        if not attribution or not policy:
            raise MissingInformation("accepted attribution and locked baseline policy are required")
        if attribution["attribution"] not in {"POLICY_GAP", "POLICY_CONFLICT"} or not attribution["policy_change_recommended"]:
            raise MissingInformation("attribution closed rule evolution without a policy issue")
        datasets = [item["value"] for item in materials if item.get("ref_type") == "REPLAY_DATASET"]
        if any(sample.get("split") == "SCORING" for data in datasets for sample in data.get("samples", [])):
            raise ValueError("policy author cannot read SCORING")
        policy = Policy.model_validate(policy).model_dump(mode="json", exclude_none=True)
        candidate = deepcopy(policy)
        candidate_version = "2.0-demo-r1"
        if work["aggregate_id"] != "EVOLUTION-001":
            candidate_version = f"2.0-demo-r1-{work['aggregate_id'].lower()}"
        candidate.update(version=candidate_version, status="CANDIDATE", description="明确成人监督教学场景的最小候选例外。")
        candidate["exceptions"] = {"any_of": [{"condition_id": "CONTROLLED_EDUCATION", "all_of": [{"condition_id": "ADULT_SUPERVISION", "fact": "ADULT_SUPERVISION", "op": "EQ", "value": True}, {"condition_id": "EDUCATIONAL_CONTEXT", "fact": "EDUCATIONAL_CONTEXT", "op": "EQ", "value": True}]}]}
        candidate["content_hash"] = policy_hash_value(candidate)
        return {"proposal_id": f"PP-{work['run_id']}", "proposal_revision": 1, "base_policy": {"policy_id": policy["policy_id"], "version": policy["version"], "content_hash": policy["content_hash"]}, "candidate_policy": candidate, "changes": [{"path": "exceptions", "operation": "REPLACE", "rationale": "明确受控教学边界。"}], "reason": "申诉改判与归因报告确认规则例外表达存在缺口。", "risks": ["候选例外可能扩大适用范围。"]}

    def _build_replay_analyst(self, work: dict[str, Any], materials: list[dict[str, Any]]) -> dict[str, Any]:
        proposal_item = next((item for item in materials if item.get("artifact_type") == "POLICY_PROPOSAL"), None)
        dataset_item = next((item for item in materials if item.get("ref_type") == "REPLAY_DATASET"), None)
        if not proposal_item or not dataset_item:
            raise MissingInformation("proposal and replay dataset are required")
        replay_id = f"REPLAY-{work['run_id']}"
        result = self.gateway.replay_execute(work["assignee_id"], work["work_order_id"], work["run_id"], work["trace_id"], replay_id, proposal_item["ref_id"], dataset_item["ref_id"])
        if not result.get("ok"):
            error = result.get("error") or {}
            raise ToolInvocationError(error.get("code", "VALIDATION_ERROR"), error.get("message", "replay.execute failed"))
        replay = result["data"]
        proposal = proposal_item["value"]
        return {"replay_id": replay["replay_id"], "proposal_id": proposal["proposal_id"], "baseline_policy": {**replay["baseline_policy"], "content_hash": proposal["base_policy"]["content_hash"]}, "candidate_policy": {**replay["candidate_policy"], "content_hash": proposal["candidate_policy"]["content_hash"]}, "dataset_version": replay["dataset_version"], "dataset_manifest_hash": replay["dataset_manifest_hash"], "metrics": deepcopy(replay["metrics"]), "recommendation": replay["recommendation"], "result_summary": "指标、逐案变化与推荐均来自固定 Python 回放器；本 Agent 仅核对绑定并解释结果。"}

    @staticmethod
    def _envelope(spec: AgentSpec, work: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        envelope = {"artifact_id": f"ART-{work['work_order_id']}", "artifact_type": spec.output_type, "schema_version": "1.0", "aggregate_type": work["aggregate_type"], "aggregate_id": work["aggregate_id"], "work_order_id": work["work_order_id"], "run_id": work["run_id"], "trace_id": work["trace_id"], "producer_type": "AGENT", "producer_id": spec.agent_id, "payload": payload, "content_hash": canonical_hash(payload), "created_at": FIXED_TIME.isoformat()}
        normalized = ArtifactEnvelope.model_validate(envelope).model_dump(mode="json", exclude_none=True)
        normalized["content_hash"] = canonical_hash(normalized["payload"])
        return ArtifactEnvelope.model_validate(normalized).model_dump(mode="json", exclude_none=True)
