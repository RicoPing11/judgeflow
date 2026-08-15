from __future__ import annotations

from pathlib import Path

from app.models import TOP_LEVEL_SCHEMAS
from app.models.export_schemas import export_json_schemas


ROOT = Path(__file__).resolve().parents[1]


def directory_snapshot(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.glob("*.schema.json"))}


def test_every_pydantic_schema_exports_json_schema(tmp_path: Path) -> None:
    paths = export_json_schemas(tmp_path)
    assert len(paths) == len(TOP_LEVEL_SCHEMAS)
    assert all(path.read_text(encoding="utf-8").endswith("\n") for path in paths)
    assert all('"$defs"' in path.read_text(encoding="utf-8") or '"properties"' in path.read_text(encoding="utf-8") for path in paths)


def test_schema_export_is_repeatable_and_matches_checked_in_files(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    export_json_schemas(first)
    export_json_schemas(second)
    expected = directory_snapshot(ROOT / "schemas" / "exported")
    assert directory_snapshot(first) == directory_snapshot(second)
    assert directory_snapshot(first) == expected
