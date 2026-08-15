"""Canonical SHA-256 helpers shared by persistence and deterministic replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def policy_hash_value(policy: dict[str, Any]) -> str:
    """Hash the human text and executable DSL, excluding identity/status/hash metadata."""

    return canonical_hash(
        {
            "title": policy["title"],
            "description": policy["description"],
            "dsl": {
                "applicability": policy["applicability"],
                "required_elements": policy["required_elements"],
                "exceptions": policy["exceptions"],
                "decision": policy["decision"],
            },
        }
    )


def snapshot_hash_value(snapshot: dict[str, Any]) -> str:
    return canonical_hash({"snapshot_id": snapshot["snapshot_id"], "policies": snapshot["policies"]})


def dataset_manifest_hash_value(dataset: dict[str, Any]) -> str:
    return canonical_hash({"dataset_version": dataset["dataset_version"], "samples": dataset["samples"]})
