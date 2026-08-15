from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.postgres_seed import database_summary, run_seed, verify_seed


def test_postgresql_seed_runs_three_isolated_agent_chains_and_persists_after_reconnect() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL 未设置；阶段四 PostgreSQL 持久化链路未验证")
    engine = create_engine(database_url)
    tags = ["MCP1", "MCP2", "MCP3"]
    try:
        with Session(engine) as session:
            first = [run_seed(session, tag) for tag in tags]
            assert all(result["case_status"] == "DECIDED" for result in first)
            assert all(result["evolution_status"] == "AWAITING_APPROVAL" for result in first)
            assert all(result["work_orders"] == result["artifacts"] == 8 for result in first)
            assert len({result["candidate_policy"] for result in first}) == 3
            assert len({result["replay_id"] for result in first}) == 3
        with Session(engine) as reconnected:
            persisted = [verify_seed(reconnected, tag) for tag in tags]
            assert persisted == first
            summary = database_summary(reconnected)
            assert summary["cases"] >= 3
            assert summary["appeals"] >= 3
            assert summary["policy_evolutions"] >= 3
            assert summary["work_orders"] >= 24
            assert summary["artifacts"] >= 24
            assert summary["policies"] >= 4
            assert summary["replay_runs"] >= 3
            assert summary["domain_events"] >= 3
            # Repeating the same seed IDs is verification-only and does not duplicate data.
            assert [run_seed(reconnected, tag) for tag in tags] == persisted
            assert database_summary(reconnected) == summary
    finally:
        engine.dispose()
