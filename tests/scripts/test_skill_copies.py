"""The project skills exist twice: .agents/skills is the source Layer 3 tools point at, and
.claude/skills is what Claude Code loads. They are synced by hand, so drift is a silent bug."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.mark.parametrize("skill", ["open-montage", "seedance-2-5"])
def test_both_copies_of_a_team_skill_are_identical(skill):
    source, loaded = ROOT / ".agents" / "skills" / skill, ROOT / ".claude" / "skills" / skill
    files = sorted(p.relative_to(source) for p in source.rglob("*") if p.is_file())
    assert files == sorted(p.relative_to(loaded) for p in loaded.rglob("*") if p.is_file())
    for rel in files:
        assert (source / rel).read_bytes() == (loaded / rel).read_bytes(), f"{skill}/{rel} differs between the copies"
