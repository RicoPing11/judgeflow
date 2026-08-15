from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("case-investigation", "case-deliberation", "policy-evolution", "case-replay")


def test_four_skills_have_only_the_required_handbook_and_three_examples() -> None:
    for name in SKILLS:
        skill = ROOT / "skills" / name
        assert (skill / "SKILL.md").is_file()
        examples = skill / "examples"
        assert {path.name for path in examples.glob("*.json")} == {
            "success.json",
            "missing_information.json",
            "tool_failure.json",
        }
        assert {json.loads(path.read_text(encoding="utf-8"))["scenario"] for path in examples.glob("*.json")} == {
            "success",
            "missing_information",
            "tool_failure",
        }


def test_skill_boundaries_are_explicit() -> None:
    investigation = (ROOT / "skills/case-investigation/SKILL.md").read_text(encoding="utf-8")
    deliberation = (ROOT / "skills/case-deliberation/SKILL.md").read_text(encoding="utf-8")
    evolution = (ROOT / "skills/policy-evolution/SKILL.md").read_text(encoding="utf-8")
    replay = (ROOT / "skills/case-replay/SKILL.md").read_text(encoding="utf-8")
    assert "工具失败解释为没有数据" in investigation
    assert "allowed_evidence_ids" in deliberation
    assert "不得读取 `SCORING`" in evolution
    assert "Python 回放器" in replay and "禁止自行计算" in replay
