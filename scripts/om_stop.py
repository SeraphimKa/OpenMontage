"""Put an open-montage stop on the Backlot stage rail, and approve it.

The skill spends the user's attention at four stops: the **brief card**, the
**reference set**, the **accepted shots** and the **final cut**. This script is
how those stops become visible: it reads what the run already wrote to disk
(the same files `backlot/shots.py` renders), builds the stage's canonical
artifact from them, and hands it to `lib/checkpoint.py`. The checkpoint writer
owns every rule — gate enforcement, prerequisites, schema validation, history —
so nothing here hand-rolls checkpoint JSON.

    make stop    PROJECT=<id> STOP=brief        # write awaiting_human, then ask
    make stop-ok PROJECT=<id> STOP=brief        # the user said yes: approve it

The stop-to-stage mapping is the skill's "Where this sits in the pipeline"
table, resolved against whichever pipeline the project declares:

    brief       -> idea       (brief)             on `prompt-faithful`
                -> proposal   (proposal_packet)   on a pipeline with no `idea`
    references  -> assets     (asset_manifest: the reference set)
    shots       -> assets     (asset_manifest: references + every take)
    cut         -> publish    (publish_log)

Two stops share the `assets` stage, in that order: approving the reference set
completes it, and the shots stop re-opens it as `awaiting_human` for the second
review. Both approvals stay readable in `history/`.

The stages BETWEEN the stops are filled on the way. `script`, `scene_plan`,
`edit` and `compose` are projections of the shot contract and the cut report,
so `fill_predecessors` writes them from disk rather than making the agent
re-type them. It never fills a gated stage, and never fills one whose artifact
cannot be derived honestly — `research` and `proposal` on `cinematic` demand
web sources and three concept directions that a shot-list brief does not have.
For those the checkpoint writer refuses and its message says which stages.

An artifact the agent wrote itself at `artifacts/<name>.json` always wins over
a derived one. `final_review` is never derived: a review is a reviewer's
judgement, and a script must not manufacture one.

Exit codes: 0 written, 1 the project or the stop is unusable, 2 the checkpoint
writer refused it (read the message: a gate, a prerequisite or a schema).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXIT_BAD_INPUT = 1
EXIT_REFUSED = 2

STOP_NAMES = ("brief", "references", "shots", "cut")

DEFAULT_PIPELINE = "prompt-faithful"
# `om_assemble.py` cuts with ffmpeg; the board's runtime field must say so.
RENDER_RUNTIME = "ffmpeg"


class StopError(ValueError):
    """The stop cannot be built from what is on disk."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def stop_target(stop: str, stages: list[str]) -> tuple[str, str]:
    """(stage, canonical artifact) for a stop, in the pipeline it runs under.

    `prompt-faithful` opens at `idea`, so the brief card is that stage's `brief`
    — which is exactly what a brief card is. `cinematic` has no `idea`, so the
    brief card lands on `proposal` and has to be dressed as a `proposal_packet`.
    """
    if stop == "brief":
        return ("idea", "brief") if "idea" in stages else ("proposal", "proposal_packet")
    if stop in ("references", "shots"):
        return ("assets", "asset_manifest")
    return ("publish", "publish_log")


def _pipeline_type(project_dir: Path, override: Optional[str]) -> str:
    if override:
        return override
    marker = project_dir / "project.json"
    if marker.is_file():
        try:
            value = json.loads(marker.read_text(encoding="utf-8")).get("pipeline_type")
            if isinstance(value, str) and value:
                return value
        except (OSError, json.JSONDecodeError):
            pass
    return DEFAULT_PIPELINE


# ---------------------------------------------------------------------------
# canonical artifacts, derived from the shot board
# ---------------------------------------------------------------------------

def _written(project_dir: Path, name: str) -> Optional[dict]:
    """An artifact the agent wrote itself always wins — it knows the run."""
    path = project_dir / "artifacts" / f"{name}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _timeline(board: dict) -> list[tuple[dict, float, float]]:
    """(shot, start, end) on the finished timeline.

    The cut report is the authority once the cut exists; before that, the
    delivered lengths in contract order say where each shot will land.
    """
    report = ((board.get("cut") or {}).get("report") or {})
    placed = {row.get("id"): row for row in (report.get("shots") or []) if isinstance(row, dict)}
    out, clock = [], 0.0
    for shot in board["shots"]:
        row = placed.get(shot["id"])
        if row and row.get("starts_at") is not None and row.get("ends_at") is not None:
            out.append((shot, float(row["starts_at"]), float(row["ends_at"])))
            continue
        length = shot.get("seconds_delivered") or shot.get("seconds_generated") or 0.0
        out.append((shot, clock, clock + float(length)))
        clock += float(length)
    return out


