"""Unit tests for the open-montage shot board (backlot/shots.py).

Every fixture is built in tmp_path — never against projects/, which holds the
user's real (gitignored) work.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backlot import server as server_mod
from backlot import state as state_mod
from backlot.shots import load_shot_board, take_letters
from backlot.state import load_board_state, summarize_project


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_media(path: Path, body: bytes = b"fake") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


CONTRACT = {
    "project": "night-run",
    "route": {"tool": "seedance_ark", "model_variant": "2.5", "resolution": "480p"},
    "budget_usd": 6.0,
    "max_attempts_per_shot": 3,
    "runtime_seconds": 9,
    "grade_preset": "amber-blue-night",
    "duration_note": "shots under 4 s are generated long and trimmed in the edit",
    "reference_set_accepted": {"by": "user", "date": "2026-09-20"},
    "shots": [
        {
            "id": "shot_01", "title": "Wake", "brief_timecode": "0:00-0:03",
            "seconds_generated": 4, "seconds_delivered": 3,
            "lens": "47 degrees", "action": "he opens his eyes",
            "frame_contains": ["his face", "the pillow"],
            "reference_ids": ["assets/reference/room.png"],
        },
        {
            "id": "shot_02", "title": "Stand", "brief_timecode": "0:03-0:09",
            "seconds_generated": 6, "seconds_delivered": 6,
            "reference_ids": ["assets/reference/room.png", "assets/reference/window.png"],
        },
    ],
}


@pytest.fixture
def shot_project(tmp_path) -> Path:
    """A finished open-montage run: two shots, three takes, one final cut."""
    project = tmp_path / "projects" / "night-run"
    _write_json(project / "artifacts" / "shot_contract.json", CONTRACT)
    _write_json(project / "artifacts" / "brief_checklist.json", {
        "deliverable": {"seconds": 9, "shots": 2, "transitions": "hard cut"},
        "entries": [
            {"id": "c01", "tag": "character", "priority": "must", "literal": "a man"},
            {"id": "e01", "tag": "environment", "priority": "should", "literal": "a bedroom"},
        ],
        "conflicts": [{"id": "x01", "issue": "the lamp and the window are on one side", "status": "resolved"}],
        "decisions": [{
            "subject": "Window position", "chosen": "foot wall",
            "options_considered": ["foot wall", "side wall", "two windows"],
            "rejected_because": {"side wall": "no cool side on the face",
                                 "two windows": "a second blue source competes"},
        }],
    })
    (project / "artifacts" / "brief_source.md").parent.mkdir(parents=True, exist_ok=True)
    (project / "artifacts" / "brief_source.md").write_text(
        "# Brief\n\nNine seconds, two hard-cut shots in a dim bedroom.\n", encoding="utf-8")

    _write_media(project / "assets" / "reference" / "room.png")
    _write_media(project / "assets" / "reference" / "window.png")
    _write_media(project / "assets" / "reference" / "spare.png.unused")
    (project / "assets" / "reference" / "README.md").write_text("mapping", encoding="utf-8")
    _write_json(project / "assets" / "reference" / "room.review.json",
                {"verdict": "accept", "why": "covers the set dressing"})

    for name in ("shot_01.mp4", "shot_02.attempt1.mp4", "shot_02.mp4"):
        _write_media(project / "assets" / "video" / name)
    # om_assemble intermediates must never be mistaken for takes
    _write_media(project / "assets" / "video" / "work" / "01_shot_01.part.mp4")

    (project / "artifacts" / "prompts").mkdir(parents=True, exist_ok=True)
    (project / "artifacts" / "prompts" / "shot_01.txt").write_text("SHOT: he wakes", encoding="utf-8")
    (project / "artifacts" / "prompts" / "shot_02.txt").write_text("SHOT: he stands", encoding="utf-8")

    _write_json(project / "artifacts" / "adherence" / "shot_01.json", {
        "shot_id": "shot_01", "clip": "assets/video/shot_01.mp4", "attempt": 1,
        "verdict": "accept", "actual": {"cost_usd": 0.41},
    })
    _write_json(project / "artifacts" / "adherence" / "shot_02.json", {
        "shot_id": "shot_02", "clip": "assets/video/shot_02.mp4", "attempt": 2,
        "verdict": "accept", "actual": {"cost_usd": 0.62},
        "superseded": {"shot_02.attempt1.mp4": "he materialised at 2.2 s"},
    })
    _write_media(project / "artifacts" / "adherence" / "shot_01_sheet.png")
    _write_media(project / "artifacts" / "adherence" / "shot_01_first.png")

    _write_json(project / "artifacts" / "spend_log.json", {
        "attempts": [
            {"shot": "shot_01", "outcome": "succeeded", "usd": 0.41},
            {"shot": "shot_02", "outcome": "succeeded", "usd": 0.62},
            {"shot": "shot_02", "outcome": "output_likeness_filter", "usd": 0.0},
            {"shot": "shot_02", "outcome": "succeeded", "usd": 0.62},
        ],
        "total_usd": 1.65,
    })

    _write_json(project / "artifacts" / "final" / "cut_report.json", {
        "shots": [{"id": "shot_01"}, {"id": "shot_02"}], "cuts_at": [3.0],
        "runtime_seconds": 9.0, "runtime_expected": 9, "runtime_ok": True,
        "grade": "amber-blue-night", "edited_from": "the shot contract",
    })
    _write_media(project / "artifacts" / "final" / "cut_sheet.png")
    _write_media(project / "renders" / "night-run.mp4")
    return project


@pytest.fixture
def projects_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
    return root


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

class TestLabels:
    def test_take_letters_run_past_z(self):
        assert [take_letters(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]

    def test_shots_and_takes_are_labelled(self, shot_project):
        board = load_shot_board(shot_project)
        assert [shot["label"] for shot in board["shots"]] == ["S1", "S2"]
        assert [t["label"] for t in board["shots"][0]["takes"]] == ["S1-A"]
        assert [t["label"] for t in board["shots"][1]["takes"]] == ["S2-A", "S2-B"]
        assert [r["label"] for r in board["references"]["items"]] == ["R1", "R2"]
        assert board["cut"]["label"] == "CUT"
        assert board["brief"]["label"] == "BRIEF"

    def test_labels_are_stable_across_reloads(self, shot_project):
        first = load_shot_board(shot_project)
        second = load_shot_board(shot_project)
        assert [t["label"] for t in first["shots"][1]["takes"]] == \
               [t["label"] for t in second["shots"][1]["takes"]]
        assert [(t["label"], t["path"]) for t in first["shots"][1]["takes"]] == \
               [(t["label"], t["path"]) for t in second["shots"][1]["takes"]]

    def test_a_new_take_does_not_renumber_the_kept_ones(self, shot_project):
        """om_shot moves the current take aside before writing the new one."""
        before = {t["path"]: t["label"] for t in load_shot_board(shot_project)["shots"][1]["takes"]}
        video = shot_project / "assets" / "video"
        (video / "shot_02.mp4").rename(video / "shot_02.attempt2.mp4")
        _write_media(video / "shot_02.mp4", b"newer")
        after = {t["path"]: t["label"] for t in load_shot_board(shot_project)["shots"][1]["takes"]}
        assert after["assets/video/shot_02.attempt1.mp4"] == before["assets/video/shot_02.attempt1.mp4"]
        assert after["assets/video/shot_02.attempt2.mp4"] == before["assets/video/shot_02.mp4"]
        assert after["assets/video/shot_02.mp4"] == "S2-C"


# ---------------------------------------------------------------------------
# adapter
# ---------------------------------------------------------------------------

class TestShotBoard:
    def test_not_a_shot_project(self, tmp_path):
        (tmp_path / "artifacts").mkdir()
        assert load_shot_board(tmp_path) is None

    def test_brief_card(self, shot_project):
        brief = load_shot_board(shot_project)["brief"]
        assert brief["project"] == "night-run"
        assert brief["runtime_seconds"] == 9
        assert brief["budget_usd"] == 6.0
        assert brief["grade_preset"] == "amber-blue-night"
        assert "Nine seconds" in brief["summary"]
        assert brief["checklist"]["total"] == 2
        assert brief["checklist"]["priorities"] == {"must": 1, "should": 1}
        assert brief["checklist"]["conflicts"][0]["id"] == "x01"
        assert any(note["key"] == "duration_note" for note in brief["notes"])

    def test_reference_set(self, shot_project):
        refs = load_shot_board(shot_project)["references"]
        paths = [item["path"] for item in refs["items"]]
        assert paths == ["assets/reference/room.png", "assets/reference/window.png"]
        assert refs["readme_path"] == "assets/reference/README.md"
        assert refs["accepted"]["by"] == "user"
        room = refs["items"][0]
        assert room["kind"] == "image"
        assert room["verdict"] == "accept"
        assert room["used_by"] == ["S1", "S2"]
        assert refs["items"][1]["used_by"] == ["S2"]

    def test_every_take_is_listed_with_its_prompt_verdict_and_cost(self, shot_project):
        board = load_shot_board(shot_project)
        shot = board["shots"][1]
        assert shot["id"] == "shot_02"
        assert shot["prompt"] == "SHOT: he stands"
        assert shot["prompt_path"] == "artifacts/prompts/shot_02.txt"
        assert shot["reference_labels"] == ["R1", "R2"]
        assert shot["accepted_take"] == "S2-B"
        superseded, accepted = shot["takes"]
        assert superseded["path"] == "assets/video/shot_02.attempt1.mp4"
        assert superseded["accepted"] is False
        assert superseded["cost_usd"] == 0.62
        assert superseded["note"] == "he materialised at 2.2 s"
        assert superseded["review"] is None
        assert accepted["accepted"] is True
        assert accepted["verdict"] == "accept"
        assert accepted["review"]["shot_id"] == "shot_02"
        assert accepted["cost_usd"] == 0.62
        # the shot's own total counts the free filter refusal too
        assert shot["cost_usd"] == 1.24
        assert shot["billed_count"] == 2

    def test_a_superseded_take_keeps_its_own_verdict_when_one_was_written(self, shot_project):
        _write_json(shot_project / "artifacts" / "adherence" / "shot_02.attempt1.json", {
            "shot_id": "shot_02", "clip": "assets/video/shot_02.attempt1.mp4",
            "verdict": "regenerate", "change_next": "he materialises at 2.2 s",
        })
        superseded = load_shot_board(shot_project)["shots"][1]["takes"][0]
        assert superseded["verdict"] == "regenerate"
        assert superseded["review_path"] == "artifacts/adherence/shot_02.attempt1.json"
        assert superseded["accepted"] is False

    def test_assembly_intermediates_are_not_takes(self, shot_project):
        board = load_shot_board(shot_project)
        paths = [t["path"] for shot in board["shots"] for t in shot["takes"]]
        assert not any("work/" in p for p in paths)

    def test_sheets_follow_the_accepted_take(self, shot_project):
        board = load_shot_board(shot_project)
        assert board["shots"][0]["takes"][0]["sheets"] == [
            "artifacts/adherence/shot_01_first.png",
            "artifacts/adherence/shot_01_sheet.png",
        ]

    def test_final_cut(self, shot_project):
        cut = load_shot_board(shot_project)["cut"]
        assert cut["path"] == "renders/night-run.mp4"
        assert cut["report"]["runtime_ok"] is True
        assert cut["report_path"] == "artifacts/final/cut_report.json"
        assert cut["review"] is None
        assert cut["sheets"] == ["artifacts/final/cut_sheet.png"]

    def test_spend_from_the_spend_log(self, shot_project):
        spend = load_shot_board(shot_project)["spend"]
        assert spend["total_usd"] == 1.65
        assert spend["budget_usd"] == 6.0
        assert spend["remaining_usd"] == 4.35
        assert spend["source"] == "artifacts/spend_log.json"

    def test_spend_falls_back_to_the_contract(self, shot_project):
        """Runs older than om_shot's spend log keep the same facts inline."""
        (shot_project / "artifacts" / "spend_log.json").unlink()
        contract = dict(CONTRACT)
        contract["spend_log"] = [
            {"job": "ref_room", "usd": 0.93},
            {"job": "shot_01", "usd": 0.41},
            {"job": "shot_02 attempt 1", "usd": 0.62},
            {"job": "shot_02 attempt 2", "usd": 0.62},
        ]
        contract["running_total_usd"] = 2.58
        _write_json(shot_project / "artifacts" / "shot_contract.json", contract)
        board = load_shot_board(shot_project)
        assert board["spend"]["total_usd"] == 2.58
        assert board["spend"]["source"] == "artifacts/shot_contract.json"
        assert [a["job"] for a in board["spend"]["unattributed"]] == ["ref_room"]
        assert [t["cost_usd"] for t in board["shots"][1]["takes"]] == [0.62, 0.62]

    def test_a_shot_with_no_take_yet(self, shot_project):
        for name in ("shot_02.mp4", "shot_02.attempt1.mp4"):
            (shot_project / "assets" / "video" / name).unlink()
        (shot_project / "artifacts" / "adherence" / "shot_02.json").unlink()
        shot = load_shot_board(shot_project)["shots"][1]
        assert shot["takes"] == []
        assert shot["accepted_take"] is None
        assert shot["verdict"] is None

    def test_a_regenerate_verdict_accepts_nothing(self, shot_project):
        _write_json(shot_project / "artifacts" / "adherence" / "shot_01.json", {
            "shot_id": "shot_01", "clip": "assets/video/shot_01.mp4",
            "verdict": "regenerate", "change_next": "add the pillow",
        })
        shot = load_shot_board(shot_project)["shots"][0]
        assert shot["accepted_take"] is None
        assert shot["takes"][0]["accepted"] is False

    def test_malformed_files_degrade_rather_than_crash(self, shot_project):
        (shot_project / "artifacts" / "brief_checklist.json").write_text("{ broken", encoding="utf-8")
        (shot_project / "artifacts" / "spend_log.json").write_text("nonsense", encoding="utf-8")
        (shot_project / "artifacts" / "adherence" / "shot_01.json").write_text("[", encoding="utf-8")
        board = load_shot_board(shot_project)
        assert board["brief"]["checklist"] is None
        assert board["spend"]["total_usd"] is None
        assert board["shots"][0]["verdict"] is None
        assert board["shots"][0]["takes"][0]["accepted"] is True  # the bare take, by convention

    def test_an_unreadable_contract_is_not_a_shot_project(self, shot_project):
        (shot_project / "artifacts" / "shot_contract.json").write_text("{{{", encoding="utf-8")
        assert load_shot_board(shot_project) is None


