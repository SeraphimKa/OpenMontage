"""media_license_report: per-asset licence listing and delivery blockers.

Uses a copy of projects/rain-city-montage/artifacts as a realistic
fixture (never the project itself) plus synthetic manifests covering
every licence class. No network.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from schemas.artifacts import ARTIFACT_NAMES, validate_artifact
from tools.analysis import media_license_report as mlr
from tools.analysis.media_license_report import MediaLicenseReport
from tools.tool_registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[2]
RAIN_CITY = REPO_ROOT / "projects" / "rain-city-montage" / "artifacts"
FIXED_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)


def _tool() -> MediaLicenseReport:
    return MediaLicenseReport(now=lambda: FIXED_NOW)


def _write_project(tmp_path: Path, manifest: dict, edit_decisions: dict | None = None) -> Path:
    project = tmp_path / "synthetic-project"
    artifacts = project / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "asset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if edit_decisions is not None:
        (artifacts / "edit_decisions.json").write_text(json.dumps(edit_decisions), encoding="utf-8")
    return project


def _asset(asset_id: str, **fields) -> dict:
    return {
        "id": asset_id,
        "type": "video",
        "path": f"projects/synthetic-project/assets/video/{asset_id}.mp4",
        "source_tool": "direct_clip_search",
        "scene_id": "s01",
        **fields,
    }


def _by_id(report: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in report["assets"]}


def _load_written(result) -> dict:
    return json.loads(Path(result.data["report_path"]).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# Realistic fixture: rain-city-montage
# ---------------------------------------------------------------------


@pytest.fixture
def rain_city(tmp_path) -> Path:
    if not RAIN_CITY.is_dir():
        pytest.skip("projects/rain-city-montage fixture not present")
    project = tmp_path / "rain-city-montage"
    (project / "artifacts").mkdir(parents=True)
    for src in RAIN_CITY.glob("*.json"):
        shutil.copy2(src, project / "artifacts" / src.name)
    return project


def test_rain_city_report_blocks_share_alike_and_lists_music(rain_city):
    result = _tool().execute({"project_dir": str(rain_city)})

    assert result.success, result.error
    report = _load_written(result)
    validate_artifact("media_licenses", report)

    assert report["project_id"] == "rain-city-montage"
    assert report["generated_at"] == FIXED_NOW.isoformat()
    assert report["cleared_for_delivery"] is False

    blocker_ids = {b["id"] for b in report["blockers"]}
    assert "clip_s01" in blocker_ids
    assert blocker_ids == {"clip_s01", "clip_s03", "clip_s05", "clip_s06", "clip_s07", "clip_s08", "clip_s09"}
    assert report["summary"]["blockers_count"] == 7

    assets = _by_id(report)
    assert assets["clip_s01"]["license"] == "CC BY-SA 4.0"
    assert assets["clip_s01"]["license_class"] == "not_cleared"
    assert assets["clip_s01"]["creator"] == "Secretlondon"
    assert assets["music_bed"]["type"] == "music"
    assert assets["music_bed"]["used"] is True
    assert assets["music_bed"]["license_class"] == "cleared"
    assert assets["music_bed"]["creator"] == "PaulYudin"
    assert assets["clip_s04"]["creator"] == "slavikfi (Vjatseslav Robtsenkov)"
    assert report["summary"]["used_count"] == 11

    assert report["attribution_credits"] == [
        '"Large Crowd Marches Through Downtown Austin" by Sports 24/7 via wikimedia '
        "(CC BY 3.0) https://commons.wikimedia.org/?curid=53041973"
    ]


def test_rain_city_markdown_has_credits_block_and_blockers(rain_city):
    result = _tool().execute({"project_dir": str(rain_city)})
    md_path = Path(result.data["markdown_path"])

    assert md_path == rain_city / "artifacts" / "media_licenses.md"
    md = md_path.read_text(encoding="utf-8")
    assert md.startswith("# Media licence report — rain-city-montage")
    assert "## Credits to include\n\n```text\n\"Large Crowd Marches Through Downtown Austin\"" in md
    assert "## Blocked for commercial delivery" in md
    assert "- `clip_s01` (video, scene s01)" in md
    assert "| music_bed | music | global | yes | cleared |" in md


def test_rain_city_fail_on_blockers(rain_city):
    result = _tool().execute({"project_dir": str(rain_city), "fail_on_blockers": True})

    assert result.success is False
    assert "7 used asset(s)" in result.error
    assert result.data["cleared_for_delivery"] is False
    assert Path(result.data["report_path"]).is_file()
    assert Path(result.data["markdown_path"]).is_file()


# ---------------------------------------------------------------------
# Synthetic manifests
# ---------------------------------------------------------------------


_EVERY_CLASS = {
    "version": "1.0",
    "assets": [
        _asset("pexels", provider="pexels", license="Pexels License (free, no attribution required)"),
        _asset(
            "ccby",
            provider="wikimedia",
            license="CC BY 4.0",
            creator="Ada Lovelace",
            original_url="https://example.test/ccby",
            generation_summary='Wikimedia Commons: "Harbor at dusk" by Someone Else. License: CC BY 4.0.',
        ),
        _asset("ccbync", provider="flickr", license="CC BY-NC 2.0"),
        _asset("client", license="client-provided"),
        _asset("library_track", type="music", subtype="library", source_tool="music_library"),
        _asset("gen_prompt", type="image", source_tool="flux_image", prompt="a harbor at dusk"),
        _asset("gen_subtype", subtype="generated"),
        _asset("gen_but_nc", model="some-model", license="CC BY-NC 4.0"),
        _asset("nolicence"),
        _asset("gibberish", license="Some Bespoke Licence v2"),
    ],
}


def test_every_licence_class_is_classified(tmp_path):
    project = _write_project(tmp_path, _EVERY_CLASS)
    result = _tool().execute({"project_dir": str(project)})

    assert result.success, result.error
    report = _load_written(result)
    validate_artifact("media_licenses", report)
    classes = {entry_id: e["license_class"] for entry_id, e in _by_id(report).items()}
    assert classes == {
        "pexels": "cleared",
        "ccby": "attribution",
        "ccbync": "not_cleared",
        "client": "user_supplied",
        "library_track": "user_supplied",
        "gen_prompt": "generated",
        "gen_subtype": "generated",
        "gen_but_nc": "not_cleared",
        "nolicence": "unknown",
        "gibberish": "not_cleared",
    }
    assert report["summary"]["per_class"] == {
        "cleared": 1,
        "attribution": 1,
        "not_cleared": 3,
        "user_supplied": 2,
        "generated": 2,
        "unknown": 1,
    }


def test_without_edit_decisions_everything_is_used(tmp_path):
    project = _write_project(tmp_path, _EVERY_CLASS)
    report = _tool().execute({"project_dir": str(project)}).data

    assert all(e["used"] for e in report["assets"])
    assert {b["id"] for b in report["blockers"]} == {"ccbync", "gen_but_nc", "nolicence", "gibberish"}
    assert {b["id"]: b["license_class"] for b in report["blockers"]}["nolicence"] == "unknown"
    assert any("edit_decisions not found" in note for note in report["notes"])
    assert report["cleared_for_delivery"] is False


def test_edit_decisions_decide_usage(tmp_path):
    manifest = {
        "version": "1.0",
        "assets": [
            _asset("by_id", license="CC0"),
            _asset("by_path", license="CC BY 4.0", creator="Ada"),
            _asset("unused_nc", license="CC BY-NC 4.0"),
            _asset("via_audio_ref", license="CC BY-SA 4.0"),
            _asset("music_unreferenced", type="music", license="Pixabay Content License"),
            _asset("narration_unlicensed", type="narration"),
        ],
    }
    edit_decisions = {
        "version": "1.0",
        "cuts": [
            {"id": "c1", "source": "by_id", "in_seconds": 0, "out_seconds": 2},
            {"id": "c2", "source": "./projects/synthetic-project/assets/video/by_path.mp4",
             "in_seconds": 0, "out_seconds": 2},
        ],
        "audio": {"music": {"asset_id": "via_audio_ref", "volume": 0.3}},
    }
    project = _write_project(tmp_path, manifest, edit_decisions)
    report = _tool().execute({"project_dir": str(project)}).data
    used = {entry_id: e["used"] for entry_id, e in _by_id(report).items()}

    assert used == {
        "by_id": True,
        "by_path": True,
        "unused_nc": False,
        "via_audio_ref": True,
        "music_unreferenced": True,
        "narration_unlicensed": True,
    }
    assert {b["id"] for b in report["blockers"]} == {"via_audio_ref", "narration_unlicensed"}
    assert report["summary"]["used_count"] == 5
    assert any("not referenced by edit_decisions" in note for note in report["notes"])


def test_unused_blocker_does_not_block_delivery(tmp_path):
    manifest = {"version": "1.0", "assets": [_asset("used", license="CC0"), _asset("spare", license="CC BY-NC 4.0")]}
    edit_decisions = {"version": "1.0", "cuts": [{"id": "c1", "source": "used", "in_seconds": 0, "out_seconds": 1}]}
    project = _write_project(tmp_path, manifest, edit_decisions)

    result = _tool().execute({"project_dir": str(project), "fail_on_blockers": True})
    assert result.success, result.error
    assert result.data["cleared_for_delivery"] is True
    assert result.data["blockers"] == []
    md = Path(result.data["markdown_path"]).read_text(encoding="utf-8")
    assert "No attribution credits required." in md


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {"license": "CC BY 4.0", "creator": "Ada", "provider": "wikimedia",
             "original_url": "https://example.test/a",
             "generation_summary": '"Harbor at dusk" by Somebody. License: CC BY 4.0.'},
            '"Harbor at dusk" by Ada via wikimedia (CC BY 4.0) https://example.test/a',
        ),
        (
            {"license": "CC BY 4.0",
             "generation_summary": 'Commons: "Harbor at dusk" by Grace Hopper. License: CC BY 4.0.'},
            '"Harbor at dusk" by Grace Hopper (CC BY 4.0)',
        ),
        ({"license": "CC BY 3.0"}, '"asset_x" (CC BY 3.0)'),
        (
            {"license": "Videvo Attribution License (free, attribution required)", "provider": "videvo"},
            '"asset_x" via videvo (Videvo Attribution License (free, attribution required))',
        ),
    ],
)
def test_credit_line_format(tmp_path, fields, expected):
    project = _write_project(tmp_path, {"version": "1.0", "assets": [_asset("asset_x", **fields)]})
    report = _tool().execute({"project_dir": str(project)}).data
    entry = report["assets"][0]

    assert entry["license_class"] == "attribution"
    assert entry["attribution_required"] is True
    assert entry["credit_line"] == expected
    assert report["attribution_credits"] == [expected]


def test_non_attribution_assets_have_no_credit_line(tmp_path):
    project = _write_project(tmp_path, _EVERY_CLASS)
    report = _tool().execute({"project_dir": str(project)}).data
    for entry in report["assets"]:
        if entry["license_class"] != "attribution":
            assert entry["credit_line"] is None
            assert entry["attribution_required"] is False


def test_creator_parsing():
    assert mlr.parse_creator({"creator": "  Explicit  "}) == "Explicit"
    assert mlr.parse_creator(
        {"generation_summary": 'Wikimedia Commons: "Rain in Kyiv" by Ата. License: CC BY-SA 3.0.'}
    ) == "Ата"
    assert mlr.parse_creator({"generation_summary": "Shot by Jane Roe"}) == "Jane Roe"
    assert mlr.parse_creator({"generation_summary": "Retrieved via CLIP rank. Score 0.38."}) == ""
    assert mlr.parse_creator({}) == ""


def test_output_path_override_writes_json_and_sibling_markdown(tmp_path):
    project = _write_project(tmp_path, {"version": "1.0", "assets": [_asset("a", license="CC0")]})
    out = tmp_path / "reports" / "licences.json"

    result = _tool().execute({"project_dir": str(project), "output_path": str(out)})

    assert result.success, result.error
    assert out.is_file()
    assert (tmp_path / "reports" / "licences.md").is_file()
    assert not (project / "artifacts" / "media_licenses.json").exists()
    validate_artifact("media_licenses", json.loads(out.read_text(encoding="utf-8")))


def test_explicit_manifest_and_edit_decisions_paths(tmp_path):
    manifest_path = tmp_path / "m.json"
    edit_path = tmp_path / "e.json"
    manifest_path.write_text(json.dumps({"version": "1.0", "assets": [_asset("a", license="CC0"), _asset("b", license="CC0")]}), encoding="utf-8")
    edit_path.write_text(json.dumps({"cuts": [{"source": "a"}]}), encoding="utf-8")

    result = _tool().execute({
        "asset_manifest_path": str(manifest_path),
        "edit_decisions_path": str(edit_path),
        "output_path": str(tmp_path / "out.json"),
    })

    assert result.success, result.error
    assert {e["id"]: e["used"] for e in result.data["assets"]} == {"a": True, "b": False}


def test_missing_inputs_fail_cleanly(tmp_path):
    assert _tool().execute({}).success is False
    missing = _tool().execute({"project_dir": str(tmp_path / "nope")})
    assert missing.success is False
    assert "asset_manifest not found" in missing.error

    project = _write_project(tmp_path, {"version": "1.0", "assets": []})
    bad_edit = _tool().execute({
        "project_dir": str(project), "edit_decisions_path": str(tmp_path / "absent.json")
    })
    assert bad_edit.success is False


# ---------------------------------------------------------------------
# Contracts: schema registration, manifest fields, discovery
# ---------------------------------------------------------------------


def test_media_licenses_is_a_registered_artifact():
    assert "media_licenses" in ARTIFACT_NAMES


def test_asset_manifest_schema_accepts_creator_and_license_class():
    validate_artifact("asset_manifest", {
        "version": "1.0",
        "assets": [_asset("a", license="CC BY 4.0", creator="Ada", license_class="attribution")],
    })


def test_tool_registers_from_module():
    registry = ToolRegistry()
    assert registry.register_module(mlr) == ["media_license_report"]
    info = registry.get("media_license_report").get_info()
    assert info["tier"] == "analyze"
    assert info["agent_skills"] == []
    assert MediaLicenseReport().estimate_cost({}) == 0.0


# ---------------------------------------------------------------------
# Review 03 regressions (all fail-open before the fix)
# ---------------------------------------------------------------------


def _manifest(*assets) -> dict:
    return {"version": "1.0", "assets": list(assets)}


def _cuts(*sources) -> list[dict]:
    return [
        {"id": f"c{i}", "source": source, "in_seconds": 0, "out_seconds": 1}
        for i, source in enumerate(sources)
    ]


def _run(tmp_path, manifest, edit_decisions=None, **inputs):
    project = _write_project(tmp_path, manifest, edit_decisions)
    return _tool().execute({"project_dir": str(project), **inputs})


# B1 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("license_text", "expected"),
    [
        ("CC BY-NC 4.0, supplied by uploader", "not_cleared"),
        ("client-provided; CC BY-NC-ND", "not_cleared"),
        ("Licence supplied by Wikimedia: CC BY-SA 4.0", "not_cleared"),
        ("Licence supplied by Wikimedia", "not_cleared"),
        ("Supplied by Pexels", "not_cleared"),
        ("client-provided", "user_supplied"),
        ("supplied by the client", "user_supplied"),
        ("Customer owned footage", "user_supplied"),
    ],
)
def test_b1_restrictions_beat_user_supplied_wording(license_text, expected):
    assert mlr.classify_asset(_asset("a", license=license_text)) == expected


def test_b1_used_nc_clip_with_supplied_wording_blocks_delivery(tmp_path):
    result = _run(tmp_path, _manifest(_asset("a", license="CC BY-NC 4.0, supplied by uploader")))

    assert result.data["cleared_for_delivery"] is False
    assert [b["id"] for b in result.data["blockers"]] == ["a"]


# B2 ------------------------------------------------------------------


def test_b2_overlay_asset_is_used(tmp_path):
    edit_decisions = {
        "version": "1.0",
        "cuts": _cuts("main"),
        "overlays": [{
            "asset_id": "ov", "start_seconds": 0, "end_seconds": 1, "position": {"x": 0, "y": 0},
        }],
    }
    result = _run(
        tmp_path,
        _manifest(_asset("main", license="CC0"), _asset("ov", type="image", license="CC BY-NC 4.0")),
        edit_decisions,
    )

    assert _by_id(result.data)["ov"]["used"] is True
    assert [b["id"] for b in result.data["blockers"]] == ["ov"]
    assert result.data["cleared_for_delivery"] is False


@pytest.mark.parametrize(
    "asset_type",
    ["font", "lut", "animation", "diagram", "subtitle", "3d_asset", "3d_world",
     "code_snippet", "music", "narration", "sfx", "audio"],
)
def test_b2_types_cuts_cannot_reference_are_always_used(tmp_path, asset_type):
    result = _run(
        tmp_path,
        _manifest(_asset("main", license="CC0"), _asset("x", type=asset_type, license="Free for personal use")),
        {"version": "1.0", "cuts": _cuts("main")},
    )

    assert _by_id(result.data)["x"]["used"] is True
    assert result.data["cleared_for_delivery"] is False


@pytest.mark.parametrize(
    ("edit_decisions", "why"),
    [
        ({"version": "1.0", "cuts": []}, "no cuts"),
        ({"version": "1.0"}, "no cuts"),
        ({"version": "1.0", "composition_mode": "atelier", "cuts": _cuts("main")}, "atelier"),
    ],
)
def test_b2_no_cuts_or_atelier_treats_every_asset_as_used(tmp_path, edit_decisions, why):
    result = _run(
        tmp_path,
        _manifest(_asset("main", license="CC0"), _asset("spare", license="CC BY-NC 4.0")),
        edit_decisions,
    )

    assert all(e["used"] for e in result.data["assets"])
    assert result.data["cleared_for_delivery"] is False
    assert any(why in note for note in result.data["notes"])


# S1 ------------------------------------------------------------------


def test_s1_orphan_references_become_unknown_blockers(tmp_path):
    edit_decisions = {
        "version": "1.0",
        "cuts": _cuts("main", "projects/p/assets/video/orphan_nc.webm"),
        "overlays": [{
            "asset_id": "ghost_overlay", "start_seconds": 0, "end_seconds": 1, "position": {"x": 0, "y": 0},
        }],
        "audio": {"music": {"asset_id": "music_missing", "volume": 0.3}},
        "subtitles": {"enabled": True, "source": "subs/missing.srt", "font": "fonts/Missing.ttf"},
    }
    result = _run(tmp_path, _manifest(_asset("main", license="CC0")), edit_decisions)

    report = _load_written(result)
    validate_artifact("media_licenses", report)
    orphans = {b["id"]: b for b in report["blockers"] if b.get("orphan_reference")}
    assert set(orphans) == {
        "projects/p/assets/video/orphan_nc.webm",
        "ghost_overlay",
        "music_missing",
        "subs/missing.srt",
        "fonts/Missing.ttf",
    }
    for blocker in orphans.values():
        assert blocker["license_class"] == "unknown"
        assert blocker["reason"] == "referenced by edit_decisions but absent from asset_manifest"
    assert report["cleared_for_delivery"] is False
    assert any("match no asset_manifest entry" in note for note in report["notes"])
    md = Path(result.data["markdown_path"]).read_text(encoding="utf-8")
    assert "- `ghost_overlay` (not in asset_manifest)" in md


def test_s1_non_reference_audio_strings_are_not_orphans(tmp_path):
    edit_decisions = {
        "version": "1.0",
        "cuts": _cuts("main"),
        "audio": {"music": {"asset_id": "main", "style": "ducked"}},
        "subtitles": {"enabled": False, "source": "subs/unused.srt"},
    }
    result = _run(tmp_path, _manifest(_asset("main", license="CC0")), edit_decisions)

    assert result.data["blockers"] == []
    assert result.data["cleared_for_delivery"] is True


# S2 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "manifest",
    [
        {"version": "1.0"},
        {"version": "1.0", "assets": {"a": {"id": "a"}}},
        {"version": "1.0", "clips": [{"id": "a"}]},
    ],
)
def test_s2_manifest_without_assets_list_fails(tmp_path, manifest):
    result = _run(tmp_path, manifest)

    assert result.success is False
    assert "'assets'" in result.error


def test_s2_empty_manifest_with_referenced_media_is_not_cleared(tmp_path):
    result = _run(tmp_path, _manifest(), {"version": "1.0", "cuts": _cuts("clip_a")})

    assert result.data["cleared_for_delivery"] is False
    assert any("lists no assets" in note for note in result.data["notes"])


# S3 ------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "../assets/video/b.webm",
        "./projects/p/assets/./video/b.webm",
        "projects/p/assets/video/sub/../b.webm",
        "http://localhost:3000/assets/video/b.webm",
        "https://cdn.example.test:8443/projects/p/assets/video/b.webm",
        "PROJECTS/P/ASSETS/VIDEO/B.WEBM",
    ],
)
def test_s3_path_forms_resolve_to_the_asset(tmp_path, ref):
    target = _asset("b", path="projects/p/assets/video/b.webm", license="CC BY-NC 4.0")
    result = _run(
        tmp_path,
        _manifest(_asset("main", license="CC0"), target),
        {"version": "1.0", "cuts": _cuts("main", ref)},
    )

    assert _by_id(result.data)["b"]["used"] is True
    assert [b["id"] for b in result.data["blockers"]] == ["b"]


def test_s3_basename_fallback_matches_and_notes(tmp_path):
    target = _asset("c", path="projects/p/assets/video/raw/clips/c.webm", license="CC BY-SA 4.0")
    result = _run(
        tmp_path,
        _manifest(_asset("main", license="CC0"), target),
        {"version": "1.0", "cuts": _cuts("main", "public/clips/C.webm")},
    )

    assert _by_id(result.data)["c"]["used"] is True
    assert [b["id"] for b in result.data["blockers"]] == ["c"]
    assert any("only by file name" in note for note in result.data["notes"])


# O1 ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"model": "flux", "license": "Personal use only"}, "not_cleared"),
        ({"prompt": "harbor at dusk", "license": "Some Bespoke Licence"}, "not_cleared"),
        ({"subtype": "generated", "license": "Editorial use only"}, "not_cleared"),
        ({"subtype": "library", "license": "Getty Images Rights-Managed"}, "not_cleared"),
        ({"subtype": "library", "license": "Artlist subscription"}, "not_cleared"),
        ({"subtype": "library", "license": "user-provided"}, "user_supplied"),
        ({"model": "flux"}, "generated"),
        ({"subtype": "library"}, "user_supplied"),
    ],
)
def test_o1_unrecognised_licence_text_blocks_generated_and_library(fields, expected):
    assert mlr.classify_asset(_asset("a", **fields)) == expected


# O2 ------------------------------------------------------------------


def test_o2_markdown_output_path_is_rejected(tmp_path):
    out = tmp_path / "out.md"
    result = _run(tmp_path, _manifest(_asset("a", license="CC0")), output_path=str(out))

    assert result.success is False
    assert ".json" in result.error
    assert not out.exists()


def test_o2_markdown_path_is_stem_dot_md(tmp_path):
    out = tmp_path / "reports" / "licences.v2.json"
    result = _run(tmp_path, _manifest(_asset("a", license="CC0")), output_path=str(out))

    assert result.success, result.error
    assert Path(result.data["markdown_path"]) == tmp_path / "reports" / "licences.v2.md"
    assert json.loads(out.read_text(encoding="utf-8"))["version"] == "1.0"


# O3 ------------------------------------------------------------------


def test_o3_markdown_normalises_newlines_and_escapes_fences(tmp_path):
    attributed = _asset("a\rb", license="CC BY 4.0", creator="Ada\r\nLovelace\n```evil")
    blocked = _asset("n\nc", license="CC BY-NC 4.0")
    result = _run(tmp_path, _manifest(attributed, blocked))

    assert result.data["attribution_credits"] == ['"a b" by Ada Lovelace ```evil (CC BY 4.0)']
    md = Path(result.data["markdown_path"]).read_bytes().decode("utf-8")
    assert "\r" not in md
    credits = md.split("## Credits to include\n\n", 1)[1].split("\n## ", 1)[0]
    assert credits.count("```") == 2
    assert "` ` `evil" in credits
    rows = [line for line in md.split("\n") if line.startswith("| a b |")]
    assert len(rows) == 1 and rows[0].endswith(" |") and "Ada Lovelace" in rows[0]
    assert "- `n c` (video, scene s01)" in md


# O4 ------------------------------------------------------------------


def test_s4_report_migrates_legacy_previous_license_string(tmp_path):
    legacy = {
        "url": "https://commons.wikimedia.org/w/api.php?pageids=1",
        "method": "api",
        "matched_text": "LicenseShortName: CC BY 4.0",
        "fetched_at": "2026-09-14T12:00:00+00:00",
        "resolver_version": "0.1.0",
        "previous_license": "CC0",
    }
    history = dict(legacy, previous_licenses=["CC BY-SA 4.0", 7], previous_license="CC0")
    result = _run(tmp_path, _manifest(
        _asset("legacy", license="CC BY 4.0", license_evidence=legacy),
        _asset("mixed", license="CC BY 4.0", license_evidence=history),
    ))

    report = _load_written(result)
    validate_artifact("media_licenses", report)
    entries = _by_id(report)
    assert entries["legacy"]["license_evidence"]["previous_licenses"] == ["CC0"]
    assert "previous_license" not in entries["legacy"]["license_evidence"]
    assert entries["mixed"]["license_evidence"]["previous_licenses"] == ["CC0", "CC BY-SA 4.0"]


def test_license_evidence_is_carried_into_json_and_markdown(tmp_path):
    evidence = {
        "url": "https://commons.wikimedia.org/w/api.php?pageids=1",
        "method": "api",
        "matched_text": "LicenseShortName: CC BY 4.0",
        "fetched_at": "2026-09-14T12:00:00+00:00",
        "resolver_version": "0.1.0",
        "previous_licenses": ["CC0"],
    }
    with_evidence = _asset("with_ev", license="CC BY 4.0", license_evidence=evidence)
    without = _asset("no_ev", license="CC0")
    malformed = _asset("bad_ev", license="CC0", license_evidence={"url": "https://x", "method": "api"})
    result = _run(tmp_path, _manifest(with_evidence, without, malformed))

    report = _load_written(result)
    validate_artifact("media_licenses", report)
    entries = _by_id(report)
    assert entries["with_ev"]["license_evidence"] == evidence
    assert "license_evidence" not in entries["no_ev"]
    assert "license_evidence" not in entries["bad_ev"]

    md = Path(result.data["markdown_path"]).read_text(encoding="utf-8")
    assert "| Source URL | Evidence |" in md
    row = next(line for line in md.split("\n") if line.startswith("| with_ev |"))
    assert row.endswith(f"| {evidence['url']} |")
    for asset_id in ("no_ev", "bad_ev"):
        row = next(line for line in md.split("\n") if line.startswith(f"| {asset_id} |"))
        assert row.endswith("| — |")


@pytest.mark.parametrize("flag", ["false", "true", 1, None])
def test_o4_fail_on_blockers_only_for_real_true(tmp_path, flag):
    result = _run(tmp_path, _manifest(_asset("a", license="CC BY-NC 4.0")), fail_on_blockers=flag)

    assert result.success is True
    assert result.data["cleared_for_delivery"] is False
