"""Five-tool local MCP server for the fixed competition demo.

The service below owns only authorization and fixture selection. Formal artifact
validation and deterministic replay are delegated to the stage-two kernel.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import (
    ConflictError,
    JudgeFlowKernel,
    StateTransitionError,
    ValidationError,
    canonical_hash,
    policy_hash_value,
)
from app.models.database import Appeal, Artifact, Case, PolicyEvolution, PolicyRow, ReplayRun, WorkOrderRow


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


class MCPToolError(ValueError):
    def __init__(self, code: str, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class JudgeFlowMCPService:
    """In-process implementation used by FastMCP and direct contract tests."""

    def __init__(self, session: Session, fixture_root: Path = FIXTURES):
        self.session = session
        self.kernel = JudgeFlowKernel(session)
        self.fixture_root = fixture_root

    def _result(self, tool: str, work_order_id: str, data: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "data": data,
            "error": None,
            "audit_ref": f"mcp:{tool}:{work_order_id}",
        }

    def _error(self, tool: str, work_order_id: str, exc: MCPToolError) -> dict[str, Any]:
        return {
            "ok": False,
            "data": exc.data,
            "error": {"code": exc.code, "message": exc.message},
            "audit_ref": f"mcp:{tool}:{work_order_id}",
        }

    def _call(self, tool: str, work_order_id: str, operation: Callable[[], Any]) -> dict[str, Any]:
        try:
            return self._result(tool, work_order_id, operation())
        except MCPToolError as exc:
            return self._error(tool, work_order_id, exc)

    def _call_mutation(self, tool: str, work_order_id: str, operation: Callable[[], Any]) -> dict[str, Any]:
        """Make stdio tool completion the explicit persistence boundary."""

        try:
            data = operation()
            self.session.commit()
            return self._result(tool, work_order_id, data)
        except MCPToolError as exc:
            self.session.rollback()
            return self._error(tool, work_order_id, exc)
        except Exception:
            self.session.rollback()
            raise

    def _authorize(
        self, *, consumer: str, work_order_id: str, run_id: str, trace_id: str
    ) -> WorkOrderRow:
        work = self.session.get(WorkOrderRow, work_order_id)
        if work is None:
            raise MCPToolError("NOT_FOUND", "work order does not exist")
        if work.assignee != consumer:
            raise MCPToolError("PERMISSION_DENIED", "consumer is not the work order assignee")
        if work.run_id != run_id or work.trace_id != trace_id:
            raise MCPToolError("PERMISSION_DENIED", "run or trace does not match the assigned execution")
        return work

    def _load_fixture(self, path: Path) -> dict[str, Any]:
        try:
            return _json(path)
        except FileNotFoundError as exc:
            raise MCPToolError("NOT_FOUND", "authorized fixed fixture does not exist") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise MCPToolError("VALIDATION_ERROR", "authorized fixed fixture cannot be read") from exc

    @staticmethod
    def _work_data(work: WorkOrderRow) -> dict[str, Any]:
        return {
            "work_order_id": work.work_order_id,
            "aggregate_type": _value(work.aggregate_type),
            "aggregate_id": work.aggregate_id,
            "step_type": _value(work.step_type),
            "assignee_id": work.assignee,
            "input_refs": work.input_refs,
            "expected_artifact_type": _value(work.expected_artifact_type),
            "run_id": work.run_id,
            "trace_id": work.trace_id,
            "status": _value(work.status),
            "retry_count": work.attempt - 1,
            "created_at": work.created_at.isoformat(),
            "updated_at": work.updated_at.isoformat(),
        }

    def work_order_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        return self._call(
            "work_order.get",
            work_order_id,
            lambda: self._work_data(
                self._authorize(
                    consumer=consumer, work_order_id=work_order_id, run_id=run_id, trace_id=trace_id
                )
            ),
        )

    def context_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            work = self._authorize(
                consumer=consumer, work_order_id=work_order_id, run_id=run_id, trace_id=trace_id
            )
            if _value(work.aggregate_type) == "APPEAL" and any(
                item["ref_type"] not in {"APPEAL", "EVIDENCE"} for item in work.input_refs
            ):
                raise MCPToolError(
                    "PERMISSION_DENIED",
                    "appeal context is limited to its immutable snapshot and evidence whitelist",
                )
            materials: list[dict[str, Any]] = []
            allowed = {(item["ref_type"], item["ref_id"]) for item in work.input_refs}
            for ref_type, ref_id in sorted(allowed):
                materials.append(self._resolve_ref(work, ref_type, ref_id))
            return {"aggregate_id": work.aggregate_id, "materials": materials}

        return self._call("context.get", work_order_id, operation)

    def _resolve_ref(self, work: WorkOrderRow, ref_type: str, ref_id: str) -> dict[str, Any]:
        if _value(work.aggregate_type) == "APPEAL" and ref_type not in {"APPEAL", "EVIDENCE"}:
            raise MCPToolError(
                "PERMISSION_DENIED",
                "appeal context is limited to the original snapshot and whitelisted new evidence",
            )
        if ref_type == "CASE":
            case = self.session.get(Case, ref_id)
            if case is None:
                raise MCPToolError("NOT_FOUND", "authorized case reference does not exist")
            return {"ref_type": ref_type, "ref_id": ref_id, "value": case.input_json}
        if ref_type == "POLICY_SNAPSHOT":
            aggregate_type = _value(work.aggregate_type)
            if aggregate_type == "APPEAL":
                raise MCPToolError("PERMISSION_DENIED", "appeal cannot open the live policy snapshot")
            case = self.session.get(Case, work.aggregate_id) if aggregate_type == "CASE" else None
            if aggregate_type == "POLICY_EVOLUTION":
                evolution = self.session.get(PolicyEvolution, work.aggregate_id)
                appeal = self.session.get(Appeal, evolution.source_appeal_id) if evolution else None
                case = self.session.get(Case, appeal.case_id) if appeal else None
            if case is None or case.policy_snapshot_json.get("snapshot_id") != ref_id:
                raise MCPToolError("PERMISSION_DENIED", "policy snapshot is outside the work order")
            policies: list[dict[str, Any]] = []
            for locked in case.policy_snapshot_json["policies"]:
                policy = self.session.get(PolicyRow, (locked["policy_id"], locked["version"]))
                if policy is None or policy.content_hash != locked["content_hash"]:
                    raise MCPToolError("VALIDATION_ERROR", "locked policy version is missing or mismatched")
                policies.append(policy.dsl_json)
            return {
                "ref_type": ref_type,
                "ref_id": ref_id,
                "value": {"snapshot": case.policy_snapshot_json, "policies": policies},
            }
        if ref_type == "ARTIFACT":
            artifact = self.session.get(Artifact, ref_id)
            if artifact is None:
                raise MCPToolError("NOT_FOUND", "authorized artifact reference does not exist")
            return {"ref_type": ref_type, "ref_id": ref_id, "artifact_type": _value(artifact.artifact_type), "value": artifact.payload}
        if ref_type == "APPEAL":
            appeal = self.session.get(Appeal, ref_id)
            if appeal is None:
                raise MCPToolError("NOT_FOUND", "authorized appeal reference does not exist")
            frozen_snapshot = appeal.original_case_snapshot_json.get("policy_snapshot")
            if not isinstance(frozen_snapshot, dict) or not isinstance(frozen_snapshot.get("policies"), list):
                raise MCPToolError("INCOMPLETE_DATA", "appeal snapshot is missing its frozen policy snapshot")
            locked_policies: list[dict[str, Any]] = []
            for locked in frozen_snapshot["policies"]:
                if not isinstance(locked, dict) or not all(
                    isinstance(locked.get(key), str) for key in ("policy_id", "version", "content_hash")
                ):
                    raise MCPToolError("INCOMPLETE_DATA", "appeal frozen policy reference is incomplete")
                policy = self.session.get(PolicyRow, (locked["policy_id"], locked["version"]))
                if policy is None or policy.content_hash != locked["content_hash"]:
                    raise MCPToolError("VALIDATION_ERROR", "appeal locked policy version is missing or mismatched")
                locked_policies.append(policy.dsl_json)
            # The immutable snapshot and whitelist are the entire appeal context.
            return {
                "ref_type": ref_type,
                "ref_id": ref_id,
                "value": {
                    "request": appeal.request_json,
                    "original_case_snapshot": appeal.original_case_snapshot_json,
                    "allowed_evidence_ids": appeal.allowed_evidence_ids,
                    "locked_policies": locked_policies,
                },
            }
        if ref_type == "EVIDENCE":
            if _value(work.aggregate_type) == "APPEAL":
                appeal = self.session.get(Appeal, work.aggregate_id)
                if appeal is None or ref_id not in appeal.allowed_evidence_ids:
                    raise MCPToolError("PERMISSION_DENIED", "evidence is outside the appeal whitelist")
            evidence = self._evidence_entry(ref_id)
            return {"ref_type": ref_type, "ref_id": ref_id, "value": evidence["data"]}
        if ref_type == "REPLAY_DATASET":
            dataset = self._load_fixture(self.fixture_root / "replay" / ref_id / "dataset.json")
            if _value(work.step_type) in {"ATTRIBUTION", "POLICY_DRAFTING"}:
                dataset = dict(dataset)
                dataset["samples"] = [item for item in dataset["samples"] if item["split"] != "SCORING"]
            return {"ref_type": ref_type, "ref_id": ref_id, "value": dataset}
        raise MCPToolError("VALIDATION_ERROR", "unsupported input reference type")

    def _evidence_entry(self, evidence_id: str) -> dict[str, Any]:
        catalog = self._load_fixture(self.fixture_root / "evidence" / "search_results.json")
        for entry in catalog["entries"]:
            if entry.get("evidence_id") == evidence_id:
                if entry["status"] == "TOOL_FAILURE":
                    raise MCPToolError(entry["error_code"], entry["message"])
                return entry
        raise MCPToolError("NOT_FOUND", "fixed evidence does not exist")

    def evidence_search(
        self,
        consumer: str,
        work_order_id: str,
        run_id: str,
        trace_id: str,
        query_type: str,
        evidence_id: str,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            work = self._authorize(
                consumer=consumer, work_order_id=work_order_id, run_id=run_id, trace_id=trace_id
            )
            if ("EVIDENCE", evidence_id) not in {
                (item["ref_type"], item["ref_id"]) for item in work.input_refs
            }:
                raise MCPToolError("PERMISSION_DENIED", "evidence is not authorized by the work order")
            if _value(work.aggregate_type) == "APPEAL":
                appeal = self.session.get(Appeal, work.aggregate_id)
                if appeal is None or evidence_id not in appeal.allowed_evidence_ids:
                    raise MCPToolError("PERMISSION_DENIED", "evidence is outside the appeal whitelist")
            entry = self._evidence_entry(evidence_id)
            if entry["query_type"] != query_type:
                raise MCPToolError("NOT_FOUND", "fixed evidence does not exist for this query type")
            if entry["status"] == "INCOMPLETE_DATA":
                raise MCPToolError("INCOMPLETE_DATA", entry["message"], entry["data"])
            return entry["data"]

        return self._call("evidence.search", work_order_id, operation)

    def artifact_put(
        self, consumer: str, work_order_id: str, run_id: str, trace_id: str, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            work = self._authorize(
                consumer=consumer, work_order_id=work_order_id, run_id=run_id, trace_id=trace_id
            )
            submitted = artifact
            # Fixed-demo compact form: the domain Agent submits only its
            # schema payload.  WorkOrder-bound envelope fields are authority
            # data, so the MCP service derives them instead of asking an LLM
            # to copy twelve identifiers and compute a hash in shell text.
            # Full envelopes remain supported for stage-one compatibility.
            if "artifact_id" not in submitted:
                payload = deepcopy(submitted.get("payload", submitted))
                if _value(work.expected_artifact_type) == "POLICY_PROPOSAL":
                    candidate = payload.get("candidate_policy")
                    if isinstance(candidate, dict) and not candidate.get("content_hash"):
                        candidate["content_hash"] = policy_hash_value(candidate)
                submitted = {
                    "artifact_id": f"ART-{work_order_id}",
                    "artifact_type": _value(work.expected_artifact_type),
                    "schema_version": "1.0",
                    "aggregate_type": _value(work.aggregate_type),
                    "aggregate_id": work.aggregate_id,
                    "work_order_id": work_order_id,
                    "run_id": run_id,
                    "trace_id": trace_id,
                    "producer_type": "AGENT",
                    "producer_id": consumer,
                    "payload": payload,
                    "content_hash": canonical_hash(payload),
                    "created_at": datetime.now(UTC).isoformat(),
                }
            else:
                if submitted.get("work_order_id") != work_order_id:
                    raise MCPToolError("VALIDATION_ERROR", "artifact work order does not match request")
                if submitted.get("run_id") != run_id or submitted.get("trace_id") != trace_id:
                    raise MCPToolError("VALIDATION_ERROR", "artifact run or trace does not match request")
                if submitted.get("producer_id") != consumer:
                    raise MCPToolError("PERMISSION_DENIED", "consumer cannot submit for another producer")
            existing = self.session.scalar(
                select(Artifact).where(
                    Artifact.work_order_id == work_order_id,
                    Artifact.run_id == run_id,
                    Artifact.artifact_type == submitted.get("artifact_type"),
                )
            )
            try:
                row = self.kernel.accept_artifact(submitted)
            except ConflictError as exc:
                raise MCPToolError("CONFLICT", str(exc)) from exc
            except (PydanticValidationError, ValidationError, StateTransitionError, ValueError) as exc:
                raise MCPToolError("VALIDATION_ERROR", str(exc)) from exc
            return {"artifact_id": row.artifact_id, "idempotent": existing is not None}

        return self._call_mutation("artifact.put", work_order_id, operation)

    def replay_execute(
        self,
        consumer: str,
        work_order_id: str,
        run_id: str,
        trace_id: str,
        replay_id: str,
        proposal_artifact_id: str,
        dataset_version: str,
    ) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            work = self._authorize(
                consumer=consumer, work_order_id=work_order_id, run_id=run_id, trace_id=trace_id
            )
            if _value(work.step_type) != "REPLAY" or _value(work.expected_artifact_type) != "REPLAY_REPORT":
                raise MCPToolError("PERMISSION_DENIED", "only the replay work order can execute replay")
            refs = {(item["ref_type"], item["ref_id"]) for item in work.input_refs}
            if ("ARTIFACT", proposal_artifact_id) not in refs or ("REPLAY_DATASET", dataset_version) not in refs:
                raise MCPToolError("PERMISSION_DENIED", "replay inputs are outside the work order")
            existing = self.session.get(ReplayRun, replay_id)
            if existing is not None:
                if (
                    existing.evolution_id == work.aggregate_id
                    and existing.proposal_artifact_id == proposal_artifact_id
                    and existing.dataset_version == dataset_version
                ):
                    return self._replay_data(existing)
                raise MCPToolError("CONFLICT", "replay_id already binds different immutable inputs")
            try:
                row = self.kernel.execute_replay(
                    replay_id=replay_id,
                    evolution_id=work.aggregate_id,
                    proposal_artifact_id=proposal_artifact_id,
                    dataset=self._load_fixture(
                        self.fixture_root / "replay" / dataset_version / "dataset.json"
                    ),
                )
            except ConflictError as exc:
                raise MCPToolError("CONFLICT", str(exc)) from exc
            except (ValidationError, StateTransitionError, ValueError) as exc:
                raise MCPToolError("VALIDATION_ERROR", str(exc)) from exc
            return self._replay_data(row)

        return self._call_mutation("replay.execute", work_order_id, operation)

    @staticmethod
    def _replay_data(row: ReplayRun) -> dict[str, Any]:
        return {
            "replay_id": row.replay_id,
            "proposal_artifact_id": row.proposal_artifact_id,
            "baseline_policy": {"policy_id": row.baseline_policy_id, "version": row.baseline_policy_version},
            "candidate_policy": {"policy_id": row.candidate_policy_id, "version": row.candidate_policy_version},
            "dataset_version": row.dataset_version,
            "dataset_manifest_hash": row.dataset_manifest_hash,
            "metrics": row.metrics_json,
            "case_results": row.case_results_json,
            "recommendation": _value(row.recommendation),
        }


def create_mcp_server(service: JudgeFlowMCPService, consumer: str):
    """Bind exactly five tools to the official local FastMCP transport."""

    from mcp.server.fastmcp import FastMCP

    server = FastMCP("judgeflow-mcp")

    @server.tool(name="work_order.get")
    def work_order_get(work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        return service.work_order_get(consumer, work_order_id, run_id, trace_id)

    @server.tool(name="context.get")
    def context_get(work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        return service.context_get(consumer, work_order_id, run_id, trace_id)

    @server.tool(name="evidence.search")
    def evidence_search(
        work_order_id: str, run_id: str, trace_id: str, query_type: str, evidence_id: str
    ) -> dict[str, Any]:
        return service.evidence_search(consumer, work_order_id, run_id, trace_id, query_type, evidence_id)

    @server.tool(name="artifact.put")
    def artifact_put(
        work_order_id: str, run_id: str, trace_id: str, artifact: dict[str, Any]
    ) -> dict[str, Any]:
        return service.artifact_put(consumer, work_order_id, run_id, trace_id, artifact)

    @server.tool(name="replay.execute")
    def replay_execute(
        work_order_id: str,
        run_id: str,
        trace_id: str,
        replay_id: str,
        proposal_artifact_id: str,
        dataset_version: str,
    ) -> dict[str, Any]:
        return service.replay_execute(
            consumer,
            work_order_id,
            run_id,
            trace_id,
            replay_id,
            proposal_artifact_id,
            dataset_version,
        )

    return server
