"""Tests for the open-montage stop writer (scripts/om_stop.py).

Every fixture is built in tmp_path. The script must never hand-roll checkpoint
JSON: it derives each stage's canonical artifact and lets lib/checkpoint.py
enforce the gate, the prerequisites, the schema and the history.

The `prompt-faithful` pipeline is the one the skill runs under. `cinematic` is
kept here as the negative case: its `research` and `proposal` artifacts cannot
be derived from a shot-list brief, and the script must refuse rather than
invent them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import om_stop  # noqa: E402
from backlot.state import load_board_state  # noqa: E402
from lib.checkpoint import init_project  # noqa: E402


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _write_media(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake")


CONTRACT = {
    "project": "night-run",
    "route": {"tool": "seedance_ark", "resolution": "480p"},
    "budget_usd": 6.0,
    "runtime_seconds": 9,
    "grade_preset": "none",
    "shots": [
        {"id": "shot_01", "title": "Wake", "brief_timecode": "0:00-0:03",
         "seconds_generated": 4, "seconds_delivered": 3,
         "lens": "47 degrees", "camera": "static locked-off",
         "action": "he opens his eyes", "frame_contains": ["his face", "the pillow"],
         "trim": {"keep_seconds": "0.0-3.0", "why": "the turn ends at 3.0 s"},
         "reference_ids": ["assets/reference/room.png"]},
        {"id": "shot_02", "title": "Stand", "brief_timecode": "0:03-0:09",
         "seconds_generated": 6, "seconds_delivered": 6,
         "lens": "47 degrees", "action": "he stands up",
         "reference_ids": ["assets/reference/room.png"]},
    ],
}

CHECKLIST = {
    "deliverable": {"seconds": 9, "shots": 2},
    "entries": [
        {"id": "c01", "tag": "character", "priority": "must", "literal": "a man"},
        {"id": "l01", "tag": "look", "priority": "must", "literal": "fine film grain"},
        {"id": "l02", "tag": "look", "priority": "must", "literal": "photoreal skin texture"},
    ],
    "conflicts": [],
    "decisions": [{
        "subject": "Window position", "chosen": "foot wall",
        "options_considered": ["foot wall", "side wall", "two windows"],
        "rejected_because": {"side wall": "no cool side on the face",
                             "two windows": "a second blue source competes"},
    }],
}


def _make_project(root: Path, pipeline_type: str) -> Path:
    init_project("night-run", title="Night Run", pipeline_type=pipeline_type,
                 pipeline_dir=root)
    p = root / "night-run"
    _write_json(p / "artifacts" / "shot_contract.json", CONTRACT)
    _write_json(p / "artifacts" / "brief_checklist.json", CHECKLIST)
    (p / "artifacts" / "brief_source.md").write_text(
        "# Brief\n\nUltra-realistic sequence, nine seconds, two hard-cut shots. "
        "A man wakes.\n", encoding="utf-8")
    _write_media(p / "assets" / "reference" / "room.png")
    for shot in ("shot_01", "shot_02"):
        _write_media(p / "assets" / "video" / f"{shot}.mp4")
        (p / "artifacts" / "prompts").mkdir(parents=True, exist_ok=True)
        (p / "artifacts" / "prompts" / f"{shot}.txt").write_text("SHOT: …", encoding="utf-8")
        _write_json(p / "artifacts" / "adherence" / f"{shot}.json",
                    {"shot_id": shot, "clip": f"assets/video/{shot}.mp4", "verdict": "accept",
                     "actual": {"width": 854, "height": 480, "cost_usd": 0.41}})
    _write_json(p / "artifacts" / "spend_log.json",
                {"attempts": [{"shot": "shot_01", "usd": 0.41}, {"shot": "shot_02", "usd": 0.62}],
                 "total_usd": 1.03})
    _write_json(p / "artifacts" / "final" / "cut_report.json",
                {"shots": [{"id": "shot_01", "starts_at": 0.0, "ends_at": 3.0, "entered_by": "cut",
                            "transition_seconds": 0.0},
                           {"id": "shot_02", "starts_at": 3.0, "ends_at": 9.0, "entered_by": "cut",
                            "transition_seconds": 0.0}],
                 "cuts_at": [3.0], "runtime_seconds": 9.0, "runtime_expected": 9,
                 "runtime_ok": True, "grade": "none", "edited_from": "the shot contract"})
    _write_media(p / "renders" / "night-run.mp4")
    return p


@pytest.fixture
def projects_root(tmp_path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture
def project(projects_root) -> Path:
    """A finished open-montage run on the pipeline the skill uses."""
    return _make_project(projects_root, "prompt-faithful")


def _checkpoint(project: Path, stage: str) -> dict:
    return json.loads((project / f"checkpoint_{stage}.json").read_text(encoding="utf-8"))


def _artifact(project: Path, stage: str, name: str) -> dict:
    return _checkpoint(project, stage)["artifacts"][name]


def _run(project: Path, stop: str, approve: bool = False, note: str | None = None) -> int:
    return om_stop.write_stop(project, stop, approve, note=note)


def _walk_to(project: Path, last_stop: str) -> None:
    """Approve every stop up to and including `last_stop`."""
    for stop in om_stop.STOP_NAMES:
        assert _run(project, stop) == 0, stop
        assert _run(project, stop, approve=True) == 0, stop
        if stop == last_stop:
            return


# ---------------------------------------------------------------------------

class TestStopMapping:
    def test_the_brief_card_is_the_idea_stage_on_prompt_faithful(self):
        stages = ["idea", "script", "scene_plan", "assets", "edit", "compose", "publish"]
        assert om_stop.stop_target("brief", stages) == ("idea", "brief")
        assert om_stop.stop_target("references", stages) == ("assets", "asset_manifest")
        assert om_stop.stop_target("shots", stages) == ("assets", "asset_manifest")
        assert om_stop.stop_target("cut", stages) == ("publish", "publish_log")

    def test_a_pipeline_without_idea_falls_back_to_proposal(self):
        stages = ["research", "proposal", "script", "scene_plan", "assets", "edit",
                  "compose", "publish"]
        assert om_stop.stop_target("brief", stages) == ("proposal", "proposal_packet")

    def test_the_default_pipeline_is_the_one_the_skill_uses(self):
        assert om_stop.DEFAULT_PIPELINE == "prompt-faithful"

    def test_a_project_without_a_contract_is_refused(self, projects_root, capsys):
        (projects_root / "plain").mkdir()
        assert _run(projects_root / "plain", "brief") == om_stop.EXIT_BAD_INPUT
        assert "not an open-montage project" in capsys.readouterr().out


class TestTheWholeRun:
    """Four stops, no seeding, no hand-written artifact. This is the whole job."""

    def test_all_four_stops_from_a_clean_project(self, project):
        _walk_to(project, "cut")
        stages = {s["name"]: s for s in load_board_state(project)["stages"]}
        assert [(n, s["status"]) for n, s in stages.items()] == [
            ("idea", "completed"), ("script", "completed"), ("scene_plan", "completed"),
            ("assets", "completed"), ("edit", "completed"), ("compose", "completed"),
            ("publish", "completed"),
        ]
        for gated in ("idea", "assets", "publish"):
            assert stages[gated]["gated"] is True
            assert stages[gated]["human_approved"] is True
            assert stages[gated]["gate_skipped"] is False
        for mechanical in ("script", "scene_plan", "edit", "compose"):
            assert stages[mechanical]["gated"] is False

    def test_each_stop_is_awaiting_before_it_is_approved(self, project):
        assert _run(project, "brief") == 0
        assert _checkpoint(project, "idea")["status"] == "awaiting_human"
        assert _checkpoint(project, "idea")["human_approved"] is False
        assert _run(project, "brief", approve=True) == 0
        assert _checkpoint(project, "idea")["status"] == "completed"
        assert _checkpoint(project, "idea")["human_approved"] is True
        assert list((project / "history").glob("checkpoint_idea_*.json"))

    def test_the_labels_to_look_at_are_recorded(self, project):
        _run(project, "brief", approve=True)
        assert _checkpoint(project, "idea")["metadata"]["look_at"] == ["BRIEF"]
        _run(project, "references", approve=True)
        assert _checkpoint(project, "assets")["metadata"]["look_at"] == ["R1"]
        _run(project, "shots")
        assert _checkpoint(project, "assets")["metadata"]["look_at"] == ["S1-A", "S2-A"]
        _run(project, "shots", approve=True)
        _run(project, "cut")
        assert _checkpoint(project, "publish")["metadata"]["look_at"] == ["CUT"]

    def test_the_users_note_is_kept(self, project):
        _run(project, "brief", approve=True, note="yes, go")
        assert _checkpoint(project, "idea")["metadata"]["note"] == "yes, go"

    def test_the_cost_snapshot_feeds_the_board_meter(self, project):
        _run(project, "brief", approve=True)
        snapshot = _checkpoint(project, "idea")["cost_snapshot"]
        assert snapshot["total_spent_usd"] == 1.03
        assert snapshot["budget_remaining_usd"] == 4.97


class TestDerivedArtifacts:
    def test_the_brief_card_comes_off_the_brief_and_the_contract(self, project):
        _run(project, "brief")
        card = _artifact(project, "idea", "brief")
        assert card["title"] == "night-run"
        assert card["hook"].startswith("Ultra-realistic sequence")
        assert card["tone"] == "Ultra-realistic sequence"
        assert card["style"] == "fine film grain; photoreal skin texture"
        assert card["target_duration_seconds"] == 9
        assert card["key_points"] == ["S1 0:00-0:03 Wake", "S2 0:03-0:09 Stand"]
        assert card["metadata"]["budget_usd"] == 6.0
        assert "projected" in card["metadata"]["note"].lower()

    def test_the_shot_list_becomes_the_script_without_invented_narration(self, project):
        _walk_to(project, "references")
        script = _artifact(project, "script", "script")
        assert script["total_duration_seconds"] == 9.0
        assert [(s["id"], s["start_seconds"], s["end_seconds"], s["label"])
                for s in script["sections"]] == [
            ("shot_01", 0.0, 3.0, "Wake"), ("shot_02", 3.0, 9.0, "Stand")]
        assert script["sections"][0]["text"] == "he opens his eyes"
        assert "not narration" in script["metadata"]["note"]

    def test_the_contract_becomes_the_scene_plan(self, project):
        _walk_to(project, "references")
        plan = _artifact(project, "scene_plan", "scene_plan")
        assert [s["id"] for s in plan["scenes"]] == ["shot_01", "shot_02"]
        first = plan["scenes"][0]
        assert first["type"] == "generated"
        assert first["framing"] == "47 degrees"
        assert first["movement"] == "static locked-off"
        assert first["texture_keywords"] == ["his face", "the pillow"]

    def test_the_trims_become_the_edit_decisions(self, project):
        _walk_to(project, "cut")
        edit = _artifact(project, "edit", "edit_decisions")
        assert edit["render_runtime"] == "ffmpeg"
        assert [(c["id"], c["in_seconds"], c["out_seconds"]) for c in edit["cuts"]] == [
            ("shot_01", 0.0, 3.0), ("shot_02", 0.0, 6.0)]
        assert edit["cuts"][0]["source"] == "assets/video/shot_01.mp4"
        assert edit["cuts"][0]["reason"] == "the turn ends at 3.0 s"
        assert edit["cuts"][0]["transition_in"] == "cut"

    def test_the_render_report_records_what_was_produced(self, project):
        _walk_to(project, "cut")
        report = _artifact(project, "compose", "render_report")
        output = report["outputs"][0]
        assert output["path"] == "renders/night-run.mp4"
        assert output["format"] == "mp4"
        # the fixture mp4 is not real, so ffprobe fails and the adherence files answer
        assert output["resolution"] == "854x480"
        assert output["duration_seconds"] == 9.0
        assert report["metadata"]["probed"] is False
        assert any("ffprobe unavailable" in note for note in report["verification_notes"])

    def test_a_review_is_never_derived(self, project):
        _walk_to(project, "cut")
        compose = _checkpoint(project, "compose")
        assert "final_review" not in compose["artifacts"]
        assert "not derived here" in _artifact(project, "compose", "render_report")["metadata"]["note"]

    @pytest.mark.parametrize("name,stage", [
        ("brief", "idea"), ("script", "script"), ("scene_plan", "scene_plan"),
        ("edit_decisions", "edit"), ("render_report", "compose"),
    ])
    def test_an_artifact_the_agent_wrote_itself_wins(self, project, name, stage):
        mine = {
            "brief": {"version": "1.0", "title": "Mine", "hook": "h", "key_points": ["k"],
                      "tone": "t", "style": "s", "target_platform": "youtube",
                      "target_duration_seconds": 9},
            "script": {"version": "1.0", "title": "Mine", "total_duration_seconds": 9,
                       "sections": [{"id": "s1", "text": "spoken", "start_seconds": 0,
                                     "end_seconds": 9}]},
            "scene_plan": {"version": "1.0", "scenes": [
                {"id": "s1", "type": "broll", "description": "mine",
                 "start_seconds": 0, "end_seconds": 9}]},
            "edit_decisions": {"version": "1.0", "render_runtime": "remotion",
                               "cuts": [{"id": "s1", "source": "a.mp4", "in_seconds": 0,
                                         "out_seconds": 9}]},
            "render_report": {"version": "1.0", "outputs": [
                {"path": "renders/mine.mp4", "format": "mp4", "resolution": "1920x1080",
                 "duration_seconds": 9}]},
        }[name]
        _write_json(project / "artifacts" / f"{name}.json", mine)
        _walk_to(project, "cut")
        assert _artifact(project, stage, name) == mine


class TestAssetStops:
    def test_the_reference_stop_shows_only_the_reference_set(self, project):
        _run(project, "brief", approve=True)
        assert _run(project, "references") == 0
        manifest = _artifact(project, "assets", "asset_manifest")
        assert [a["id"] for a in manifest["assets"]] == ["R1"]
        assert manifest["assets"][0]["scene_id"] == "reference"

    def test_the_shots_stop_reopens_the_stage_with_every_take(self, project):
        _walk_to(project, "references")
        assert _checkpoint(project, "assets")["status"] == "completed"

        assert _run(project, "shots") == 0
        assert _checkpoint(project, "assets")["status"] == "awaiting_human"
        manifest = _artifact(project, "assets", "asset_manifest")
        assert [a["id"] for a in manifest["assets"]] == ["R1", "S1-A", "S2-A"]
        assert manifest["metadata"]["accepted_takes"] == {"S1": "S1-A", "S2": "S2-A"}
        assert manifest["total_cost_usd"] == 1.03
        take = next(a for a in manifest["assets"] if a["id"] == "S1-A")
        assert take["subtype"] == "accepted_take"
        assert take["cost_usd"] == 0.41
        assert "artifacts/prompts/shot_01.txt" in take["generation_summary"]

        assert _run(project, "shots", approve=True) == 0
        # every superseded version survives, in order: the reference stop's
        # awaiting_human and its approval, then the shots stop's awaiting_human.
        archived = sorted((project / "history").glob("checkpoint_assets_*.json"))
        statuses = [json.loads(p.read_text(encoding="utf-8"))["status"] for p in archived]
        assert statuses == ["awaiting_human", "completed", "awaiting_human"]

    def test_the_reference_stop_refuses_an_empty_folder(self, project, capsys):
        _run(project, "brief", approve=True)
        (project / "assets" / "reference" / "room.png").unlink()
        assert _run(project, "references") == om_stop.EXIT_BAD_INPUT
        assert "no reference file" in capsys.readouterr().out


class TestCutStop:
    def test_exported_only_once_the_user_approves(self, project):
        _walk_to(project, "shots")
        assert _run(project, "cut") == 0
        entry = _artifact(project, "publish", "publish_log")["entries"][0]
        assert entry["status"] == "pending_review"
        assert entry["export_path"] == "renders/night-run.mp4"

        assert _run(project, "cut", approve=True) == 0
        assert _artifact(project, "publish", "publish_log")["entries"][0]["status"] == "exported"

    def test_it_refuses_before_the_cut_is_assembled(self, project, capsys):
        _walk_to(project, "shots")
        (project / "renders" / "night-run.mp4").unlink()
        (project / "artifacts" / "final" / "cut_report.json").unlink()
        assert _run(project, "cut") == om_stop.EXIT_BAD_INPUT
        assert "no assembled cut yet" in capsys.readouterr().out


class TestWhatItRefusesToFill:
    def test_a_gated_stage_is_never_filled(self, project):
        """assets is gated; reaching the cut stop must not approve it silently."""
        _walk_to(project, "brief")
        assert _run(project, "cut") == om_stop.EXIT_REFUSED
        assert not (project / "checkpoint_publish.json").exists()
        assert not (project / "checkpoint_assets.json").exists()

    def test_cinematics_research_and_proposal_are_refused_not_invented(
            self, projects_root, capsys):
        """Their schemas want web sources and three concept directions."""
        project = _make_project(projects_root, "cinematic")
        assert _run(project, "references") == om_stop.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "PREREQUISITE VIOLATION" in out
        assert "research" in out and "proposal" in out
        assert not (project / "checkpoint_research.json").exists()
        assert not (project / "checkpoint_proposal.json").exists()

    def test_the_cinematic_brief_stop_still_builds_a_proposal_packet(
            self, projects_root, monkeypatch):
        """The old mapping keeps working for a project already on cinematic."""
        project = _make_project(projects_root, "cinematic")
        packet = om_stop.build_artifact(
            __import__("backlot.shots", fromlist=["x"]).load_shot_board(project),
            project, "brief", "cinematic", "proposal_packet", approved=False)
        titles = [c["title"] for c in packet["concept_options"]]
        assert titles[0] == "night-run"
        assert titles[1:] == ["Rejected: side wall", "Rejected: two windows"]

    def test_it_refuses_to_invent_concepts(self, projects_root, capsys):
        project = _make_project(projects_root, "cinematic")
        _write_json(project / "artifacts" / "brief_checklist.json",
                    {**CHECKLIST, "decisions": []})
        assert _run(project, "brief") == om_stop.EXIT_BAD_INPUT
        assert "nothing here will invent the others" in capsys.readouterr().out
