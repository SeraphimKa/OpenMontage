#!/usr/bin/env python3
"""Month-to-date generation spend across every project on this machine.

Nothing else totals spend across projects: the provider has no spend cap at any
level and each project's log knows only itself. This reads every
`projects/*/artifacts/spend_log.json` (and the inline `spend_log` that older
contracts carry), sums the billed attempts per calendar month, and compares the
current month against a cap.

The cap is `OM_MONTHLY_CAP_USD`, from the environment or from `.env` (read with
python-dotenv; nothing else in that file is touched or printed). No cap means a
report only.

  scripts/om_spend.py            the report for this month
  scripts/om_spend.py --month 2026-08
  scripts/om_spend.py --check    exit 3 when this month's spend has reached the cap,
                                 which is what the shot-go hook asks; a warning at
                                 80 percent goes to stdout either way.

Each colleague's laptop holds only their own projects, so the total here is that
person's spend, not the team's.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_CAP_REACHED = 3
WARN_FRACTION = 0.8


def read_cap(repo_root: Path) -> Optional[float]:
    raw = os.environ.get("OM_MONTHLY_CAP_USD")
    if not raw:
        env_file = repo_root / ".env"
        if env_file.is_file():
            try:
                from dotenv import dotenv_values
                raw = dotenv_values(env_file).get("OM_MONTHLY_CAP_USD")
            except ImportError:
                raw = None
    if not raw:
        return None
    try:
        cap = float(raw)
    except ValueError:
        return None
    return cap if cap > 0 else None


def attempt_month(attempt: dict) -> Optional[str]:
    """`om_shot.py` writes `at` (ISO datetime); older inline logs write `date`."""
    for key in ("at", "date"):
        value = attempt.get(key)
        if isinstance(value, str) and len(value) >= 7 and value[4] == "-":
            return value[:7]
    return None


def load_attempts(project_dir: Path) -> list[dict]:
    log_path = project_dir / "artifacts" / "spend_log.json"
    if log_path.is_file():
        try:
            log = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log = None
        if isinstance(log, dict) and isinstance(log.get("attempts"), list):
            return [a for a in log["attempts"] if isinstance(a, dict)]
    contract_path = project_dir / "artifacts" / "shot_contract.json"
    if contract_path.is_file():
        try:
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        inline = contract.get("spend_log") if isinstance(contract, dict) else None
        if isinstance(inline, list):
            return [a for a in inline if isinstance(a, dict)]
    return []


def spend_by_month(projects_dir: Path) -> dict[str, dict[str, dict]]:
    """{month: {project: {"usd": n, "billed": k, "undated_usd": m}}}.

    Attempts with no usable date are counted under "undated" so money is never
    silently dropped from the total.
    """
    table: dict[str, dict[str, dict]] = defaultdict(dict)
    if not projects_dir.is_dir():
        return table
    for project_dir in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        for attempt in load_attempts(project_dir):
            usd = attempt.get("usd")
            if not isinstance(usd, (int, float)) or usd <= 0:
                continue
            month = attempt_month(attempt) or "undated"
            row = table[month].setdefault(project_dir.name, {"usd": 0.0, "billed": 0})
            row["usd"] = round(row["usd"] + float(usd), 4)
            row["billed"] += 1
    return table


def month_total(table: dict, month: str) -> float:
    return round(sum(r["usd"] for r in table.get(month, {}).values()), 4)


def this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def report(table: dict, month: str, cap: Optional[float]) -> str:
    lines = [f"Generation spend, {month} (this machine only)"]
    rows = table.get(month, {})
    if not rows:
        lines.append("  no billed attempts")
    for project, row in sorted(rows.items(), key=lambda kv: -kv[1]["usd"]):
        lines.append(f"  {project:<32} {row['usd']:>8.2f} USD  {row['billed']:>3} billed take(s)")
    total = month_total(table, month)
    lines.append(f"  {'total':<32} {total:>8.2f} USD")
    undated = month_total(table, "undated")
    if undated:
        lines.append(f"  (plus {undated:.2f} USD in attempts with no date, in no month)")
    if cap is None:
        lines.append("  no monthly cap set (OM_MONTHLY_CAP_USD)")
    else:
        lines.append(f"  cap {cap:.2f} USD, {max(cap - total, 0):.2f} left")
    return "\n".join(lines)


def check(table: dict, month: str, cap: Optional[float]) -> tuple[int, str]:
    total = month_total(table, month)
    if cap is None:
        return EXIT_OK, ""
    if total >= cap:
        return EXIT_CAP_REACHED, (
            f"Monthly generation cap reached: {total:.2f} USD spent in {month} against a cap of "
            f"{cap:.2f} USD (OM_MONTHLY_CAP_USD). No further paid takes this month unless the "
            f"cap is raised by whoever set it.")
    if total >= WARN_FRACTION * cap:
        return EXIT_OK, (
            f"Warning: {total:.2f} USD of the {cap:.2f} USD monthly cap is spent in {month}; "
            f"{cap - total:.2f} USD left.")
    return EXIT_OK, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", help="YYYY-MM (default: the current month, UTC)")
    parser.add_argument("--check", action="store_true",
                        help="exit 3 when the month's spend has reached the cap")
    parser.add_argument("--projects-dir", help="projects root (default: the repo's projects/)")
    parser.add_argument("--cap", type=float, help="override OM_MONTHLY_CAP_USD")
    args = parser.parse_args(argv)

    from lib.paths import PROJECTS_DIR, REPO_ROOT
    projects_dir = Path(args.projects_dir) if args.projects_dir else PROJECTS_DIR
    month = args.month or this_month()
    if len(month) != 7 or month[4] != "-":
        print(f"bad month {month!r}: expected YYYY-MM")
        return EXIT_BAD_INPUT
    cap = args.cap if args.cap else read_cap(REPO_ROOT)
    table = spend_by_month(projects_dir)

    if args.check:
        code, message = check(table, month, cap)
        if message:
            print(message)
        return code
    print(report(table, month, cap))
    _, message = check(table, month, cap)
    if message:
        print(message)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
