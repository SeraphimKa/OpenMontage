"""Run one contracted shot of an open-montage project through seedance_ark.

The open-montage skill decides WHAT to generate: the shot contract, the prompt
and the references. This script only executes that decision safely and keeps
the books, so nobody needs a throwaway runner in /tmp again:

  * everything comes from projects/<id>/artifacts/shot_contract.json and
    artifacts/prompts/<shot>.txt, nothing is typed on the command line
  * the plan and the cost are printed before anything is spent
  * the payload is validated locally first, so a bad reference fails for free
  * a provider filter refusal is recognised, retried within the skill's limit,
    and never mistaken for a prompt defect
  * every attempt, billed or free, lands in artifacts/spend_log.json
  * the contract's `budget_usd` and `max_attempts_per_shot` (default 3 billed
    takes) are enforced before spending: at either limit the script stops, and
    raising the limit in the contract is the user's decision
  * a finished clip's video_url is logged with its 24 h expiry, because a
    "change only X" edit needs that URL and a saved mp4 cannot be sent back
  * a previous take is kept as <shot>.attemptN.mp4, never overwritten

    make shot      PROJECT=restless-night SHOT=shot_02     # plan only, free
    make shot-go   PROJECT=restless-night SHOT=shot_02     # generate
    make shot-sheet PROJECT=restless-night SHOT=shot_02    # contact sheets

Run it through make, or with the repo's .venv python: the Ark tool imports PIL
inside the try block that reports "image is unreadable or corrupt", so under an
interpreter without Pillow a missing module looks like a broken image.

Exit codes: 0 done, 1 bad contract or payload, 3 likeness filter exhausted its
retries (stop and ask the user), 4 a reference was refused as a real person
(choose a person_route), 5 any other provider failure, 6 the budget or the
attempt cap is reached (stop and ask the user).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TEAM_ROUTE = {
    "tool": "seedance_ark",
    "model_variant": "2.5",
    "resolution": "480p",
    "aspect_ratio": "16:9",
    "operation": "reference_to_video",
    "custom_price_cny_per_million_tokens": 77.0,
}

INPUT_PERSON_FILTER = "input_person_filter"
OUTPUT_LIKENESS_FILTER = "output_likeness_filter"
OTHER_FAILURE = "other"

EXIT_BAD_INPUT = 1
EXIT_LIKENESS_EXHAUSTED = 3
EXIT_PERSON_REFUSED = 4
EXIT_PROVIDER_FAILURE = 5
EXIT_LIMIT_REACHED = 6

DEFAULT_MAX_ATTEMPTS = 3
OUTPUT_URL_LIFETIME = timedelta(hours=24)


class ContractError(ValueError):
    """The shot contract cannot be turned into a tool call."""


def classify_error(message: str | None) -> str:
    """Name the provider refusal, so a filter is never read as a prompt defect."""
    text = message or ""
    if "InputImageSensitiveContentDetected" in text or "InputVideoSensitiveContentDetected" in text:
        return INPUT_PERSON_FILTER
    if "OutputVideoSensitiveContentDetected" in text:
        return OUTPUT_LIKENESS_FILTER
    return OTHER_FAILURE


def split_references(reference_ids: list[str], project_dir: Path) -> tuple[list[str], list[str]]:
    """Map the contract's reference_ids column to the tool's two inputs.

    asset:// IDs and https:// output URLs go in reference_image_urls; files go
    in reference_image_paths, resolved against the project directory.
    """
    paths: list[str] = []
    urls: list[str] = []
    for ref in reference_ids:
        ref = str(ref).strip()
        if ref.upper().startswith("PENDING"):
            raise ContractError(f"reference is still pending: {ref}")
        if ref.startswith(("asset://", "https://", "http://")):
            urls.append(ref)
            continue
        path = Path(ref)
        if not path.is_absolute():
            path = project_dir / path
        if not path.is_file():
            raise ContractError(f"reference file not found: {path}")
        paths.append(str(path))
    return paths, urls


def load_contract(project_dir: Path) -> dict[str, Any]:
    contract_path = project_dir / "artifacts" / "shot_contract.json"
    if not contract_path.is_file():
        raise ContractError(f"no shot contract at {contract_path}")
    return json.loads(contract_path.read_text())


def shot_row(contract: dict[str, Any], shot_id: str) -> dict[str, Any]:
    for row in contract.get("shots", []):
        if row.get("id") == shot_id:
            return row
    known = ", ".join(row.get("id", "?") for row in contract.get("shots", []))
    raise ContractError(f"{shot_id} is not in the contract (it has: {known})")


def build_inputs(contract: dict[str, Any], shot_id: str, project_dir: Path) -> dict[str, Any]:
    """Turn one contract row into seedance_ark inputs."""
    row = shot_row(contract, shot_id)
    route = {**TEAM_ROUTE, **(contract.get("route") or {})}
    if route["tool"] != "seedance_ark":
        raise ContractError(f"this runner drives seedance_ark, the contract names {route['tool']}")

    seconds = row.get("seconds_generated", row.get("seconds"))
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 4:
        raise ContractError(
            f"{shot_id}: seconds must be a whole number of 4 or more, got {seconds!r} "
            "(generate at 4 and trim in the edit)"
        )

    prompt_path = project_dir / row.get("prompt_file", f"artifacts/prompts/{shot_id}.txt")
    if not prompt_path.is_file():
        raise ContractError(f"no prompt at {prompt_path}")
    prompt = prompt_path.read_text().strip()
    if not prompt:
        raise ContractError(f"{prompt_path} is empty")

    paths, urls = split_references(list(row.get("reference_ids") or []), project_dir)
    operation = route["operation"] if (paths or urls) else "text_to_video"

    inputs: dict[str, Any] = {
        "operation": operation,
        "prompt": prompt,
        "model_variant": str(route["model_variant"]),
        "resolution": row.get("resolution", route["resolution"]),
        "aspect_ratio": route["aspect_ratio"],
        "duration": seconds,
        "generate_audio": bool(row.get("generate_audio", True)),
        "custom_price_cny_per_million_tokens": float(route["custom_price_cny_per_million_tokens"]),
        "output_path": str(project_dir / "assets" / "video" / f"{shot_id}.mp4"),
    }
    if paths:
        inputs["reference_image_paths"] = paths
    if urls:
        inputs["reference_image_urls"] = urls
    if row.get("return_last_frame"):
        inputs["return_last_frame"] = True
    # An edit ("change only X") sends the previous take back. Only its provider
    # URL works, and only until the urls_expire logged beside it.
    if row.get("reference_video_urls"):
        inputs["reference_video_urls"] = list(row["reference_video_urls"])
        if row.get("reference_video_durations"):  # billed input; absent, the estimate assumes 15 s
            inputs["reference_video_durations"] = list(row["reference_video_durations"])
        inputs["operation"] = route["operation"]
    return inputs


def next_attempt_path(output_path: Path) -> Path:
    """Where an existing take moves to, so a retake never destroys it."""
    n = 1
    while True:
        candidate = output_path.with_name(f"{output_path.stem}.attempt{n}{output_path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def append_spend(log_path: Path, entry: dict[str, Any]) -> float:
    """Append one attempt to the project's spend log and return the running total."""
    log = json.loads(log_path.read_text()) if log_path.is_file() else {"attempts": []}
    log["attempts"].append(entry)
    log["total_usd"] = round(sum(a.get("usd") or 0.0 for a in log["attempts"]), 4)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
    return log["total_usd"]


