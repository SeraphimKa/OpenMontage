"""Assemble an open-montage project's accepted shots into the deliverable.

Shots are generated one per call, so the cuts here are real splices rather than
something the model was asked to perform. This script reads the shot contract
and does the four things every assembly needs:

  * trims each shot to the brief's length (the provider cannot generate under
    4 s, so short shots are generated long and cut here)
  * applies one named grade to every shot, so the sequence stays matched
  * joins the shots in contract order with hard cuts
  * fades the sound 60 ms down and up across each cut, because independently
    generated room tones pop at a splice, while keeping it in sync with the picture

    make assemble PROJECT=restless-night
    make assemble PROJECT=restless-night GRADE=none
    make cut-sheet PROJECT=restless-night

Assembly also writes artifacts/final/cut_report.json (shot order, where each
cut falls, the runtime against the contract's `runtime_seconds`). `cut-sheet`
adds what a clean reviewer of the whole cut needs: a 1 fps sheet of the
deliverable and, for every cut, the last frame before it beside the first frame
after it, which is where a jump in location, wardrobe or light shows.

Editing instructions live in artifacts/edit.json, which the agent writes from
the user's words and the user can read. Without the file the cut is the
contract: every shot once, in order, hard cuts. With it:

    {"sequence": [{"shot": "shot_01"},
                  {"shot": "shot_03", "keep_seconds": "0.5-2.5"},
                  {"shot": "shot_02", "transition": {"type": "dissolve", "seconds": 0.5}}],
     "grade": "amber-blue-night",
     "clip_audio": "keep",                      (or "mute")
     "music": {"file": "assets/music/bed.mp3", "volume_db": -18, "fade_out_seconds": 2},
     "titles": [{"text": "Restless", "start": 0.5, "end": 2.5, "position": "bottom", "size": 36}],
     "fade_in_seconds": 0.5, "fade_out_seconds": 1.0,
     "runtime_seconds": 14.5}

`sequence` may reorder, drop or repeat shots. `transition` describes how a shot
is entered; the default is a cut. A dissolve overlaps the two shots, so it
shortens the runtime by its length. Every key is optional.

Trim comes from the row's `trim.keep_seconds` ("0.0-3.0"), else its
`seconds_delivered`, else the whole clip. The grade comes from --grade, else
the contract's `grade_preset`, else none.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

# Validated by eye against real frames before being named here. A stronger
# version of amber-blue-night (bb=1.14, bs=0.16) turned skin cold and read as
# stylised, which breaks a photoreal brief.
GRADES = {
    "none": None,
    "amber-blue-night": (
        "colorchannelmixer=rr=1.00:gg=1.00:bb=1.06,"
        "colorbalance=rs=-0.04:bs=0.08:rm=-0.02:bm=0.04:rh=0.01:bh=-0.01,"
        "eq=saturation=1.06:contrast=1.04"
    ),
}
AUDIO_EDGE_FADE_S = 0.06
ENCODE = ["-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p"]


class EditError(ValueError):
    """The editing instructions cannot be carried out; the message says which one."""


TITLE_Y = {"top": "h*0.08", "center": "(h-text_h)/2", "bottom": "h*0.92-text_h"}


def load_edit(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "artifacts" / "edit.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def edit_plan(contract: dict[str, Any], edit: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per clip in the cut: the contract row, its trim, how it is entered."""
    rows = {row["id"]: row for row in contract.get("shots", [])}
    sequence = edit.get("sequence") or [{"shot": shot_id} for shot_id in rows]
    plan = []
    for number, item in enumerate(sequence, start=1):
        shot_id = item.get("shot")
        if shot_id not in rows:
            raise EditError(f"sequence item {number} names {shot_id!r}; the contract has: {', '.join(rows)}")
        row = dict(rows[shot_id])
        if item.get("keep_seconds"):
            row["trim"] = {"keep_seconds": item["keep_seconds"]}
        transition = item.get("transition") or {"type": "cut"}
        if isinstance(transition, str):
            transition = {"type": transition}
        kind = transition.get("type", "cut")
        if kind not in ("cut", "dissolve"):
            raise EditError(f"sequence item {number}: transition {kind!r} is not one of cut, dissolve")
        seconds = float(transition.get("seconds", 0.5)) if kind == "dissolve" else 0.0
        if number == 1:
            kind, seconds = "cut", 0.0  # the first shot has nothing to dissolve from
        plan.append({"shot": shot_id, "row": row, "transition": kind, "transition_seconds": seconds})
    if not plan:
        raise EditError("the cut has no shots")
    return plan