def _runtime(board: dict) -> float:
    timeline = _timeline(board)
    return max((end for _, _, end in timeline), default=0.0) or float(
        board["brief"].get("runtime_seconds") or 1)


def _look_entries(project_dir: Path) -> list[str]:
    """The checklist's `look` entries — what the piece is supposed to look like."""
    data = _read_checklist(project_dir)
    return [
        str(entry.get("literal"))
        for entry in (data.get("entries") or [])
        if isinstance(entry, dict) and entry.get("tag") == "look" and entry.get("literal")
    ]


def _read_checklist(project_dir: Path) -> dict:
    try:
        data = json.loads((project_dir / "artifacts" / "brief_checklist.json")
                          .read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def brief(board: dict, project_dir: Path) -> dict:
    """The brief card as the `idea` stage's canonical artifact.

    Every field is read off the brief and the contract. `tone` and `style` come
    from the checklist's own `look` entries, which is where this skill records
    what the piece must look like; `metadata.derived_from` says so, so a reader
    can tell a projection from a judgement.
    """
    written = _written(project_dir, "brief")
    if written is not None:
        return written

    card = board["brief"]
    checklist = _read_checklist(project_dir)
    deliverable = checklist.get("deliverable") if isinstance(checklist.get("deliverable"), dict) else {}
    looks = _look_entries(project_dir)
    summary = card.get("summary") or ""
    first_sentence = summary.split(".")[0].strip() if summary else ""

    key_points = [
        _clip(f"{shot['label']} {shot.get('brief_timecode') or ''} {shot.get('title') or shot['id']}", 160)
        for shot in board["shots"]
    ] or [_clip(first_sentence or "the brief's shot list", 160)]

    return {
        "version": "1.0",
        "title": _clip(card.get("project") or project_dir.name.replace("-", " ").title(), 120),
        "hook": _clip(first_sentence or "the brief, shot for shot", 200),
        "key_points": key_points,
        # The opening clause of a brief like this is its tone statement
        # ("Ultra-realistic cinematic sequence, …"); the whole sentence is the hook.
        "tone": _clip(first_sentence.split(",")[0].strip() or "as the brief states", 120),
        "style": _clip("; ".join(looks) or card.get("grade_preset") or "as the brief states", 300),
        "target_platform": "generic",
        "target_duration_seconds": float(card.get("runtime_seconds") or _runtime(board)),
        "metadata": {
            "derived_from": [card.get("source_path"), "artifacts/brief_checklist.json",
                             board["contract_path"]],
            "note": ("Projected from the brief and the shot contract by scripts/om_stop.py. "
                     "tone and style are the checklist's `look` entries; the brief itself is "
                     "the authority."),
            "deliverable": deliverable,
            "checklist_entries": (card.get("checklist") or {}).get("total"),
            "conflicts": len((card.get("checklist") or {}).get("conflicts") or []),
            "budget_usd": card.get("budget_usd"),
            "max_attempts_per_shot": card.get("max_attempts_per_shot"),
            "person_route": card.get("person_route"),
            "look_at": ["BRIEF"],
        },
    }


def script(board: dict, project_dir: Path) -> dict:
    """The brief's shot list, recorded as the script. No narration is invented."""
    written = _written(project_dir, "script")
    if written is not None:
        return written

    sections = []
    for shot, start, end in _timeline(board):
        section: dict[str, Any] = {
            "id": shot["id"],
            "label": _clip(shot.get("title") or shot["id"], 120),
            "text": _clip(shot.get("action") or shot.get("title") or shot["id"], 2000),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
        }
        if shot.get("camera"):
            section["speaker_directions"] = _clip(shot["camera"], 500)
        sections.append(section)
    if not sections:
        raise StopError("the shot contract has no rows, so there is no script to record")

    return {
        "version": "1.0",
        "title": _clip(board["brief"].get("project") or project_dir.name, 120),
        "total_duration_seconds": _runtime(board),
        "sections": sections,
        "metadata": {
            "derived_from": board["contract_path"],
            "note": ("This brief carries a shot list, not narration. Each section's `text` "
                     "is that shot's action line from the contract."),
        },
    }


def scene_plan(board: dict, project_dir: Path) -> dict:
    """The shot contract, recorded as the scene plan."""
    written = _written(project_dir, "scene_plan")
    if written is not None:
        return written

    scenes = []
    for shot, start, end in _timeline(board):
        scene: dict[str, Any] = {
            "id": shot["id"],
            "type": "generated",
            "description": _clip(shot.get("title") or shot["id"], 200),
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "script_section_id": shot["id"],
        }
        if shot.get("lens"):
            scene["framing"] = _clip(shot["lens"], 300)
        if shot.get("camera"):
            scene["movement"] = _clip(shot["camera"], 500)
        if shot.get("action"):
            scene["shot_intent"] = _clip(shot["action"], 500)
        if shot.get("frame_contains"):
            scene["texture_keywords"] = [_clip(item, 120) for item in shot["frame_contains"][:20]]
        scenes.append(scene)
    if not scenes:
        raise StopError("the shot contract has no rows, so there is no scene plan to record")

    return {
        "version": "1.0",
        "scenes": scenes,
        "metadata": {
            "derived_from": board["contract_path"],
            "note": "The shot contract is this pipeline's scene plan; see Phase 4 of the skill.",
        },
    }


def edit_decisions(board: dict, project_dir: Path) -> dict:
    """The trims and the joins `om_assemble.py` actually performed."""
    written = _written(project_dir, "edit_decisions")
    if written is not None:
        return written

    report = ((board.get("cut") or {}).get("report") or {})
    placed = {row.get("id"): row for row in (report.get("shots") or []) if isinstance(row, dict)}
    cuts = []
    for shot in board["shots"]:
        keep = (shot.get("trim") or {}).get("keep_seconds")
        start, end = 0.0, shot.get("seconds_delivered") or shot.get("seconds_generated")
        if isinstance(keep, str) and "-" in keep:
            try:
                start, end = (float(part) for part in keep.split("-", 1))
            except ValueError:
                pass
        if end is None:
            continue
        cut: dict[str, Any] = {
            "id": shot["id"],
            "source": shot["accepted_take"] and next(
                (t["path"] for t in shot["takes"] if t["accepted"]), None
            ) or f"assets/video/{shot['id']}.mp4",
            "in_seconds": round(float(start), 3),
            "out_seconds": round(float(end), 3),
        }
        row = placed.get(shot["id"])
        if row and row.get("entered_by"):
            cut["transition_in"] = str(row["entered_by"])
            if row.get("transition_seconds"):
                cut["transition_duration"] = float(row["transition_seconds"])
        if (shot.get("trim") or {}).get("why"):
            cut["reason"] = _clip(shot["trim"]["why"], 500)
        cuts.append(cut)
    if not cuts:
        raise StopError("no shot has a delivered length, so there is nothing to cut")

    return {
        "version": "1.0",
        "cuts": cuts,
        "render_runtime": RENDER_RUNTIME,
        "metadata": {
            "derived_from": [board["contract_path"], (board.get("cut") or {}).get("report_path")],
            "grade": report.get("grade") or board["brief"].get("grade_preset"),
            "edited_from": report.get("edited_from"),
            "edit_instructions": (board.get("cut") or {}).get("edit_path"),
            "note": "scripts/om_assemble.py joins these with ffmpeg; see Phase 7 of the skill.",
        },
    }


def _probe(path: Path) -> dict:
    """ffprobe the deliverable. Local only; absent ffprobe is not an error."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", str(path)],
            check=True, capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    stream = (data.get("streams") or [{}])[0]
    probed: dict[str, Any] = {}
    if stream.get("width") and stream.get("height"):
        probed["resolution"] = f"{stream['width']}x{stream['height']}"
    rate = str(stream.get("r_frame_rate") or "")
    if "/" in rate:
        top, _, bottom = rate.partition("/")
        try:
            probed["fps"] = round(float(top) / float(bottom or 1), 3)
        except (ValueError, ZeroDivisionError):
            pass
    try:
        probed["duration_seconds"] = round(float(data["format"]["duration"]), 3)
    except (KeyError, TypeError, ValueError):
        pass
    return probed


def render_report(board: dict, project_dir: Path) -> dict:
    """What was actually rendered. A record of ffmpeg's output, not a judgement.

    `final_review` is deliberately NOT built here: a review is evidence from a
    reviewer, and a script must never manufacture one.
    """
    written = _written(project_dir, "render_report")
    if written is not None:
        return written

    cut = board.get("cut") or {}
    if not cut.get("path"):
        raise StopError("no assembled cut yet: run `make assemble PROJECT=<id>` first")
    probed = _probe(project_dir / cut["path"])
    report = cut.get("report") or {}
    accepted_actual = next(
        ((take["review"] or {}).get("actual") or {}
         for shot in board["shots"] for take in shot["takes"]
         if take["accepted"] and take.get("review")), {})

    resolution = probed.get("resolution")
    if not resolution and accepted_actual.get("width") and accepted_actual.get("height"):
        resolution = f"{accepted_actual['width']}x{accepted_actual['height']}"
    if not resolution:
        resolution = str((board["brief"].get("route") or {}).get("resolution") or "unknown")

    output: dict[str, Any] = {
        "path": cut["path"],
        "format": Path(cut["path"]).suffix.lstrip(".") or "mp4",
        "resolution": resolution,
        "duration_seconds": float(
            probed.get("duration_seconds")
            or report.get("runtime_seconds")
            or _runtime(board)),
    }
    if probed.get("fps"):
        output["fps"] = probed["fps"]
    if cut.get("size_bytes"):
        output["file_size_bytes"] = int(cut["size_bytes"])

    return {
        "version": "1.0",
        "outputs": [output],
        "verification_notes": [
            _clip(f"cut report: {cut.get('report_path')}", 300),
            _clip(f"runtime {report.get('runtime_seconds')}s against "
                  f"{report.get('runtime_expected')}s, runtime_ok={report.get('runtime_ok')}", 300),
            _clip(f"cuts at {report.get('cuts_at')}, grade {report.get('grade')}", 300),
            _clip("resolution and duration read by ffprobe" if probed
                  else "ffprobe unavailable: resolution and duration taken from the "
                       "adherence files and the cut report", 300),
        ],
        "metadata": {
            "derived_from": cut.get("report_path"),
            "probed": bool(probed),
            "cut_review": cut.get("review_path"),
            "note": ("final_review is not derived here: a review is a reviewer's judgement, "
                     "not a projection of the output. See Phase 7 of the skill."),
        },
    }


def _alternatives(project_dir: Path, contract_notes: list[dict]) -> list[dict]:
    """Directions this run weighed at the brief card and did not take.

    `proposal_packet` wants at least three concept options. A client brief with
    a shot list supplies exactly one direction, so the other entries are not
    invented: they are the options the run actually recorded as considered and
    rejected, in `brief_checklist.json`'s `decisions` and the contract's own
    decision notes. Each is titled "Rejected: …" so nobody reads it as an offer.
    """
    checklist = project_dir / "artifacts" / "brief_checklist.json"
    decisions = []
    try:
        data = json.loads(checklist.read_text(encoding="utf-8"))
        decisions = [d for d in (data.get("decisions") or []) if isinstance(d, dict)]
    except (OSError, json.JSONDecodeError, AttributeError):
        decisions = []

    out: list[dict] = []
    for decision in decisions:
        chosen = str(decision.get("chosen") or "")
        rejected_because = decision.get("rejected_because") or {}
        for option in decision.get("options_considered") or []:
            option = str(option)
            if not option or option in chosen or chosen.startswith(option):
                continue
            reason = ""
            if isinstance(rejected_because, dict):
                reason = str(next(
                    (v for k, v in rejected_because.items() if k in option or option in k), ""))
            out.append({
                "title": _clip(f"Rejected: {option}", 120),
                "hook": _clip(f"{decision.get('subject') or 'Weighed at the brief card'} — "
                              f"chosen instead: {chosen}", 200),
                "why": _clip(reason or "Recorded as considered and not taken; the brief card "
                                       "carries the chosen option.", 500),
            })
    for note in contract_notes:
        out.append({
            "title": _clip(f"Rejected: the alternative to {note.get('subject') or note['key']}", 120),
            "hook": _clip(note.get("subject") or str(note["key"]).replace("_", " "), 200),
            "why": _clip(note["text"], 500),
        })
    return out


def proposal_packet(board: dict, project_dir: Path, pipeline_type: str) -> dict:
    """The brief card as the proposal stage's canonical artifact.

    A packet the agent wrote itself (`artifacts/proposal_packet.json`) wins —
    it knows the conversation. Otherwise the packet is a projection of
    `shot_contract.json` and `brief_checklist.json`: the brief is the selected
    concept, and the rejected alternatives the run recorded fill the schema's
    other concept slots.
    """
    written = project_dir / "artifacts" / "proposal_packet.json"
    if written.is_file():
        try:
            data = json.loads(written.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass

    brief = board["brief"]
    spend = board["spend"]
    shots = board["shots"]
    runtime = brief.get("runtime_seconds") or sum(
        (shot.get("seconds_delivered") or shot.get("seconds_generated") or 0) for shot in shots
    ) or 1
    route = brief.get("route") or {}
    tool = route.get("tool") or "seedance_ark"
    estimate = brief.get("cost_estimate") or {}
    budget = brief.get("budget_usd")
    estimated = spend.get("total_usd")
    if estimated is None:
        estimated = 0.0

    if budget is None:
        verdict = "no_budget_set"
    elif estimated > budget:
        verdict = "over_budget"
    elif estimated > budget * 0.75:
        verdict = "near_limit"
    else:
        verdict = "within_budget"

    cost: dict[str, Any] = {
        "total_estimated_usd": round(float(estimated), 4),
        "line_items": [
            {
                "tool": tool,
                "operation": f"{len(shots)} contracted shots, one generation per shot",
                "quantity": len(shots),
                "estimated_usd": round(float(estimated), 4),
                "notes": _clip(estimate.get("batch_total") or spend.get("source"), 200),
            }
        ],
        "budget_verdict": verdict,
    }
    if budget is not None:
        cost["budget_cap_usd"] = float(budget)

    visual_approach = _clip(
        f"{len(shots)} generated shots, one generation each, hard cuts, "
        f"grade {brief.get('grade_preset') or 'none'}", 300)
    concepts = [{
        "id": "c1",
        "title": _clip(brief.get("project") or "Contracted shot list", 120),
        "hook": _clip(brief.get("summary") or "The brief, shot for shot.", 160),
        "narrative_structure": "story",
        "visual_approach": visual_approach,
        "target_duration_seconds": float(runtime),
        "why_this_works": (
            "The brief already names every shot, its framing and its action. This "
            "production delivers that shot list literally: one generation per shot "
            "against a written contract, each output scored against the brief by an "
            "independent reviewer."
        ),
    }]
    for index, alt in enumerate(_alternatives(project_dir, brief.get("notes") or []), start=2):
        concepts.append({
            "id": f"c{index}",
            "title": alt["title"],
            "hook": alt["hook"],
            "narrative_structure": "story",
            "visual_approach": visual_approach,
            "target_duration_seconds": float(runtime),
            "why_this_works": alt["why"],
        })
    if len(concepts) < 3:
        raise StopError(
            "the proposal_packet schema wants at least three concept directions, and this "
            f"brief recorded {len(concepts)}. A client brief with a shot list supplies one, "
            "and nothing here will invent the others. Record the alternatives you weighed in "
            "brief_checklist.json's `decisions` (`options_considered` + `rejected_because`), "
            "or write artifacts/proposal_packet.json yourself and rerun — a packet on disk wins."
        )

    return {
        "version": "1.0",
        "concept_options": concepts,
        "selected_concept": {
            "concept_id": "c1",
            "rationale": (
                "The brief is the concept — the user supplied the shot list, so there "
                "is nothing to choose between. The open-montage skill's job is "
                "faithfulness to it."
            ),
        },
        "production_plan": {
            "pipeline": pipeline_type,
            "stages": [{
                "stage": "assets",
                "tools": [{
                    "tool_name": tool,
                    "role": "one video generation per contracted shot",
                    "available": True,
                }],
                "approach": (
                    "open-montage: checklist from the brief, reviewed prompts, a "
                    "reference set the user accepts, then one shot per call with an "
                    "independent adherence score on every take."
                ),
            }],
            "render_runtime": RENDER_RUNTIME,
        },
        "cost_estimate": cost,
        "approval": {"status": "pending"},
    }


def _reference_assets(board: dict) -> list[dict]:
    route = (board["brief"].get("route") or {})
    tool = route.get("tool") or "seedance_ark"
    assets = []
    for item in board["references"]["items"]:
        assets.append({
            "id": item["label"],
            "type": item["kind"],
            "path": item["path"],
            "source_tool": tool,
            "scene_id": "reference",
            "subtype": "reference",
            "generation_summary": _clip(
                f"{item['label']} {item['name']}"
                + (f" · used by {' '.join(item['used_by'])}" if item["used_by"] else " · not a shot reference")
                + (f" · reviewer: {item['verdict']}" if item.get("verdict") else ""),
                400),
        })
    return assets


def _take_assets(board: dict) -> list[dict]:
    route = (board["brief"].get("route") or {})
    tool = route.get("tool") or "seedance_ark"
    assets = []
    for shot in board["shots"]:
        for take in shot["takes"]:
            summary = (
                f"{take['label']} of {shot['label']} ({shot['id']})"
                + (" · ACCEPTED" if take["accepted"] else " · superseded")
                + (f" · reviewer: {take['verdict']}" if take.get("verdict") else "")
                + (f" · prompt: {shot['prompt_path']}" if shot.get("prompt_path") else "")
                + (f" · references: {' '.join(shot['reference_labels'])}" if shot["reference_labels"] else "")
                + (f" · {take['note']}" if take.get("note") else "")
            )
            asset: dict[str, Any] = {
                "id": take["label"],
                "type": "video",
                "path": take["path"],
                "source_tool": tool,
                "scene_id": shot["id"],
                "subtype": "accepted_take" if take["accepted"] else "superseded_take",
                "generation_summary": _clip(summary, 900),
            }
            if take["cost_usd"] is not None:
                asset["cost_usd"] = float(take["cost_usd"])
            if shot.get("seconds_generated") is not None:
                asset["duration_seconds"] = float(shot["seconds_generated"])
            if route.get("resolution"):
                asset["resolution"] = str(route["resolution"])
            assets.append(asset)
    return assets


def asset_manifest(board: dict, stop: str) -> dict:
    """The reference set, and at the shots stop every take beside it."""
    assets = _reference_assets(board)
    if stop == "shots":
        assets += _take_assets(board)
    if not assets:
        raise StopError(
            "nothing to show yet: no reference file under assets/reference/"
            + (" and no take under assets/video/" if stop == "shots" else "")
        )
    total = board["spend"].get("total_usd")
    manifest: dict[str, Any] = {"version": "1.0", "assets": assets}
    if total is not None:
        manifest["total_cost_usd"] = float(total)
    manifest["metadata"] = {
        "stop": stop,
        "source": board["contract_path"],
        "label_scheme": board["label_scheme"],
        "accepted_takes": {
            shot["label"]: shot["accepted_take"]
            for shot in board["shots"] if shot["accepted_take"]
        },
        "look_at": _labels_for(board, stop),
    }
    return manifest


def publish_log(board: dict, approved: bool) -> dict:
    cut = board.get("cut")
    if not cut or not cut.get("path"):
        raise StopError("no assembled cut yet: run `make assemble PROJECT=<id>` first")
    report = cut.get("report") or {}
    entry: dict[str, Any] = {
        "platform": "local_delivery",
        "status": "exported" if approved else "pending_review",
        "timestamp": _now(),
        "export_path": cut["path"],
    }
    log: dict[str, Any] = {"version": "1.0", "entries": [entry]}
    log["metadata"] = {
        "stop": "cut",
        "runtime_seconds": report.get("runtime_seconds"),
        "runtime_ok": report.get("runtime_ok"),
        "cuts_at": report.get("cuts_at"),
        "grade": report.get("grade"),
        "cut_report": cut.get("report_path"),
        "cut_review": cut.get("review_path"),
        "look_at": _labels_for(board, "cut"),
    }
    return log


# ---------------------------------------------------------------------------
# what to tell the user to look at
# ---------------------------------------------------------------------------

def _labels_for(board: dict, stop: str) -> list[str]:
    """The labels on the board this stop is asking the user to look at."""
    if stop == "brief":
        return ["BRIEF"]
    if stop == "references":
        return [item["label"] for item in board["references"]["items"]]
    if stop == "shots":
        return [
            take["label"]
            for shot in board["shots"] for take in shot["takes"]
        ]
    return ["CUT"]


# ---------------------------------------------------------------------------
# driver
# ---------------------------------------------------------------------------

# Canonical artifact name -> how to build it from what is on disk. A stage whose
# artifact is not here cannot be filled from the shot contract, and the
# checkpoint writer's prerequisite message is then the right answer.
BUILDERS = {
    "brief": brief,
    "script": script,
    "scene_plan": scene_plan,
    "edit_decisions": edit_decisions,
    "render_report": render_report,
}


def build_artifact(board: dict, project_dir: Path, stop: str, pipeline_type: str,
                   artifact_name: str, approved: bool) -> dict:
    if stop == "brief":
        if artifact_name == "brief":
            return brief(board, project_dir)
        packet = proposal_packet(board, project_dir, pipeline_type)
        if approved:
            packet["approval"]["status"] = "approved"
            if packet["cost_estimate"].get("budget_cap_usd") is not None:
                packet["approval"]["approved_budget_usd"] = packet["cost_estimate"]["budget_cap_usd"]
        return packet
    if stop in ("references", "shots"):
        return asset_manifest(board, stop)
    return publish_log(board, approved)


def fill_predecessors(project_dir: Path, board: dict, pipeline_type: str,
                      stage: str) -> list[str]:
    """Write the mechanical stages that sit before this stop, if they are missing.

    `script`, `scene_plan`, `edit` and `compose` are projections of the shot
    contract and the cut report — the run already decided everything in them, so
    re-typing them by hand is a transcription exercise with a schema attached.

    Two things are never filled here. A **gated** stage is somebody's approval and
    is only ever written by its own stop. A stage whose artifact has no builder
    (`research`, `proposal`) cannot be derived honestly from a shot-list brief, so
    it is left for the checkpoint writer to refuse with its own message.
    """
    from lib.checkpoint import (CANONICAL_STAGE_ARTIFACTS, _stage_requires_approval,
                                get_pipeline_stages, read_checkpoint, write_checkpoint)

    stages = get_pipeline_stages(pipeline_type)
    if stage not in stages:
        return []
    filled = []
    for predecessor in stages[: stages.index(stage)]:
        if _stage_requires_approval(pipeline_type, predecessor):
            continue
        try:
            existing = read_checkpoint(project_dir.parent, project_dir.name, predecessor)
        except Exception:
            existing = None
        if existing is not None and existing.get("status") == "completed":
            continue
        artifact_name = CANONICAL_STAGE_ARTIFACTS.get(predecessor)
        builder = BUILDERS.get(artifact_name or "")
        if builder is None:
            continue
        try:
            artifact = builder(board, project_dir)
        except StopError:
            continue
        write_checkpoint(
            project_dir.parent, project_dir.name, predecessor, "completed",
            {artifact_name: artifact},
            pipeline_type=pipeline_type,
            metadata={"derived_by": "scripts/om_stop.py",
                      "source": board["contract_path"]},
        )
        filled.append(predecessor)
    return filled


def _ensure_marker(project_dir: Path, board: dict, pipeline_type: str) -> None:
    """Write project.json when the project has none.

    A project the skill started by hand has a contract but no marker, and the
    board reads the pipeline (so the stage rail and its gates) from the marker
    alone: without it every checkpoint written here renders as pending.
    """
    if (project_dir / "project.json").is_file():
        return
    from lib.checkpoint import init_project
    title = (board.get("brief") or {}).get("title") or project_dir.name
    init_project(project_dir.name, title=title, pipeline_type=pipeline_type,
                 pipeline_dir=project_dir.parent)


def write_stop(project_dir: Path, stop: str, approve: bool,
               pipeline_type: Optional[str] = None, note: Optional[str] = None) -> int:
    from backlot.shots import load_shot_board
    from lib.checkpoint import (CheckpointValidationError, get_pipeline_stages,
                                write_checkpoint)

    board = load_shot_board(project_dir)
    if board is None:
        print(f"no shot contract at {project_dir / 'artifacts/shot_contract.json'}: "
              "this is not an open-montage project")
        return EXIT_BAD_INPUT

    resolved_pipeline = _pipeline_type(project_dir, pipeline_type)
    _ensure_marker(project_dir, board, resolved_pipeline)
    stage, artifact_name = stop_target(stop, get_pipeline_stages(resolved_pipeline))
    try:
        artifact = build_artifact(board, project_dir, stop, resolved_pipeline,
                                  artifact_name, approve)
    except StopError as exc:
        print(f"STOP        {exc}")
        return EXIT_BAD_INPUT

    try:
        filled = fill_predecessors(project_dir, board, resolved_pipeline, stage)
    except (CheckpointValidationError, ValueError) as exc:
        print(f"REFUSED     while filling an earlier stage: {exc}")
        return EXIT_REFUSED
    if filled:
        print(f"FILLED      {' '.join(filled)}   (derived from the contract and the cut report)")

    labels = _labels_for(board, stop)
    metadata: dict[str, Any] = {"stop": stop, "look_at": labels}
    if note:
        metadata["note"] = note

    spend = board["spend"]
    cost_snapshot = None
    if spend.get("total_usd") is not None:
        cost_snapshot = {"total_spent_usd": float(spend["total_usd"])}
        if spend.get("remaining_usd") is not None:
            cost_snapshot["budget_remaining_usd"] = float(spend["remaining_usd"])

    try:
        path = write_checkpoint(
            project_dir.parent,
            project_dir.name,
            stage,
            "completed" if approve else "awaiting_human",
            {artifact_name: artifact},
            pipeline_type=resolved_pipeline,
            human_approval_required=True,
            human_approved=approve,
            cost_snapshot=cost_snapshot,
            metadata=metadata,
        )
    except (CheckpointValidationError, ValueError) as exc:
        print(f"REFUSED     {exc}")
        if "PREREQUISITE" in str(exc):
            print()
            print("            The checkpoint writer requires every earlier stage of the pipeline to")
            print("            be completed (and approved where the manifest gates it) before a later")
            print("            one advances. Those stages are yours to write the ordinary way, per")
            print("            skills/meta/checkpoint-protocol.md — this script only writes the four")
            print("            stops. The skill's pipeline table says what each earlier stage owes;")
            print("            a stage listed as 'none' still owes its canonical artifact, recording")
            print("            that there was nothing to do.")
        return EXIT_REFUSED

    print(f"STOP        {stop} -> stage {stage} ({artifact_name})")
    print(f"STATUS      {'completed, human_approved' if approve else 'awaiting_human'}")
    print(f"WROTE       {path}")
    if labels:
        shown = labels if len(labels) <= 12 else labels[:12] + [f"… +{len(labels) - 12} more"]
        print(f"LOOK AT     {' '.join(shown)}")
    if not approve:
        print("\nTell the user which labels to look at on the board, then END YOUR TURN.")
        print(f"When they approve: make stop-ok PROJECT={project_dir.name} STOP={stop}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project", help="project id under projects/")
    parser.add_argument("stop", choices=sorted(STOP_NAMES), help="which of the skill's four stops")
    parser.add_argument("--approve", action="store_true",
                        help="the user said yes: write the stage completed with human_approved")
    parser.add_argument("--note", help="one line from the user, kept in the checkpoint metadata")
    parser.add_argument("--pipeline", help=f"pipeline type; default: project.json, else {DEFAULT_PIPELINE}")
    parser.add_argument("--projects-dir", help="projects root (default: the repo's projects/)")
    args = parser.parse_args(argv)

    from lib.paths import PROJECTS_DIR
    root = Path(args.projects_dir) if args.projects_dir else PROJECTS_DIR
    project_dir = root / args.project
    if not project_dir.is_dir():
        print(f"no project at {project_dir}")
        return EXIT_BAD_INPUT
    return write_stop(project_dir, args.stop, args.approve, args.pipeline, args.note)


if __name__ == "__main__":
    raise SystemExit(main())
