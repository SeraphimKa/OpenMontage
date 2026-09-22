"""Shot-board derivation — what an `open-montage` skill production wrote to disk.

The standard pipeline writes `scene_plan.json` + `asset_manifest.json` and the
board joins them into a filmstrip. The `open-montage` skill writes a different
set of files (`artifacts/shot_contract.json`, `artifacts/prompts/<shot>.txt`,
`artifacts/adherence/<shot>.json`, `assets/reference/`, `artifacts/spend_log.json`,
`artifacts/final/cut_report.json`), so the filmstrip stays empty for it.

This module reads those files and returns the four things a non-technical
operator has to be able to see without opening Finder: the brief they agreed,
the reference set, every take of every shot with its prompt, its independent
adherence verdict, its cost and whether it was accepted, and the final cut.

Everything here is read-only and defensive, like `backlot.state`: a malformed
JSON file or a half-written artifact must degrade the board, never crash it.

Labels (acceptance: short, stable, derived from disk):

    BRIEF   the brief card
    R1..Rn  reference files, ordered by project-relative path
    S1..Sn  shots, in shot-contract row order
    S2-A..  takes of a shot: kept attempts by attempt number, current take last
    CUT     the assembled deliverable

A new take landing never renumbers an existing one: `om_shot.py` moves the
current take to `<shot>.attemptN.mp4` before writing the new one, and N is
larger than every attempt already on disk, so the kept take keeps its position
in the ordering (and therefore its letter).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

MEDIA_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MEDIA_VIDEO_EXT = {".mp4", ".webm", ".mov"}

CONTRACT_REL = "artifacts/shot_contract.json"
SPEND_LOG_REL = "artifacts/spend_log.json"
ADHERENCE_REL = "artifacts/adherence"
PROMPTS_REL = "artifacts/prompts"
REFERENCE_REL = "assets/reference"
VIDEO_REL = "assets/video"
FINAL_REL = "artifacts/final"

# Intermediate renders `om_assemble.py` writes; never takes.
VIDEO_SKIP_DIRS = {"work"}

# Contract keys rendered as the brief card's free-text notes, in this order.
NOTE_KEYS = (
    "duration_note",
    "single_lens_note",
    "generation_order_reason",
    "posture_decision",
    "seated_reference_plan",
    "provider_constraint",
    "perturbation_applied",
    "grade_decision",
    "spend_note",
)

BRIEF_SUMMARY_CHARS = 700


def _read_json(path: Path) -> Optional[Any]:
    """Read a JSON file, returning None on any failure (any top-level type)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeError, ValueError):
        return None


def _read_json_dict(path: Path) -> Optional[dict]:
    data = _read_json(path)
    return data if isinstance(data, dict) else None


def _read_text(path: Path, limit: Optional[int] = None) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text[:limit] if limit else text