def join_filter(lengths: list[float], plan: list[dict[str, Any]], with_audio: bool, fps: float) -> tuple[list[str], float]:
    """Filter chains joining the parts with each entry's transition, and the picture length.

    Ends on the labels [vjoin] and, with audio, [ajoin].
    """
    chains = [f"[{i}:v]fps={fps:g},settb=AVTB,format=yuv420p[v{i}]" for i in range(len(lengths))]
    if with_audio:
        # At a hard cut each side gets a short fade and the sound is joined end to end, so it
        # stays exactly as long as the picture. A crossfade there would shorten it and drift.
        for i, length in enumerate(lengths):
            fades = []
            if i > 0 and plan[i]["transition"] == "cut":
                fades.append(f"afade=t=in:st=0:d={AUDIO_EDGE_FADE_S:g}")
            if i < len(lengths) - 1 and plan[i + 1]["transition"] == "cut":
                fades.append(f"afade=t=out:st={length - AUDIO_EDGE_FADE_S:.3f}:d={AUDIO_EDGE_FADE_S:g}")
            chains.append(f"[{i}:a]{','.join(fades) or 'anull'}[a{i}]")
    video, audio, clock = "[v0]", "[a0]", lengths[0]
    for i in range(1, len(lengths)):
        entry, last = plan[i], i == len(lengths) - 1
        v_out, a_out = ("[vjoin]" if last else f"[vj{i}]"), ("[ajoin]" if last else f"[aj{i}]")
        if entry["transition"] == "dissolve":
            d = entry["transition_seconds"]
            join_audio = f"acrossfade=d={d:g}:c1=tri:c2=tri"
            if d >= min(lengths[i - 1], lengths[i]):
                raise EditError(f"the {d:g} s dissolve into {entry['shot']} is as long as a shot it joins")
            chains.append(f"{video}[v{i}]xfade=transition=fade:duration={d:g}:offset={clock - d:.3f}{v_out}")
            clock += lengths[i] - d
        else:
            join_audio = "concat=n=2:v=0:a=1"
            chains.append(f"{video}[v{i}]concat=n=2:v=1:a=0{v_out}")
            clock += lengths[i]
        if with_audio:
            chains.append(f"{audio}[a{i}]{join_audio}{a_out}")
        video, audio = v_out, a_out
    if len(lengths) == 1:
        chains.append("[v0]null[vjoin]")
        if with_audio:
            chains.append("[a0]anull[ajoin]")
    return chains, clock


def finish_filter(edit: dict[str, Any], runtime: float, has_clip_audio: bool, music_input: int | None,
                  title_files: list[Path]) -> tuple[list[str], bool]:
    """Titles and fades on the picture; music and fades on the sound. Ends on [v] and [a]."""
    steps = []
    for title, text_file in zip(edit.get("titles") or [], title_files):
        position = title.get("position", "bottom")
        if position not in TITLE_Y:
            raise EditError(f"title position {position!r} is not one of {', '.join(TITLE_Y)}")
        start, end = float(title.get("start", 0)), float(title.get("end", runtime))
        if not 0 <= start < end <= runtime + 1e-6:
            raise EditError(f"title {title.get('text')!r} runs {start:g}-{end:g} s but the cut is {runtime:.2f} s")
        steps.append(f"drawtext=textfile='{text_file}':expansion=none:font=Sans:fontsize={int(title.get('size', 36))}"
                     f":fontcolor=white:borderw=2:bordercolor=black@0.6:x=(w-text_w)/2:y={TITLE_Y[position]}"
                     f":enable='between(t,{start:g},{end:g})'")
    fade_in, fade_out = float(edit.get("fade_in_seconds", 0)), float(edit.get("fade_out_seconds", 0))
    if fade_in:
        steps.append(f"fade=t=in:st=0:d={fade_in:g}")
    if fade_out:
        steps.append(f"fade=t=out:st={runtime - fade_out:.3f}:d={fade_out:g}")
    chains = [f"[vjoin]{','.join(steps) or 'null'}[v]"]

    keep_clip_audio = has_clip_audio and edit.get("clip_audio", "keep") != "mute"
    sources = ["[ajoin]"] if keep_clip_audio else []
    if music_input is not None:
        music = edit["music"]
        tail = float(music.get("fade_out_seconds", 0))
        bed = f"[{music_input}:a]atrim=0:{runtime:.3f},asetpts=PTS-STARTPTS,volume={float(music.get('volume_db', -18)):g}dB"
        if tail:
            bed += f",afade=t=out:st={runtime - tail:.3f}:d={tail:g}"
        chains.append(bed + "[music]")
        sources.append("[music]")
    if not sources:
        return chains, False
    mix = sources[0] if len(sources) == 1 else None
    if mix is None:
        chains.append(f"{''.join(sources)}amix=inputs=2:duration=first:normalize=0[amix]")
        mix = "[amix]"
    ends = []
    if fade_in:
        ends.append(f"afade=t=in:st=0:d={fade_in:g}")
    if fade_out:
        ends.append(f"afade=t=out:st={runtime - fade_out:.3f}:d={fade_out:g}")
    chains.append(f"{mix}{','.join(ends) or 'anull'}[a]")
    return chains, True


