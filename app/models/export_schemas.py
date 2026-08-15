"""Deterministically export the stage 1 Pydantic contracts as JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

from app.models import TOP_LEVEL_SCHEMAS


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "schemas" / "exported"


def export_json_schemas(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for model in TOP_LEVEL_SCHEMAS:
        path = output_dir / f"{model.__name__}.schema.json"
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    export_json_schemas()
