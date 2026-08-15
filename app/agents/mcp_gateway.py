"""Synchronous stage-four adapter over the official FastMCP tool dispatcher."""

from __future__ import annotations

import asyncio
from typing import Any

from app.mcp import JudgeFlowMCPService, create_mcp_server


class FastMCPGateway:
    """Invoke the registered stage-three tools through FastMCP ``call_tool``.

    The adapter stays in-process for the pre-AgentTeams acceptance, but it does
    exercise official MCP tool registration, input validation and dispatch rather
    than calling JudgeFlowMCPService methods directly.
    """

    def __init__(self, service: JudgeFlowMCPService):
        self.service = service
        self._servers: dict[str, Any] = {}

    def _call(self, consumer: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        server = self._servers.setdefault(consumer, create_mcp_server(self.service, consumer))
        _content, result = asyncio.run(server.call_tool(tool, arguments))
        return result

    def work_order_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        return self._call(consumer, "work_order.get", {"work_order_id": work_order_id, "run_id": run_id, "trace_id": trace_id})

    def context_get(self, consumer: str, work_order_id: str, run_id: str, trace_id: str) -> dict[str, Any]:
        return self._call(consumer, "context.get", {"work_order_id": work_order_id, "run_id": run_id, "trace_id": trace_id})

    def evidence_search(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, query_type: str, evidence_id: str) -> dict[str, Any]:
        return self._call(consumer, "evidence.search", {"work_order_id": work_order_id, "run_id": run_id, "trace_id": trace_id, "query_type": query_type, "evidence_id": evidence_id})

    def artifact_put(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        return self._call(consumer, "artifact.put", {"work_order_id": work_order_id, "run_id": run_id, "trace_id": trace_id, "artifact": artifact})

    def replay_execute(self, consumer: str, work_order_id: str, run_id: str, trace_id: str, replay_id: str, proposal_artifact_id: str, dataset_version: str) -> dict[str, Any]:
        return self._call(consumer, "replay.execute", {"work_order_id": work_order_id, "run_id": run_id, "trace_id": trace_id, "replay_id": replay_id, "proposal_artifact_id": proposal_artifact_id, "dataset_version": dataset_version})