def trim_window(row: dict[str, Any]) -> tuple[float, float | None]:
    """(start, duration) to keep from a generated clip; duration None keeps the rest."""
    keep = (row.get("trim") or {}).get("keep_seconds")
    if isinstance(keep, str) and "-" in keep:
        start, end = (float(part) for part in keep.split("-", 1))
        if end <= start:
            raise ValueError(f"{row.get('id')}: trim {keep!r} keeps nothing")
        return start, end - start
    delivered = row.get("seconds_delivered")
    if isinstance(delivered, (int, float)) and not isinstance(delivered, bool):
        return 0.0, float(delivered)
    return 0.0, None


def has_audio(clip: Path) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(clip)],
        check=True, capture_output=True, text=True)
    return bool(probe.stdout.strip())


def probe_seconds(clip: Path) -> float:
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        check=True, capture_output=True, text=True).stdout.strip())


def cut_report(plan: list[dict[str, Any]], lengths: list[float], total: float,
               expected: float | None, grade: str) -> dict[str, Any]:
    """Order, cut points and runtime: the facts a whole-cut review starts from."""
    shots, clock = [], 0.0
    for entry, length in zip(plan, lengths):
        clock -= entry["transition_seconds"]  # a dissolve overlaps the shot before it
        shots.append({"id": entry["shot"], "starts_at": round(clock, 3), "ends_at": round(clock + length, 3),
                      "entered_by": entry["transition"], "transition_seconds": entry["transition_seconds"]})
        clock += length
    report: dict[str, Any] = {
        "shots": shots,
        "cuts_at": [shot["starts_at"] for shot in shots[1:]],
        "runtime_seconds": round(total, 3),
        "grade": grade,
    }
    if expected is not None:
        report["runtime_expected"] = expected
        report["runtime_ok"] = abs(total - expected) <= 0.1
    return report


def probe_fps(clip: Path) -> float:
    rate = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(clip)],
        check=True, capture_output=True, text=True).stdout.strip()
    top, _, bottom = rate.partition("/")
    return float(top) / float(bottom or 1)


def make_cut_sheets(project_dir: Path) -> int:
    final_dir = project_dir / "artifacts" / "final"
    report_path = final_dir / "cut_report.json"
    final = project_dir / "renders" / f"{project_dir.name}.mp4"
    if not report_path.is_file() or not final.is_file():
        print("no assembled cut yet: run `make assemble` first")
        return 1
    report = json.loads(report_path.read_text())
    seconds = max(1, round(report["runtime_seconds"]))
    columns = min(seconds, 8)
    sheet = final_dir / "cut_sheet.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-vf",
                    f"fps=1,scale=480:-2,tile={columns}x{-(-seconds // columns)}", str(sheet)], check=True)
    print(f"wrote {sheet}")
    for number, shot in enumerate(report["shots"][1:], start=1):
        at, after = shot["starts_at"], shot["starts_at"] + shot.get("transition_seconds", 0.0)
        pair = final_dir / f"cut_{number:02d}_pair.png"
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-ss", f"{max(0.0, at - 0.1):.3f}", "-i", str(final),
                        "-ss", f"{after + 0.05:.3f}", "-i", str(final),
                        "-filter_complex", "[0:v][1:v]hstack=inputs=2", "-frames:v", "1", str(pair)], check=True)
        print(f"wrote {pair}   (before | after the cut at {at:g} s)")
    print("\nThe reviewer also watches the deliverable in real time, with sound.")
    return 0


