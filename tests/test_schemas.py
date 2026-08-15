from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models import (
    ArtifactEnvelope,
    EvidenceRef,
    Policy,
    PolicyCondition,
    PolicySnapshot,
    TOP_LEVEL_SCHEMAS,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "fixtures" / "schema_examples"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def valid_examples() -> dict:
    return load_json(EXAMPLES / "valid.json")


@pytest.fixture(scope="module")
def invalid_examples() -> dict:
    return load_json(EXAMPLES / "invalid.json")


def test_every_top_level_schema_has_valid_and_invalid_example(valid_examples: dict, invalid_examples: dict) -> None:
    expected = {model.__name__ for model in TOP_LEVEL_SCHEMAS}
    assert set(valid_examples) == expected
    assert set(invalid_examples) == expected


@pytest.mark.parametrize("model", TOP_LEVEL_SCHEMAS, ids=lambda model: model.__name__)
def test_all_valid_examples_pass(model: type, valid_examples: dict) -> None:
    model.model_validate(valid_examples[model.__name__])


@pytest.mark.parametrize("model", TOP_LEVEL_SCHEMAS, ids=lambda model: model.__name__)
def test_all_invalid_examples_are_rejected(model: type, invalid_examples: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(invalid_examples[model.__name__])


@pytest.mark.parametrize("model", TOP_LEVEL_SCHEMAS, ids=lambda model: model.__name__)
def test_extra_fields_are_rejected_by_every_top_level_schema(model: type, valid_examples: dict) -> None:
    sample = copy.deepcopy(valid_examples[model.__name__])
    sample["unexpected_stage_two_field"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(sample)


@pytest.mark.parametrize("operator", ["EVAL", "EXEC", "PYTHON", "GT", "OR"])
def test_policy_dsl_rejects_unknown_or_executable_operators(operator: str) -> None:
    with pytest.raises(ValidationError):
        PolicyCondition.model_validate(
            {"condition_id": "BAD", "fact": "X", "op": operator, "value": "__import__('os')"}
        )


def test_policy_dsl_rejects_script_and_template_fields() -> None:
    with pytest.raises(ValidationError):
        PolicyCondition.model_validate({"script": "return true"})
    with pytest.raises(ValidationError):
        PolicyCondition.model_validate({"template": "{{ dangerous_call() }}"})


@pytest.mark.parametrize("name", ["RiskArgument", "CounterArgument", "DecisionRecord", "AppealDecision"])
def test_every_formal_conclusion_has_evidence_and_policy_clause(name: str, valid_examples: dict) -> None:
    model = next(model for model in TOP_LEVEL_SCHEMAS if model.__name__ == name)
    artifact = model.model_validate(valid_examples[name])
    for conclusion in artifact.conclusions:
        assert conclusion.evidence_id
        assert conclusion.policy_ref.policy_id
        assert conclusion.policy_ref.version
        assert conclusion.policy_ref.clause_ids


def test_artifact_type_must_match_payload(valid_examples: dict) -> None:
    sample = copy.deepcopy(valid_examples["ArtifactEnvelope"])
    sample["artifact_type"] = "RISK_ARGUMENT"
    with pytest.raises(ValidationError):
        ArtifactEnvelope.model_validate(sample)


def test_policy_snapshot_locks_policy_version_and_hash(valid_examples: dict) -> None:
    snapshot = PolicySnapshot.model_validate(valid_examples["PolicySnapshot"])
    assert snapshot.policies[0].policy_id == "MINOR_DANGEROUS_ACT"
    assert snapshot.policies[0].version == "1.0"
    assert snapshot.policies[0].content_hash.startswith("sha256:")
    assert snapshot.snapshot_hash.startswith("sha256:")


def test_fixed_demo_fixtures_are_minimal_and_valid() -> None:
    case = load_json(ROOT / "fixtures" / "case" / "main_case.json")
    appeal = load_json(ROOT / "fixtures" / "appeal" / "appeal_request.json")
    new_evidence = load_json(ROOT / "fixtures" / "appeal" / "new_evidence.json")
    policy = load_json(ROOT / "fixtures" / "policies" / "baseline_policy.json")
    snapshot = load_json(ROOT / "fixtures" / "policies" / "policy_snapshot.json")
    replay = load_json(ROOT / "fixtures" / "replay" / "v1" / "dataset.json")

    assert case["case_id"] == appeal["case_id"] == "CASE-001"
    assert appeal["allowed_new_evidence_ids"] == [new_evidence["evidence_id"]]
    EvidenceRef.model_validate(new_evidence)
    Policy.model_validate(policy)
    locked = PolicySnapshot.model_validate(snapshot)
    assert locked.policies[0].version == policy["version"]
    assert locked.policies[0].content_hash == policy["content_hash"]
    assert {sample["split"] for sample in replay["samples"]} == {"TRIGGER", "DRAFTING", "SCORING"}
    assert len(replay["samples"]) == 3
