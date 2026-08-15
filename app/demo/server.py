"""Small stdlib HTTP server for the stage-six single-page demo."""

from __future__ import annotations

import argparse
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.demo import DemoConflict, DemoNotFound, DemoService


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"


class DemoHandler(BaseHTTPRequestHandler):
    engine = None

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if path == "/api/demo/overview":
            self._service_call(lambda service: service.overview(
                self._datetime(query, "from"), self._datetime(query, "to")
            ))
            return
        if path == "/api/demo/cases":
            self._service_call(lambda service: service.list_cases(
                self._text(query, "q"), self._text(query, "status"), self._text(query, "policy_id"),
                self._integer(query, "page", 1), self._integer(query, "page_size", 20),
            ))
            return
        if path.startswith("/api/demo/cases/"):
            self._service_call(lambda service: service.case_detail(path.removeprefix("/api/demo/cases/")))
            return
        if path == "/api/demo/policies":
            self._service_call(lambda service: service.list_policies(
                self._text(query, "q"), self._text(query, "status"),
                self._integer(query, "page", 1), self._integer(query, "page_size", 20),
            ))
            return
        if path.startswith("/api/demo/policies/"):
            self._service_call(lambda service: service.policy_detail(
                path.removeprefix("/api/demo/policies/"), self._text(query, "version") or None,
                self._datetime(query, "from"), self._datetime(query, "to"),
            ))
            return
        if path == "/api/demo/approvals":
            self._service_call(lambda service: service.list_approvals(
                self._integer(query, "page", 1), self._integer(query, "page_size", 20)
            ))
            return
        if path.startswith("/api/demo/replays/"):
            self._service_call(lambda service: service.replay_detail(
                path.removeprefix("/api/demo/replays/"), self._text(query, "impact"), self._text(query, "q"),
                self._integer(query, "page", 1), self._integer(query, "page_size", 20),
            ))
            return
        if path.startswith("/api/demo/runs/"):
            demo_run_id = path.removeprefix("/api/demo/runs/")
            self._service_call(lambda service: service.view(demo_run_id))
            return
        files = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css",
                 "/refinement.css": "refinement.css", "/app.js": "app.js"}
        filename = files.get(path)
        if filename is None:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "state": "NOT_FOUND", "error": "页面不存在"})
            return
        target = FRONTEND / filename
        content_type = {".html": "text/html", ".css": "text/css", ".js": "text/javascript"}[target.suffix]
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        path = unquote(urlparse(self.path).path)
        body = self._body()
        demo_run_id = body.get("demo_run_id")
        if not isinstance(demo_run_id, str):
            self._json(HTTPStatus.BAD_REQUEST, {"ok": False, "state": "FAILURE", "error": "缺少 demo_run_id"})
            return
        if path == "/api/demo/runs":
            self._service_call(lambda service: service.start(demo_run_id))
        elif path == "/api/demo/approve":
            self._service_call(lambda service: service.approve(demo_run_id))
        elif path == "/api/demo/failures":
            self._service_call(lambda service: service.create_failure(demo_run_id))
        else:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "state": "NOT_FOUND", "error": "接口不存在"})

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length) or b"{}")
            return value if isinstance(value, dict) else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def _service_call(self, operation) -> None:
        try:
            with Session(self.engine) as session:
                self._json(HTTPStatus.OK, operation(DemoService(session)))
        except DemoNotFound as exc:
            self._json(HTTPStatus.NOT_FOUND, {"ok": False, "state": "NOT_FOUND", "error": str(exc)})
        except DemoConflict as exc:
            self._json(HTTPStatus.CONFLICT, {"ok": False, "state": "FAILURE", "error": str(exc)})
        except Exception:
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                "ok": False, "state": "FAILURE",
                "error": "后端执行失败；这不是业务无数据，请检查服务日志。",
            })

    @staticmethod
    def _text(query: dict[str, list[str]], name: str) -> str:
        return query.get(name, [""])[0].strip()

    @classmethod
    def _integer(cls, query: dict[str, list[str]], name: str, default: int) -> int:
        try:
            return int(cls._text(query, name) or default)
        except ValueError as exc:
            raise DemoConflict(f"{name} 必须是整数") from exc

    @classmethod
    def _datetime(cls, query: dict[str, list[str]], name: str) -> datetime | None:
        value = cls._text(query, name)
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DemoConflict(f"{name} 必须是 ISO 8601 时间") from exc
        if parsed.tzinfo is None:
            raise DemoConflict(f"{name} 必须包含时区")
        return parsed

    def _json(self, status: HTTPStatus, value: dict) -> None:
        data = json.dumps(value, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"demo-http {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="JudgeFlow stage-six single-page demo")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8086)
    args = parser.parse_args()
    engine = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    DemoHandler.engine = engine
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    try:
        print(f"JudgeFlow Demo: http://{args.host}:{args.port}", flush=True)
        server.serve_forever()
    finally:
        server.server_close()
        engine.dispose()


if __name__ == "__main__":
    main()