def limit_reached(contract: dict[str, Any], log: dict[str, Any], shot_id: str, estimate_usd: float) -> str | None:
    """Why this shot may not be generated, or None. Only billed takes count:
    a filter refusal is free and says nothing about the prompt."""
    attempts = log.get("attempts", [])
    billed = sum(1 for a in attempts if a.get("shot") == shot_id and (a.get("usd") or 0.0) > 0)
    cap = contract.get("max_attempts_per_shot", DEFAULT_MAX_ATTEMPTS)
    if billed >= cap:
        return (f"{shot_id} already has {billed} billed takes and the cap is {cap}. Another wording is "
                "unlikely to fix it: split the shot, add a reference, or ask the user to accept a deviation.")
    budget = contract.get("budget_usd")
    if budget is not None:
        spent = sum(a.get("usd") or 0.0 for a in attempts)
        if spent + estimate_usd > float(budget) + 1e-9:
            return (f"spent {spent:.4f} USD, this take is about {estimate_usd:.4f} USD, and the agreed "
                    f"budget is {float(budget):.4f} USD.")
    return None


def actual_usd(tokens: int | None, price_cny_per_million: float, cny_per_usd: float) -> float:
    if not tokens:
        return 0.0
    return round(tokens * price_cny_per_million / 1_000_000 / cny_per_usd, 4)


