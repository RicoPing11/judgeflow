"""Fixed Python evaluator for the stage-one finite policy DSL."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.hashing import dataset_manifest_hash_value
from app.models.schemas import Policy, PolicyCondition


class PolicyExecutionError(ValueError):
    pass


def _evaluate(condition: PolicyCondition, facts: dict[str, Any]) -> bool:
    if condition.all_of is not None:
        return all(_evaluate(child, facts) for child in condition.all_of)
    if condition.any_of is not None:
        return any(_evaluate(child, facts) for child in condition.any_of)
    if condition.fact not in facts:
        return False
    actual = facts[condition.fact]
    expected = condition.value
    if condition.op == "EQ":
        return type(actual) is type(expected) and actual == expected
    if condition.op == "IN":
        assert isinstance(expected, list)  # guaranteed by the authoritative Pydantic schema
        return any(type(actual) is type(item) and actual == item for item in expected)
    if condition.op in {"GTE", "LTE"}:
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            return False
        if condition.op == "GTE":
            return actual >= expected
        return actual <= expected
    # Defensive even if a caller somehow bypasses Pydantic construction.
    raise PolicyExecutionError(f"unknown operator: {condition.op}")


def execute_policy(policy_data: dict[str, Any] | Policy, facts: dict[str, Any]) -> str:
    """Return VIOLATION/NO_VIOLATION with no dynamic evaluation facilities."""

    try:
        policy = policy_data if isinstance(policy_data, Policy) else Policy.model_validate(policy_data)
    except ValidationError as exc:
        raise PolicyExecutionError("invalid policy DSL") from exc
    required = _evaluate(policy.required_elements, facts)
    excepted = _evaluate(policy.exceptions, facts)
    if required and not excepted:
        return policy.decision
    return "NO_VIOLATION" if policy.decision == "VIOLATION" else "VIOLATION"


def replay_policies(
    baseline_data: dict[str, Any] | Policy,
    candidate_data: dict[str, Any] | Policy,
    dataset: dict[str, Any],
) -> dict[str, Any]:
    """Compare both immutable policies against exactly the supplied fixed manifest."""

    baseline = baseline_data if isinstance(baseline_data, Policy) else Policy.model_validate(baseline_data)
    candidate = candidate_data if isinstance(candidate_data, Policy) else Policy.model_validate(candidate_data)
    samples = dataset.get("samples")
    if not isinstance(samples, list) or not samples:
        raise PolicyExecutionError("replay dataset must contain fixed samples")
    required_splits = {"TRIGGER", "DRAFTING", "SCORING"}
    if {sample.get("split") for sample in samples} != required_splits:
        raise PolicyExecutionError("replay dataset must contain TRIGGER, DRAFTING, and SCORING")
    actual_manifest_hash = dataset_manifest_hash_value(dataset)
    if dataset.get("manifest_hash") != actual_manifest_hash:
        raise PolicyExecutionError("replay dataset manifest hash mismatch")

    results: list[dict[str, Any]] = []
    baseline_fp = baseline_fn = candidate_fp = candidate_fn = changed = 0
    for sample in samples:
        if not isinstance(sample.get("facts"), dict) or sample.get("expected_outcome") not in {
            "VIOLATION",
            "NO_VIOLATION",
        }:
            raise PolicyExecutionError("invalid replay sample")
        expected = sample["expected_outcome"]
        baseline_outcome = execute_policy(baseline, sample["facts"])
        candidate_outcome = execute_policy(candidate, sample["facts"])
        if sample["split"] == "SCORING":
            baseline_fp += int(baseline_outcome == "VIOLATION" and expected == "NO_VIOLATION")
            baseline_fn += int(baseline_outcome == "NO_VIOLATION" and expected == "VIOLATION")
            candidate_fp += int(candidate_outcome == "VIOLATION" and expected == "NO_VIOLATION")
            candidate_fn += int(candidate_outcome == "NO_VIOLATION" and expected == "VIOLATION")
            changed += int(baseline_outcome != candidate_outcome)
        results.append(
            {
                "sample_id": sample["sample_id"],
                "split": sample["split"],
                "expected_outcome": expected,
                "baseline_outcome": baseline_outcome,
                "candidate_outcome": candidate_outcome,
            }
        )

    baseline_errors = baseline_fp + baseline_fn
    candidate_errors = candidate_fp + candidate_fn
    if candidate_errors < baseline_errors:
        recommendation = "PASS"
    elif candidate_errors > baseline_errors:
        recommendation = "FAIL"
    else:
        recommendation = "INCONCLUSIVE"
    return {
        "dataset_version": dataset.get("dataset_version"),
        "dataset_manifest_hash": dataset.get("manifest_hash"),
        "metrics": {
            "baseline_false_positives": baseline_fp,
            "candidate_false_positives": candidate_fp,
            "baseline_false_negatives": baseline_fn,
            "candidate_false_negatives": candidate_fn,
            "changed_cases": changed,
        },
        "case_results": results,
        "recommendation": recommendation,
    }
