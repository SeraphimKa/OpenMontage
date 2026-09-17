"""Free checks on an open-montage project's shot prompts, before a paid call.

A clean reviewer is still required. This only removes the defects a machine can
find for nothing, each of which cost a review round or a wasted generation in
the restless-night run of 2026-09-17:

  * a prompt missing a section of the seedance-2-5 grammar, or its LENS LOCK
  * a person shot with no IDENTITY / NO-IP LOCK (the output likeness filter)
  * a person shot sent with references but no REFERENCE USE section (the plates
    showed an empty bed, so the man materialised 2.2 s into the shot)
  * CARRY-OVER blocks that are not byte-identical from shot to shot
  * a focal length in the prompt that is not the one in the contract
  * timed beats that overrun the clip, or sit wholly inside a trimmed-off tail
  * an exclusive POSITIVE LOCK ("and those three things only") that silently
    deletes elements the same shot requires

    make prompt-lint PROJECT=restless-night

It also answers the question that four review rounds were spent on, namely
whether a stated lens at a stated distance can hold what the frame must show:

    python scripts/om_prompt_lint.py frame --fov 47 --distance 2.35
    python scripts/om_prompt_lint.py frame --fov 63 --need-height 1.5
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

REQUIRED_SECTIONS = (
    "GLOBAL STYLE", "SCENE", "CHARACTERS", "LOCATION", "FIRST FRAME AND BLOCKING",
    "OPTICS / CAMERA", "LIGHTING", "AUDIO", "POSITIVE LOCKS",
)
HEADER = re.compile(r"^[A-Z][A-Z /-]+$")
BEAT = re.compile(r"^(\d+(?:\.\d+)?) to (\d+(?:\.\d+)?) s\b", re.M)
DEGREES = re.compile(r"(\d+(?:\.\d+)?)\s*degrees?\b")
EXCLUSIVE = re.compile(r"\b(?:things?|elements?|items?) only\b|\band nothing else\b|\bnothing but\b", re.I)
FIGURATIVE = re.compile(r"\b(?:as if|as though|like an?)\b", re.I)


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warning"
    shot: str
    code: str
    message: str


def frame_size(fov_diagonal_deg: float, distance_m: float, aspect: tuple[int, int] = (16, 9)) -> tuple[float, float]:
    """Width and height in metres that a lens holds at a distance."""
    diagonal = 2 * distance_m * math.tan(math.radians(fov_diagonal_deg) / 2)
    hyp = math.hypot(*aspect)
    return diagonal * aspect[0] / hyp, diagonal * aspect[1] / hyp


def distance_for_height(fov_diagonal_deg: float, height_m: float, aspect: tuple[int, int] = (16, 9)) -> float:
    """How far back a lens must be for the frame to be this tall."""
    return height_m / frame_size(fov_diagonal_deg, 1.0, aspect)[1]


def sections(prompt: str) -> dict[str, str]:
    """Split a prompt into its labelled sections. 'Shot 1:' lines are body, not headers."""
    found: dict[str, str] = {}
    current: str | None = None
    for line in prompt.splitlines():
        if HEADER.match(line.strip()) and line == line.strip():
            current = line.strip()
            found[current] = ""
        elif current is not None:
            found[current] += line + "\n"
    return {name: body.strip() for name, body in found.items()}


def has_person(parts: dict[str, str]) -> bool:
    opening = parts.get("CHARACTERS", "").lstrip().lower()
    return bool(opening) and not opening.startswith(("nobody", "none", "no one"))


def trim_end(row: dict[str, Any]) -> float | None:
    keep = (row.get("trim") or {}).get("keep_seconds")
    if isinstance(keep, str) and "-" in keep:
        try:
            return float(keep.rsplit("-", 1)[1])
        except ValueError:
            return None
    return None


def lint_shot(shot_id: str, prompt: str, row: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    parts = sections(prompt)

    for name in REQUIRED_SECTIONS:
        if name not in parts:
            out.append(Finding("error", shot_id, "missing-section", f"no {name} section"))
    if "LENS LOCK" not in prompt:
        out.append(Finding("error", shot_id, "no-lens-lock", "no LENS LOCK: the documented cause of lens drift mid-shot"))

    if has_person(parts):
        if "IDENTITY / NO-IP LOCK" not in parts:
            out.append(Finding("error", shot_id, "no-identity-lock",
                               "a person is described but there is no IDENTITY / NO-IP LOCK section; "
                               "a detailed face converges on a recognisable actor and the output is discarded"))
        if row.get("reference_ids") and "REFERENCE USE" not in parts:
            out.append(Finding("error", shot_id, "no-reference-use",
                               "references are attached but there is no REFERENCE USE section saying what they "
                               "show and what state the person is in from the first frame; plates of an empty "
                               "room make the person materialise mid-shot"))

    contract_lens = DEGREES.search(str(row.get("lens", "")))
    if contract_lens:
        degrees = contract_lens.group(1)
        stated = {m.group(1) for m in DEGREES.finditer(parts.get("OPTICS / CAMERA", ""))}
        if stated and degrees not in stated:
            out.append(Finding("error", shot_id, "lens-mismatch",
                               f"contract lens is {degrees} degrees, OPTICS / CAMERA states {', '.join(sorted(stated))}"))

    seconds = row.get("seconds_generated", row.get("seconds"))
    beats = [(float(a), float(b)) for a, b in BEAT.findall(prompt)]
    if beats and isinstance(seconds, (int, float)):
        if beats[0][0] != 0.0:
            out.append(Finding("error", shot_id, "beats-start-late", f"the first timed beat starts at {beats[0][0]} s, not 0.0"))
        if beats[-1][1] > seconds + 1e-6:
            out.append(Finding("error", shot_id, "beats-overrun", f"timed beats run to {beats[-1][1]} s but the clip is {seconds} s"))
        for (_, end), (start, _) in zip(beats, beats[1:]):
            if abs(end - start) > 1e-6:
                out.append(Finding("warning", shot_id, "beat-gap", f"timed beats are not contiguous at {end} s / {start} s"))
        cut = trim_end(row)
        if cut is not None:
            for start, end in beats:
                if start >= cut - 1e-6:
                    out.append(Finding("warning", shot_id, "beat-in-trimmed-tail",
                                       f"the beat {start}-{end} s lies wholly after the {cut} s trim point: any "
                                       "brief-required action in it is cut from the delivered shot"))
    elif not beats and trim_end(row) is not None:
        out.append(Finding("warning", shot_id, "untimed-trimmed-shot",
                           "this shot is trimmed but has no time-coded beats, so the action may spread across "
                           "the whole clip and lose its ending to the trim"))

    for match in EXCLUSIVE.finditer(parts.get("POSITIVE LOCKS", "")):
        out.append(Finding("warning", shot_id, "exclusive-lock",
                           f"POSITIVE LOCKS says '{match.group(0)}': an exclusive list deletes every required "
                           "element it does not name. List what must be in frame instead"))
    for match in FIGURATIVE.finditer(prompt):
        out.append(Finding("warning", shot_id, "figurative",
                           f"'{match.group(0)}' reads as a comparison; the model renders comparisons as objects"))
    return out


def lint_cross_shot(prompts: dict[str, str]) -> list[Finding]:
    out: list[Finding] = []
    carry = {shot: sections(text).get("CARRY-OVER") for shot, text in prompts.items()}
    carry = {shot: block for shot, block in carry.items() if block}
    if len(set(carry.values())) > 1:
        out.append(Finding("error", ", ".join(sorted(carry)), "carry-over-differs",
                           "CARRY-OVER must be restated verbatim, same words in the same order, in every shot that has one"))

    people = {shot: " ".join(sections(text)["CHARACTERS"].split())
              for shot, text in prompts.items() if has_person(sections(text))}
    blocks = sorted(people.values(), key=len)
    if blocks and any(not block.startswith(blocks[0]) for block in blocks):
        out.append(Finding("warning", ", ".join(sorted(people)), "characters-differ",
                           "the CHARACTERS description is not the same text in every person shot; whatever differs "
                           "is re-invented by the model in each generation"))
    return out


def lint_project(project_dir: Path) -> list[Finding]:
    contract = json.loads((project_dir / "artifacts" / "shot_contract.json").read_text())
    prompts: dict[str, str] = {}
    findings: list[Finding] = []
    for row in contract.get("shots", []):
        shot_id = row["id"]
        path = project_dir / row.get("prompt_file", f"artifacts/prompts/{shot_id}.txt")
        if not path.is_file():
            findings.append(Finding("error", shot_id, "no-prompt", f"no prompt at {path}"))
            continue
        prompts[shot_id] = path.read_text()
        findings += lint_shot(shot_id, prompts[shot_id], row)
    return findings + lint_cross_shot(prompts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    lint = sub.add_parser("lint", help="check every shot prompt of a project")
    lint.add_argument("project")
    frame = sub.add_parser("frame", help="what a lens holds at a distance, or the distance it needs")
    frame.add_argument("--fov", type=float, required=True, help="diagonal field of view in degrees")
    frame.add_argument("--distance", type=float, help="metres from lens to subject")
    frame.add_argument("--need-height", type=float, help="metres the frame must hold top to bottom")
    frame.add_argument("--aspect", default="16:9")
    args = parser.parse_args(argv)

    if args.command == "frame":
        w, h = (int(part) for part in args.aspect.split(":"))
        if args.distance is not None:
            width, height = frame_size(args.fov, args.distance, (w, h))
            print(f"{args.fov:g} degrees at {args.distance:g} m holds {width:.2f} m wide x {height:.2f} m tall ({args.aspect})")
        if args.need_height is not None:
            d = distance_for_height(args.fov, args.need_height, (w, h))
            print(f"{args.fov:g} degrees needs {d:.2f} m to hold {args.need_height:g} m top to bottom ({args.aspect})")
        if args.distance is None and args.need_height is None:
            parser.error("give --distance, --need-height, or both")
        return 0

    project_dir = ROOT / "projects" / args.project
    findings = lint_project(project_dir)
    for f in findings:
        print(f"{f.level.upper():8} {f.shot:10} {f.code:22} {f.message}")
    errors = sum(f.level == "error" for f in findings)
    warnings = len(findings) - errors
    print(f"\n{errors} error(s), {warnings} warning(s). "
          + ("Fix the errors before any paid call." if errors else "A clean reviewer is still required."))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