# ---------------------------------------------------------------------------
# board state + server
# ---------------------------------------------------------------------------

class TestBoardState:
    def test_state_carries_the_shot_board(self, shot_project):
        state = load_board_state(shot_project)
        assert state["shots"] is not None
        assert [s["label"] for s in state["shots"]["shots"]] == ["S1", "S2"]
        assert state["storyboard"] is None          # no scene_plan: the shot board replaces it
        assert state["cost"]["total_spent_usd"] == 1.65
        assert state["cost"]["budget_remaining_usd"] == 4.35

    def test_a_new_take_moves_the_project_back_into_the_live_window(self, shot_project):
        """A take is neither a checkpoint nor an artifact JSON — fold it in."""
        old = 1_600_000_000
        for path in shot_project.rglob("*"):
            if path.is_file():
                os.utime(path, (old, old))
        assert load_board_state(shot_project)["live"] is False
        _write_media(shot_project / "assets" / "video" / "shot_02.attempt2.mp4", b"newer")
        state = load_board_state(shot_project)
        assert state["live"] is True

    def test_a_standard_pipeline_project_is_unaffected(self, projects_root):
        project = projects_root / "film"
        _write_json(project / "artifacts" / "scene_plan.json", {
            "version": "1.0",
            "scenes": [{"id": "sc1", "type": "generated", "description": "open",
                        "start_seconds": 0, "end_seconds": 4}],
        })
        state = load_board_state(project)
        assert state["shots"] is None
        assert state["storyboard"] is not None
        assert summarize_project(project)["shot_count"] == 0

    def test_summary_counts_shots(self, shot_project):
        assert summarize_project(shot_project)["shot_count"] == 2


