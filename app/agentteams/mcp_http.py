"""One Streamable HTTP transport for the existing five JudgeFlow MCP tools.

Higress key-auth supplies the authenticated Agent identity in X-Mse-Consumer.
Business authorization remains in JudgeFlowMCPService; this adapter adds no
custom guard and exposes no caller-controlled identity tool argument.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.agents.specs import AGENT_SPECS
from app.mcp import JudgeFlowMCPService


def _error(tool: str, work_order_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message},
        "audit_ref": f"mcp:{tool}:{work_order_id}",
    }


def _identity(ctx: Context) -> str:
    request = ctx.request_context.request
    if not isinstance(request, Request):
        raise PermissionError("HTTP request context is required")
    consumer = request.headers.get("x-mse-consumer", "")
    if consumer.startswith("worker-"):
        consumer = consumer.removeprefix("worker-")
    if consumer not in AGENT_SPECS:
        raise PermissionError("authenticated consumer is not a JudgeFlow domain Agent")
    return consumer


def create_http_mcp_server(
    session_factory: Callable[[], Session], *, trust_native_gateway: bool = False
) -> FastMCP:
    if not trust_native_gateway:
        raise RuntimeError("native Higress identity boundary must be established before MCP startup")
    server = FastMCP(
        "judgeflow-mcp",
        host="0.0.0.0",
        port=8765,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
    )

    def invoke(
        tool: str,
        work_order_id: str,
        ctx: Context,
        operation: Callable[[JudgeFlowMCPService, str], dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            consumer = _identity(ctx)
        except PermissionError as exc:
            return _error(tool, work_order_id, "PERMISSION_DENIED", str(exc))
        if tool not in AGENT_SPECS[consumer].allowed_tools:
            return _error(tool, work_order_id, "PERMISSION_DENIED", "tool is outside the Agent contract")
        with session_factory() as session:
            return operation(JudgeFlowMCPService(session), consumer)

    @server.tool(name="work_order.get")
    def work_order_get(work_order_id: str, run_id: str, trace_id: str, ctx: Context) -> dict[str, Any]:
        return invoke(
            "work_order.get", work_order_id, ctx,
            lambda service, consumer: service.work_order_get(consumer, work_order_id, run_id, trace_id),
        )

    @server.tool(name="context.get")
    def context_get(work_order_id: str, run_id: str, trace_id: str, ctx: Context) -> dict[str, Any]:
        return invoke(
            "context.get", work_order_id, ctx,
            lambda service, consumer: service.context_get(consumer, work_order_id, run_id, trace_id),
        )

    @server.tool(name="evidence.search")
    def evidence_search(
        work_order_id: str,
        run_id: str,
        trace_id: str,
        query_type: str,
        evidence_id: str,
        ctx: Context,
    ) -> dict[str, Any]:
        return invoke(
            "evidence.search", work_order_id, ctx,
            lambda service, consumer: service.evidence_search(
                consumer, work_order_id, run_id, trace_id, query_type, evidence_id
            ),
        )

    @server.tool(name="artifact.put")
    def artifact_put(
        work_order_id: str,
        run_id: str,
        trace_id: str,
        artifact: dict[str, Any],
        ctx: Context,
    ) -> dict[str, Any]:
        return invoke(
            "artifact.put", work_order_id, ctx,
            lambda service, consumer: service.artifact_put(
                consumer, work_order_id, run_id, trace_id, artifact
            ),
        )

    @server.tool(name="replay.execute")
    def replay_execute(
        work_order_id: str,
        run_id: str,
        trace_id: str,
        replay_id: str,
        proposal_artifact_id: str,
        dataset_version: str,
        ctx: Context,
    ) -> dict[str, Any]:
        return invoke(
            "replay.execute", work_order_id, ctx,
            lambda service, consumer: service.replay_execute(
                consumer,
                work_order_id,
                run_id,
                trace_id,
                replay_id,
                proposal_artifact_id,
                dataset_version,
            ),
        )

    @server.custom_route("/healthz", methods=["GET"])
    async def healthz(_request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "transport": "streamable-http", "tools": 5})

    return server


def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_engine(database_url)
    if os.environ.get("JUDGEFLOW_TRUST_NATIVE_HIGRESS") != "1":
        raise RuntimeError("set JUDGEFLOW_TRUST_NATIVE_HIGRESS=1 only behind authenticated Higress")
    create_http_mcp_server(lambda: Session(engine), trust_native_gateway=True).run(
        transport="streamable-http"
    )


if __name__ == "__main__":
    main()