def _rel(project_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except (ValueError, OSError):
        return path.name


def _stat(path: Path) -> tuple[Optional[int], Optional[float]]:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return None, None


def _kind(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in MEDIA_IMAGE_EXT:
        return "image"
    if ext in MEDIA_VIDEO_EXT:
        return "video"
    return None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------

def take_letters(index: int) -> str:
    """A, B, ... Z, AA, AB — so a shot with 27 takes still gets a short label."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


def _attempt_number(path: Path, shot_id: str) -> Optional[int]:
    """`shot_02.attempt3.mp4` → 3; the current `shot_02.mp4` → None."""
    m = re.fullmatch(rf"{re.escape(shot_id)}\.attempt(\d+)", path.stem)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# spend
# ---------------------------------------------------------------------------

def _spend_attempts(project_dir: Path, contract: dict) -> tuple[list[dict], Optional[float], str]:
    """Every logged attempt, the running total, and which file it came from.

    `om_shot.py` writes `artifacts/spend_log.json` ({"attempts": [...],
    "total_usd": n}). Runs that predate it keep the same facts inline in the
    contract as `spend_log` + `running_total_usd`; both shapes are read.
    """
    log = _read_json_dict(project_dir / SPEND_LOG_REL)
    if log and isinstance(log.get("attempts"), list):
        attempts = [a for a in log["attempts"] if isinstance(a, dict)]
        total = _number(log.get("total_usd"))
        if total is None:
            total = round(sum(_number(a.get("usd")) or 0.0 for a in attempts), 4)
        return attempts, total, SPEND_LOG_REL

    inline = contract.get("spend_log")
    if isinstance(inline, list):
        attempts = [a for a in inline if isinstance(a, dict)]
        total = _number(contract.get("running_total_usd"))
        if total is None:
            total = round(sum(_number(a.get("usd")) or 0.0 for a in attempts), 4)
        return attempts, total, CONTRACT_REL

    return [], _number(contract.get("running_total_usd")), CONTRACT_REL


def _attempt_shot_id(attempt: dict, shot_ids: list[str]) -> Optional[str]:
    """Which shot an attempt belongs to.

    `om_shot.py` records `{"shot": "shot_02"}`. The inline contract log uses a
    free-text `job` ("shot_02 attempt 3"), so fall back to a prefix match —
    longest id first, so `shot_1` never swallows `shot_10`.
    """
    shot = attempt.get("shot")
    if isinstance(shot, str) and shot in shot_ids:
        return shot
    job = attempt.get("job")
    if isinstance(job, str):
        for shot_id in sorted(shot_ids, key=len, reverse=True):
            if job == shot_id or job.startswith(f"{shot_id} ") or job.startswith(f"{shot_id}."):
                return shot_id
    return None


def _is_billed(attempt: dict) -> bool:
    return (_number(attempt.get("usd")) or 0.0) > 0


# ---------------------------------------------------------------------------
# reviews
# ---------------------------------------------------------------------------

def _review_for(project_dir: Path, media: Path) -> tuple[Optional[dict], Optional[str]]:
    """The clean reviewer's verdict for one file, wherever the skill put it.

    Phase 2d writes `<role>.review.json` beside the image; Phase 6 writes
    `artifacts/adherence/<stem>.json` for clips.
    """
    for candidate in (
        media.with_suffix(media.suffix + ".review.json"),
        media.with_name(f"{media.stem}.review.json"),
        project_dir / ADHERENCE_REL / f"{media.stem}.json",
    ):
        data = _read_json_dict(candidate)
        if data is not None:
            return data, _rel(project_dir, candidate)
    return None, None


def _verdict_of(review: Optional[dict]) -> Optional[str]:
    if not review:
        return None
    verdict = review.get("verdict")
    return verdict if isinstance(verdict, str) else None


def _sheets_for(project_dir: Path, stem: str) -> list[str]:
    """Contact sheets and stills `make shot-sheet` built for one clip."""
    out = []
    adherence_dir = project_dir / ADHERENCE_REL
    if adherence_dir.is_dir():
        try:
            for f in sorted(adherence_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in MEDIA_IMAGE_EXT and f.stem.startswith(f"{stem}_"):
                    out.append(_rel(project_dir, f))
        except OSError:
            pass
    return out


# ---------------------------------------------------------------------------
# brief card
# ---------------------------------------------------------------------------

def _checklist_summary(project_dir: Path) -> Optional[dict]:
    data = _read_json_dict(project_dir / "artifacts" / "brief_checklist.json")
    if data is None:
        return None
    entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
    tags: dict[str, int] = {}
    priorities: dict[str, int] = {}
    for entry in entries:
        tag = entry.get("tag")
        if isinstance(tag, str) and tag:
            tags[tag] = tags.get(tag, 0) + 1
        priority = entry.get("priority")
        if isinstance(priority, str) and priority:
            priorities[priority] = priorities.get(priority, 0) + 1
    conflicts = [
        {
            "id": c.get("id"),
            "issue": c.get("issue"),
            "status": c.get("status"),
        }
        for c in (data.get("conflicts") or [])
        if isinstance(c, dict)
    ]
    return {
        "path": "artifacts/brief_checklist.json",
        "total": len(entries),
        "tags": tags,
        "priorities": priorities,
        "deliverable": data.get("deliverable") if isinstance(data.get("deliverable"), dict) else None,
        "conflicts": conflicts,
    }


def _brief_card(project_dir: Path, contract: dict) -> dict:
    source = project_dir / "artifacts" / "brief_source.md"
    summary = None
    if source.is_file():
        text = _read_text(source, 4000) or ""
        body = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
        summary = body.strip()[:BRIEF_SUMMARY_CHARS].strip()

    notes = []
    for key in NOTE_KEYS:
        value = contract.get(key)
        if isinstance(value, str) and value.strip():
            notes.append({"key": key, "text": value.strip()})
        elif isinstance(value, dict):
            text = value.get("chosen") or value.get("plan") or value.get("meaning") or value.get("why")
            if isinstance(text, str) and text.strip():
                notes.append({
                    "key": key,
                    "subject": value.get("subject") or value.get("problem") or value.get("discovered"),
                    "text": text.strip(),
                })

    return {
        "label": "BRIEF",
        "project": contract.get("project"),
        "source_path": "artifacts/brief_source.md" if source.is_file() else None,
        "summary": summary,
        "checklist": _checklist_summary(project_dir),
        "route": contract.get("route") if isinstance(contract.get("route"), dict) else None,
        "budget_usd": _number(contract.get("budget_usd")),
        "max_attempts_per_shot": contract.get("max_attempts_per_shot"),
        "runtime_seconds": _number(contract.get("runtime_seconds")),
        "grade_preset": contract.get("grade_preset"),
        "generation_order": [s for s in (contract.get("generation_order") or []) if isinstance(s, str)],
        "cost_estimate": contract.get("cost_estimate_480p_16x9")
        if isinstance(contract.get("cost_estimate_480p_16x9"), dict) else None,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# reference set
# ---------------------------------------------------------------------------

def _reference_items(project_dir: Path) -> list[dict]:
    """Every playable reference file under assets/reference/, R1..Rn by path.

    Sorted by project-relative path, so the numbering is the same on every
    reload. `.unused` and README.md fall out on the extension check.
    """
    root = project_dir / REFERENCE_REL
    if not root.is_dir():
        return []
    files: list[Path] = []
    try:
        for f in root.rglob("*"):
            if f.is_file() and _kind(f):
                files.append(f)
    except OSError:
        return []
    files.sort(key=lambda f: _rel(project_dir, f))

    items = []
    for index, f in enumerate(files, start=1):
        review, review_path = _review_for(project_dir, f)
        size, mtime = _stat(f)
        items.append({
            "label": f"R{index}",
            "name": f.name,
            "path": _rel(project_dir, f),
            "kind": _kind(f),
            "review": review,
            "review_path": review_path,
            "verdict": _verdict_of(review),
            "size_bytes": size,
            "mtime": mtime,
            "used_by": [],
        })
    return items


def _reference_set(project_dir: Path, contract: dict, items: list[dict]) -> dict:
    readme = project_dir / REFERENCE_REL / "README.md"
    accepted = contract.get("reference_set_accepted")
    return {
        "label": "REFS",
        "items": items,
        "readme_path": _rel(project_dir, readme) if readme.is_file() else None,
        "accepted": accepted if isinstance(accepted, dict) else None,
        "assignment": contract.get("reference_assignment")
        if isinstance(contract.get("reference_assignment"), dict) else None,
    }


# ---------------------------------------------------------------------------
# shots + takes
# ---------------------------------------------------------------------------

def _take_files(project_dir: Path, shot_id: str) -> list[Path]:
    """Kept attempts then the current take — the order the letters follow."""
    video_dir = project_dir / VIDEO_REL
    if not video_dir.is_dir():
        return []
    attempts: list[tuple[int, Path]] = []
    current: Optional[Path] = None
    try:
        entries = sorted(video_dir.iterdir())
    except OSError:
        return []
    for f in entries:
        if not f.is_file() or f.suffix.lower() not in MEDIA_VIDEO_EXT:
            continue
        if f.parent.name in VIDEO_SKIP_DIRS:
            continue
        if f.stem == shot_id:
            current = f
            continue
        n = _attempt_number(f, shot_id)
        if n is not None:
            attempts.append((n, f))
    attempts.sort(key=lambda pair: (pair[0], pair[1].name))
    ordered = [f for _, f in attempts]
    if current is not None:
        ordered.append(current)
    return ordered


def _accepted_take_path(project_dir: Path, shot_id: str, adherence: Optional[dict],
                        takes: list[Path]) -> Optional[str]:
    """Which take the reviewer signed off, as a project-relative path.

    The adherence file names the clip it judged; that is the authority. With no
    adherence file the current `<shot>.mp4` is the accepted one by convention
    (`om_assemble.py` assembles exactly those files).
    """
    if adherence:
        verdict = (_verdict_of(adherence) or "").strip().lower()
        if verdict and not verdict.startswith("accept"):
            return None  # the reviewer sent it back; nothing here is accepted
        clip = adherence.get("clip")
        if isinstance(clip, str) and clip:
            resolved = (project_dir / clip)
            if resolved.is_file():
                return _rel(project_dir, resolved)
    for f in takes:
        if f.stem == shot_id:
            return _rel(project_dir, f)
    return None


def _shot_rows(contract: dict) -> list[dict]:
    return [r for r in (contract.get("shots") or []) if isinstance(r, dict) and r.get("id")]


def _build_shot(
    project_dir: Path,
    index: int,
    row: dict,
    attempts_by_shot: dict[str, list[dict]],
    references: list[dict],
) -> dict:
    shot_id = str(row["id"])
    label = f"S{index}"

    prompt_rel = row.get("prompt_file") or f"{PROMPTS_REL}/{shot_id}.txt"
    prompt_path = project_dir / str(prompt_rel)
    prompt = _read_text(prompt_path) if prompt_path.is_file() else None

    adherence, adherence_path = _review_for(project_dir, project_dir / VIDEO_REL / f"{shot_id}.mp4")
    take_paths = _take_files(project_dir, shot_id)
    accepted_path = _accepted_take_path(project_dir, shot_id, adherence, take_paths)

    superseded = adherence.get("superseded") if isinstance(adherence, dict) else None
    superseded = superseded if isinstance(superseded, dict) else {}

    # Billed attempts map onto the takes on disk in order: a filter refusal is
    # free and writes no file, so only billed ones can have produced a take.
    shot_attempts = attempts_by_shot.get(shot_id, [])
    billed = [a for a in shot_attempts if _is_billed(a)]

    takes = []
    for take_index, f in enumerate(take_paths):
        take_rel = _rel(project_dir, f)
        accepted = accepted_path is not None and take_rel == accepted_path
        size, mtime = _stat(f)
        cost = None
        if take_index < len(billed):
            cost = _number(billed[take_index].get("usd"))
        if cost is None and accepted and isinstance(adherence, dict):
            cost = _number((adherence.get("actual") or {}).get("cost_usd"))
        # The shot's adherence file judges the take it names. A superseded take
        # only carries a verdict if the run wrote one beside it under its own
        # name; otherwise its story is the `superseded` note.
        if accepted:
            review, review_path = adherence, adherence_path
        else:
            review, review_path = _review_for(project_dir, f)
        takes.append({
            "label": f"{label}-{take_letters(take_index)}",
            "path": take_rel,
            "name": f.name,
            "attempt": _attempt_number(f, shot_id),
            "accepted": accepted,
            "cost_usd": cost,
            "review": review,
            "review_path": review_path,
            "verdict": _verdict_of(review),
            "note": superseded.get(f.name),
            "sheets": _sheets_for(project_dir, f.stem),
            "size_bytes": size,
            "mtime": mtime,
        })

    accepted_label = next((t["label"] for t in takes if t["accepted"]), None)
    reference_ids = [str(r) for r in (row.get("reference_ids") or [])]
    reference_labels = []
    by_path = {item["path"]: item for item in references}
    for ref in reference_ids:
        item = by_path.get(ref)
        if item is not None:
            reference_labels.append(item["label"])
            if label not in item["used_by"]:
                item["used_by"].append(label)

    frame_contains = [c for c in (row.get("frame_contains") or []) if isinstance(c, str)]

    return {
        "label": label,
        "id": shot_id,
        "title": row.get("title"),
        "brief_timecode": row.get("brief_timecode"),
        "seconds_generated": _number(row.get("seconds_generated")) or _number(row.get("seconds")),
        "seconds_delivered": _number(row.get("seconds_delivered")),
        "trim": row.get("trim") if isinstance(row.get("trim"), dict) else None,
        "lens": row.get("lens"),
        "camera": row.get("camera"),
        "action": row.get("action"),
        "carry_over": row.get("carry_over"),
        "risk": row.get("risk"),
        "frame_contains": frame_contains,
        "reference_ids": reference_ids,
        "reference_labels": reference_labels,
        "prompt_path": _rel(project_dir, prompt_path) if prompt_path.is_file() else None,
        "prompt": prompt,
        "takes": takes,
        "accepted_take": accepted_label,
        "verdict": _verdict_of(adherence),
        "review_path": adherence_path,
        "cost_usd": round(sum(_number(a.get("usd")) or 0.0 for a in shot_attempts), 4)
        if shot_attempts else None,
        "attempt_count": len(shot_attempts) or None,
        "billed_count": len(billed) or None,
    }


# ---------------------------------------------------------------------------
# final cut
# ---------------------------------------------------------------------------

def _final_cut(project_dir: Path) -> Optional[dict]:
    final_dir = project_dir / FINAL_REL
    report = _read_json_dict(final_dir / "cut_report.json")
    renders = project_dir / "renders"
    deliverable = None
    if renders.is_dir():
        preferred = renders / f"{project_dir.name}.mp4"
        if preferred.is_file():
            deliverable = preferred
        else:
            try:
                candidates = [
                    f for f in sorted(renders.iterdir())
                    if f.is_file() and f.suffix.lower() in MEDIA_VIDEO_EXT
                ]
            except OSError:
                candidates = []
            if candidates:
                deliverable = max(candidates, key=lambda f: _stat(f)[1] or 0.0)
    if report is None and deliverable is None:
        return None

    sheets = []
    if final_dir.is_dir():
        try:
            sheets = [
                _rel(project_dir, f) for f in sorted(final_dir.iterdir())
                if f.is_file() and f.suffix.lower() in MEDIA_IMAGE_EXT
            ]
        except OSError:
            sheets = []

    review = _read_json_dict(final_dir / "cut_review.json")
    edit = _read_json_dict(project_dir / "artifacts" / "edit.json")
    size, mtime = _stat(deliverable) if deliverable is not None else (None, None)
    return {
        "label": "CUT",
        "path": _rel(project_dir, deliverable) if deliverable is not None else None,
        "size_bytes": size,
        "mtime": mtime,
        "report": report,
        "report_path": f"{FINAL_REL}/cut_report.json" if report is not None else None,
        "review": review,
        "review_path": f"{FINAL_REL}/cut_review.json" if review is not None else None,
        "edit": edit,
        "edit_path": "artifacts/edit.json" if edit is not None else None,
        "sheets": sheets,
    }


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def is_shot_project(project_dir: Path) -> bool:
    """True when this project was produced by the open-montage skill."""
    return (Path(project_dir) / CONTRACT_REL).is_file()


def load_shot_board(project_dir: Path) -> Optional[dict[str, Any]]:
    """The open-montage view of a project, or None when it isn't one.

    Never raises: any unreadable file degrades that part of the board.
    """
    project_dir = Path(project_dir)
    contract = _read_json_dict(project_dir / CONTRACT_REL)
    if contract is None:
        return None

    rows = _shot_rows(contract)
    shot_ids = [str(r["id"]) for r in rows]

    attempts, total_usd, spend_source = _spend_attempts(project_dir, contract)
    attempts_by_shot: dict[str, list[dict]] = {}
    unattributed = []
    for attempt in attempts:
        shot_id = _attempt_shot_id(attempt, shot_ids)
        if shot_id is None:
            unattributed.append(attempt)
        else:
            attempts_by_shot.setdefault(shot_id, []).append(attempt)

    references = _reference_items(project_dir)
    shots = [
        _build_shot(project_dir, index, row, attempts_by_shot, references)
        for index, row in enumerate(rows, start=1)
    ]
    cut = _final_cut(project_dir)

    mtimes = [
        value for value in (
            [item["mtime"] for item in references]
            + [take["mtime"] for shot in shots for take in shot["takes"]]
            + [cut["mtime"] if cut else None]
        ) if value
    ]

    budget = _number(contract.get("budget_usd"))
    return {
        "label_scheme": "BRIEF · R1..Rn · S1..Sn · S1-A.. · CUT",
        "contract_path": CONTRACT_REL,
        "brief": _brief_card(project_dir, contract),
        "references": _reference_set(project_dir, contract, references),
        "shots": shots,
        "cut": cut,
        "spend": {
            "total_usd": total_usd,
            "budget_usd": budget,
            "remaining_usd": round(budget - total_usd, 4)
            if budget is not None and total_usd is not None else None,
            "source": spend_source,
            "attempts": attempts,
            "unattributed": unattributed,
        },
        "last_activity": max(mtimes) if mtimes else None,
    }
