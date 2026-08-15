from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql

from app.core.kernel import (
    APPEAL_TRANSITIONS,
    CASE_TRANSITIONS,
    EVOLUTION_TRANSITIONS,
    STEP_ARTIFACTS,
    canonical_hash,
)
from app.models.database import (
    AppealStatus,
    Base,
    CaseStatus,
    EvolutionStatus,
)
from app.replay import PolicyExecutionError, execute_policy, replay_policies


ROOT = Path(__file__).resolve().parents[1]


def load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_database_model_has_exactly_the_eight_designed_business_tables() -> None:
    assert set(Base.metadata.tables) == {
        "cases",
        "appeals",
        "policy_evolutions",
        "work_orders",
        "artifacts",
        "policies",
        "replay_runs",
        "domain_events",
    }
    for table in Base.metadata.sorted_tables:
        # Every table and PostgreSQL-specific JSONB definition must compile for PostgreSQL.
        str(table.compile(dialect=postgresql.dialect()))


def test_only_the_fixed_state_transitions_exist() -> None:
    assert CASE_TRANSITIONS == {
        CaseStatus.NEW: CaseStatus.INVESTIGATING,
        CaseStatus.INVESTIGATING: CaseStatus.ARGUING,
        CaseStatus.ARGUING: CaseStatus.ADJUDICATING,
        CaseStatus.ADJUDICATING: CaseStatus.DECIDED,
    }
    assert APPEAL_TRANSITIONS == {
        AppealStatus.NEW: AppealStatus.REVIEWING,
        AppealStatus.REVIEWING: AppealStatus.DECIDED,
    }
    assert EVOLUTION_TRANSITIONS == {
        EvolutionStatus.NEW: {EvolutionStatus.ATTRIBUTING},
        EvolutionStatus.ATTRIBUTING: {EvolutionStatus.DRAFTING, EvolutionStatus.CLOSED},
        EvolutionStatus.DRAFTING: {EvolutionStatus.REPLAYING},
        EvolutionStatus.REPLAYING: {EvolutionStatus.AWAITING_APPROVAL},
        EvolutionStatus.AWAITING_APPROVAL: {EvolutionStatus.APPROVED, EvolutionStatus.REJECTED},
    }


def test_work_order_steps_have_one_fixed_aggregate_and_artifact_type() -> None:
    assert len(STEP_ARTIFACTS) == 8
    assert STEP_ARTIFACTS["RISK_ARGUMENT"] == {("CASE", "RISK_ARGUMENT")}
    assert STEP_ARTIFACTS["REPLAY"] == {
        ("POLICY_EVOLUTION", "REPLAY_REPORT"),
        ("POLICY_EVOLUTION", "HUMAN_APPROVAL"),
    }


@pytest.mark.parametrize(
    ("condition", "facts", "expected"),
    [
        ({"condition_id": "EQ", "fact": "X", "op": "EQ", "value": "a"}, {"X": "a"}, True),
        ({"condition_id": "IN", "fact": "X", "op": "IN", "value": ["a", "b"]}, {"X": "b"}, True),
        ({"condition_id": "GTE", "fact": "X", "op": "GTE", "value": 3}, {"X": 4}, True),
        ({"condition_id": "LTE", "fact": "X", "op": "LTE", "value": 3}, {"X": 4}, False),
    ],
)
def test_all_finite_dsl_operators_are_deterministic(condition: dict, facts: dict, expected: bool) -> None:
    policy = load_json("fixtures/policies/baseline_policy.json")
    policy["required_elements"] = condition
    policy["exceptions"] = {"condition_id": "NEVER", "fact": "NEVER", "op": "EQ", "value": True}
    first = execute_policy(policy, facts)
    second = execute_policy(policy, copy.deepcopy(facts))
    assert (first == "VIOLATION") is expected
    assert second == first


def test_policy_executor_rejects_unknown_operator_without_dynamic_evaluation() -> None:
    policy = load_json("fixtures/policies/baseline_policy.json")
    policy["required_elements"] = {
        "condition_id": "BAD",
        "fact": "X",
        "op": "EVAL",
        "value": "__import__('os').system('false')",
    }
    with pytest.raises(PolicyExecutionError, match="invalid policy DSL"):
        execute_policy(policy, {"X": True})


def test_fixed_replay_is_python_computed_and_repeatable() -> None:
    baseline = load_json("fixtures/policies/baseline_policy.json")
    candidate = load_json("fixtures/schema_examples/valid.json")["PolicyProposal"]["candidate_policy"]
    dataset = load_json("fixtures/replay/v1/dataset.json")
    first = replay_policies(baseline, candidate, dataset)
    second = replay_policies(copy.deepcopy(baseline), copy.deepcopy(candidate), copy.deepcopy(dataset))
    assert first == second
    assert first["metrics"] == {
        "baseline_false_positives": 0,
        "candidate_false_positives": 0,
        "baseline_false_negatives": 0,
        "candidate_false_negatives": 0,
        "changed_cases": 0,
    }
    assert first["recommendation"] == "INCONCLUSIVE"
    assert [item["split"] for item in first["case_results"]] == ["TRIGGER", "DRAFTING", "SCORING"]


def test_hashing_is_canonical_and_repeatable() -> None:
    assert canonical_hash({"b": 2, "a": [1]}) == canonical_hash({"a": [1], "b": 2})