def assemble(project_dir: Path, grade_name: str | None) -> int:
    contract = json.loads((project_dir / "artifacts" / "shot_contract.json").read_text())
    edit = load_edit(project_dir)
    grade_name = grade_name or edit.get("grade") or contract.get("grade_preset") or "none"
    if grade_name not in GRADES:
        print(f"unknown grade {grade_name!r}; choose from: {', '.join(GRADES)}")
        return 1
    grade = GRADES[grade_name]

    work = project_dir / "assets" / "video" / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        plan = edit_plan(contract, edit)
        music_file = None
        if edit.get("music"):
            music_file = project_dir / edit["music"].get("file", "")
            if not music_file.is_file():
                raise EditError(f"music file not found: {music_file}")
    except EditError as exc:
        print(f"EDIT       {exc}")
        return 1

    parts: list[Path] = []
    for number, entry in enumerate(plan, start=1):
        row = entry["row"]
        clip = project_dir / "assets" / "video" / f"{row['id']}.mp4"
        if not clip.is_file():
            print(f"missing {clip}: every shot in the cut must be accepted before assembly")
            return 1
        start, duration = trim_window(row)
        part = work / f"{number:02d}_{row['id']}.part.mp4"
        command = ["ffmpeg", "-y", "-v", "error", "-ss", f"{start}"]
        if duration is not None:
            command += ["-t", f"{duration}"]
        command += ["-i", str(clip)]
        if grade:
            command += ["-vf", grade]
        command += [*ENCODE, "-c:a", "aac", "-b:a", "192k", str(part)]
        subprocess.run(command, check=True)
        kept = "to the end" if duration is None else f"{duration:g} s"
        entered = "" if number == 1 else (f", {entry['transition_seconds']:g} s dissolve in"
                                          if entry["transition"] == "dissolve" else ", cut in")
        print(f"{row['id']:10} from {start:g} s, {kept}{entered}")
        parts.append(part)

    lengths = [probe_seconds(part) for part in parts]
    with_audio = all(has_audio(part) for part in parts)
    if not with_audio:
        print("at least one shot has no audio: clip sound is dropped")
    fps = probe_fps(parts[0])
    titles = edit.get("titles") or []
    title_files = []
    for number, title in enumerate(titles, start=1):
        text_file = work / f"title_{number:02d}.txt"  # a file, so no character needs escaping
        text_file.write_text(str(title.get("text", "")))
        title_files.append(text_file)
    try:
        chains, runtime = join_filter(lengths, plan, with_audio, fps)
        finish, has_sound = finish_filter(edit, runtime, with_audio, len(parts) if music_file else None, title_files)
    except EditError as exc:
        print(f"EDIT       {exc}")
        return 1

    renders = project_dir / "renders"
    renders.mkdir(exist_ok=True)
    final = renders / f"{project_dir.name}.mp4"
    command = ["ffmpeg", "-y", "-v", "error"]
    for part in parts:
        command += ["-i", str(part)]
    if music_file:
        command += ["-stream_loop", "-1", "-i", str(music_file)]
    command += ["-filter_complex", ";".join(chains + finish), "-map", "[v]"]
    if has_sound:
        command += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    command += [*ENCODE, "-t", f"{runtime:.3f}", str(final)]
    subprocess.run(command, check=True)

    length = probe_seconds(final)
    expected = edit.get("runtime_seconds", contract.get("runtime_seconds"))
    report = cut_report(plan, lengths, length, expected, grade_name)
    report["edited_from"] = "artifacts/edit.json" if edit else "the shot contract"
    final_dir = project_dir / "artifacts" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "cut_report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"\ngrade      {grade_name}")
    if music_file:
        print(f"music      {music_file.name}")
    for title in titles:
        print(f"title      {title.get('text')!r}")
    print(f"delivered  {final}  ({length:.2f} s, {len(parts)} shots, edited from {report['edited_from']})")
    if report.get("runtime_ok") is False:
        print(f"RUNTIME    {report['runtime_expected']:g} s was asked for: fix the trims, or set runtime_seconds in edit.json")
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project")
    parser.add_argument("--grade", choices=sorted(GRADES), help="overrides the contract's grade_preset")
    parser.add_argument("--sheet", action="store_true", help="review sheets for the assembled cut")
    args = parser.parse_args(argv)
    project_dir = ROOT / "projects" / args.project
    if not project_dir.is_dir():
        print(f"no project at {project_dir}")
        return 1
    if args.sheet:
        return make_cut_sheets(project_dir)
    return assemble(project_dir, args.grade)


if __name__ == "__main__":
    raise SystemExit(main())