class TestServed:
    @pytest.fixture
    def client(self, tmp_path, shot_project, monkeypatch):
        root = shot_project.parent
        monkeypatch.setattr(state_mod, "PROJECTS_DIR", root)
        monkeypatch.setattr(server_mod, "PROJECTS_DIR", root)
        monkeypatch.setattr(server_mod, "_summary_cache", {})
        monkeypatch.setattr(server_mod, "_PROJECTS_ROOT_STR",
                            os.path.normcase(str(root.resolve())))
        monkeypatch.setattr(server_mod, "THUMB_CACHE_DIR", tmp_path / "thumbs")

        async def no_watch():
            return None

        monkeypatch.setattr(server_mod, "_watch_projects", no_watch)
        with TestClient(server_mod.create_app()) as c:
            yield c

    def test_state_endpoint_serves_the_shot_board(self, client):
        payload = client.get("/api/project/night-run/state").json()
        shots = payload["shots"]
        assert [s["label"] for s in shots["shots"]] == ["S1", "S2"]
        assert shots["cut"]["label"] == "CUT"
        assert [r["label"] for r in shots["references"]["items"]] == ["R1", "R2"]
        assert shots["spend"]["total_usd"] == 1.65

    def test_every_take_path_is_servable(self, client):
        shots = client.get("/api/project/night-run/state").json()["shots"]
        paths = (
            [item["path"] for item in shots["references"]["items"]]
            + [take["path"] for shot in shots["shots"] for take in shot["takes"]]
            + [shots["cut"]["path"]]
        )
        for path in paths:
            assert client.get(f"/media/night-run/{path}").status_code == 200, path

    def test_the_library_card_counts_shots(self, client):
        card = next(p for p in client.get("/api/projects").json() if p["project_id"] == "night-run")
        assert card["shot_count"] == 2

    def test_the_ui_module_is_served(self, client):
        response = client.get("/ui/shots.js")
        assert response.status_code == 200
        assert "renderShotBoard" in response.text
