"""scripts/om_spend.py: month-to-date spend across projects, and the cap check."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "om_spend.py"
GATE = REPO / "scripts" / "spend_gate.sh"
sys.path.insert(0, str(REPO))

import scripts.om_spend as om_spend  # noqa: E402


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def projects(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    # om_shot-style log: billed, free refusal, and a take from another month
    _write(root / "alpha" / "artifacts" / "spend_log.json", {"attempts": [
        {"shot": "shot_01", "at": "2026-09-03T10:00:00+00:00", "usd": 0.4153},
        {"shot": "shot_01", "at": "2026-09-03T10:10:00+00:00", "usd": 0.0,
         "outcome": "rejected by the privacy filter"},
        {"shot": "shot_02", "at": "2026-09-04T10:00:00+00:00", "usd": 0.6209},
        {"shot": "shot_02", "at": "2026-08-30T10:00:00+00:00", "usd": 1.0},
    ], "total_usd": 2.0362})
    # older inline contract log with `date` and `job`
    _write(root / "beta" / "artifacts" / "shot_contract.json", {"spend_log": [
        {"job": "ref_room", "date": "2026-09-16", "usd": 0.9343},
        {"job": "shot_02 attempt 1", "date": "2026-09-17", "usd": 0.0},
        {"job": "old", "usd": 0.5},
    ], "running_total_usd": 1.4343})
    (root / "gamma").mkdir()  # a project with no spend at all
    return root


class TestTotals:
    def test_billed_attempts_are_summed_per_month_and_project(self, projects):
        table = om_spend.spend_by_month(projects)
        assert table["2026-09"]["alpha"] == {"usd": 1.0362, "billed": 2}
        assert table["2026-09"]["beta"] == {"usd": 0.9343, "billed": 1}
        assert table["2026-08"]["alpha"] == {"usd": 1.0, "billed": 1}
        assert "gamma" not in table["2026-09"]

    def test_undated_money_is_kept_not_dropped(self, projects):
        table = om_spend.spend_by_month(projects)
        assert table["undated"]["beta"] == {"usd": 0.5, "billed": 1}
        assert "0.50 USD in attempts with no date" in om_spend.report(table, "2026-09", None)

    def test_month_total(self, projects):
        assert om_spend.month_total(om_spend.spend_by_month(projects), "2026-09") == 1.9705


class TestCheck:
    def test_no_cap_is_a_report_only(self, projects):
        code, message = om_spend.check(om_spend.spend_by_month(projects), "2026-09", None)
        assert (code, message) == (0, "")

    def test_under_cap_is_silent(self, projects):
        code, message = om_spend.check(om_spend.spend_by_month(projects), "2026-09", 10.0)
        assert (code, message) == (0, "")

    def test_warns_at_eighty_percent(self, projects):
        code, message = om_spend.check(om_spend.spend_by_month(projects), "2026-09", 2.2)
        assert code == 0 and message.startswith("Warning:")

    def test_cap_reached_exits_three(self, projects):
        rc = subprocess.run([sys.executable, str(SCRIPT), "--projects-dir", str(projects),
                             "--month", "2026-09", "--cap", "1.5", "--check"],
                            capture_output=True, text=True)
        assert rc.returncode == 3
        assert "cap reached" in rc.stdout

    def test_cap_comes_from_the_environment(self, projects, monkeypatch):
        monkeypatch.setenv("OM_MONTHLY_CAP_USD", "1")
        rc = subprocess.run([sys.executable, str(SCRIPT), "--projects-dir", str(projects),
                             "--month", "2026-09", "--check"], capture_output=True, text=True)
        assert rc.returncode == 3

    def test_unset_cap_is_none(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OM_MONTHLY_CAP_USD", raising=False)
        assert om_spend.read_cap(tmp_path) is None


@pytest.mark.skipif(not (REPO / ".venv" / "bin" / "python").exists(), reason="needs the repo venv")
class TestGateHook:
    def _run(self, command: str, projects: Path, cap: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(GATE)], input=json.dumps({"tool_input": {"command": command}}),
                              capture_output=True, text=True,
                              env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(REPO),
                                   "OPENMONTAGE_PROJECTS_DIR": str(projects), "OM_MONTHLY_CAP_USD": cap})

    def test_shot_go_is_denied_once_the_cap_is_reached(self, projects):
        out = self._run("make shot-go PROJECT=alpha SHOT=shot_03", projects, "1")
        decision = json.loads(out.stdout)["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "cap reached" in decision["permissionDecisionReason"]

    def test_shot_go_passes_under_the_cap(self, projects):
        out = self._run("make shot-go PROJECT=alpha SHOT=shot_03", projects, "100")
        assert out.returncode == 0 and out.stdout.strip() == ""

    def test_other_commands_are_untouched(self, projects):
        out = self._run("make shot PROJECT=alpha SHOT=shot_03", projects, "1")
        assert out.returncode == 0 and out.stdout.strip() == ""