def _print_plan(tool: Any, shot_id: str, inputs: dict[str, Any]) -> None:
    model, variant = tool._resolve_model(inputs)
    print(f"SHOT        {shot_id}")
    print(f"TOOL        seedance_ark   provider: Volcengine/BytePlus Ark")
    print(f"MODEL       {model}   (variant {variant})")
    print(f"OUTPUT      {inputs['resolution']} {inputs['aspect_ratio']}, {inputs['duration']} s, {inputs['operation']}")
    print(f"PROMPT      {len(inputs['prompt'].split())} words")
    for ref in inputs.get("reference_image_paths", []):
        print(f"REFERENCE   {Path(ref).name}  ({Path(ref).stat().st_size / 1e6:.1f} MB)")
    for ref in inputs.get("reference_image_urls", []):
        print(f"REFERENCE   {ref.split('?')[0]}")
    print(f"ESTIMATE    {tool.estimate_token_usage(inputs):,} tokens = {tool.estimate_cost(inputs):.4f} USD")


def run_shot(project_dir: Path, shot_id: str, go: bool, filter_retries: int) -> int:
    from lib.env_loader import load_env
    from tools.video.seedance_ark import SeedanceArkVideo

    load_env(ROOT)
    try:
        contract = load_contract(project_dir)
        inputs = build_inputs(contract, shot_id, project_dir)
    except ContractError as exc:
        print(f"CONTRACT    {exc}")
        return EXIT_BAD_INPUT

    tool = SeedanceArkVideo()
    _print_plan(tool, shot_id, inputs)
    try:
        tool._build_payload(inputs)
    except ImportError as exc:
        print(f"PAYLOAD     cannot validate: {exc}. Run through make, or with the repo's .venv python.")
        return EXIT_BAD_INPUT
    except Exception as exc:
        print(f"PAYLOAD     refused locally: {exc}")
        print("            No API call was made and nothing was charged.")
        return EXIT_BAD_INPUT
    print("PAYLOAD     validated locally, no API call yet")

    if not go:
        print("\nPlan only. Nothing was generated or charged. Use `make shot-go` to generate.")
        return 0

    log_path = project_dir / "artifacts" / "spend_log.json"
    log = json.loads(log_path.read_text()) if log_path.is_file() else {}
    reason = limit_reached(contract, log, shot_id, tool.estimate_cost(inputs))
    if reason:
        print(f"LIMIT       {reason}")
        print("            Nothing was generated or charged. Stop here and ask the user; the limits are")
        print("            budget_usd and max_attempts_per_shot in the shot contract.")
        return EXIT_LIMIT_REACHED

    output_path = Path(inputs["output_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        kept = next_attempt_path(output_path)
        output_path.rename(kept)
        print(f"KEPT        previous take moved to {kept.name}")

    price = inputs["custom_price_cny_per_million_tokens"]
    refusals = 0
    while True:
        started = time.time()
        print(f"\nGENERATING  attempt {refusals + 1} ...", flush=True)
        result = tool.execute(inputs)
        elapsed = round(time.time() - started, 1)
        data = result.data if isinstance(result.data, dict) else {}
        tokens = (data.get("usage") or {}).get("completion_tokens")
        usd = actual_usd(tokens, price, tool._get_cny_per_usd()) if result.success else 0.0
        kind = None if result.success else classify_error(result.error)
        now = datetime.now(timezone.utc)
        total = append_spend(log_path, {
            "shot": shot_id,
            "at": now.isoformat(timespec="seconds"),
            "outcome": "succeeded" if result.success else kind,
            "usd": usd,
            "tokens": tokens,
            "elapsed_s": elapsed,
            "task_id": data.get("task_id"),
            "video_url": data.get("video_url"),
            "last_frame_url": data.get("last_frame_url"),
            "urls_expire": (now + OUTPUT_URL_LIFETIME).isoformat(timespec="seconds") if data.get("video_url") else None,
            "error": None if result.success else str(result.error),
        })

        if result.success:
            print(f"DONE        {output_path}")
            print(f"COST        {usd:.4f} USD ({tokens:,} tokens, {elapsed} s)   project total {total:.4f} USD")
            if data.get("last_frame_url"):
                print("LAST FRAME  a last_frame_url was returned. It expires in 24 hours: download it now,")
                print("            byte for byte, per 'People on the Ark route' in the open-montage skill.")
            return 0

        print(f"REFUSED     {result.error}")
        if kind == OUTPUT_LIKENESS_FILTER:
            refusals += 1
            print("            The finished clip was discarded because the rendered face read as a")
            print("            recognisable person. This is a random refusal and it was free. It says")
            print("            nothing about the prompt and does not count as an adherence failure.")
            if refusals <= filter_retries:
                print(f"            Retrying unchanged ({refusals} of {filter_retries}).")
                continue
            print(f"            Refused {refusals} times. Stop here and ask the user how to proceed.")
            return EXIT_LIKENESS_EXHAUSTED
        if kind == INPUT_PERSON_FILTER:
            print("            A reference image or video was refused as showing a real person. It was")
            print("            free. Do not alter the image. Choose a person_route: see 'People on the")
            print("            Ark route' in the open-montage skill.")
            return EXIT_PERSON_REFUSED
        return EXIT_PROVIDER_FAILURE


def make_sheets(project_dir: Path, shot_id: str) -> int:
    """Contact sheets and stills for a clean reviewer: the whole clip at a glance."""
    clip = project_dir / "assets" / "video" / f"{shot_id}.mp4"
    if not clip.is_file():
        print(f"no clip at {clip}")
        return EXIT_BAD_INPUT
    out = project_dir / "artifacts" / "adherence"
    out.mkdir(parents=True, exist_ok=True)
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clip)],
        check=True, capture_output=True, text=True).stdout.strip())
    seconds = max(1, round(duration))
    jobs = [
        (["-i", str(clip), "-vf", f"fps=1,scale=480:-2,tile={seconds}x1"], f"{shot_id}_sheet.png"),
        (["-i", str(clip), "-vf", f"fps=4,scale=320:-2,tile=8x{-(-seconds * 4 // 8)}"], f"{shot_id}_sheet_4fps.png"),
        (["-i", str(clip), "-frames:v", "1"], f"{shot_id}_first.png"),
        (["-ss", f"{duration / 2:.3f}", "-i", str(clip), "-frames:v", "1"], f"{shot_id}_mid.png"),
        (["-sseof", "-0.2", "-i", str(clip), "-frames:v", "1"], f"{shot_id}_last.png"),
    ]
    for args, name in jobs:
        subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(out / name)], check=True)
        print(f"wrote {out / name}")
    print("\nJudge the whole clip, never a single frame: watch it in real time as well.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("project", help="project id under projects/")
    parser.add_argument("shot", help="shot id from the contract, e.g. shot_02")
    parser.add_argument("--go", action="store_true", help="generate (costs money); without it, plan only")
    parser.add_argument("--sheet", action="store_true", help="build contact sheets for an existing clip")
    parser.add_argument("--filter-retries", type=int, default=1,
                        help="unchanged retries after a likeness-filter refusal (skill default: 1)")
    args = parser.parse_args(argv)

    project_dir = ROOT / "projects" / args.project
    if not project_dir.is_dir():
        print(f"no project at {project_dir}")
        return EXIT_BAD_INPUT
    if args.sheet:
        return make_sheets(project_dir, args.shot)
    return run_shot(project_dir, args.shot, args.go, max(0, args.filter_retries))


if __name__ == "__main__":
    raise SystemExit(main())
