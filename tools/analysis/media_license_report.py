"""Media licence report: every asset a project uses, and its licence.

Reads a project's ``artifacts/asset_manifest.json`` (and
``artifacts/edit_decisions.json`` when present) and writes
``artifacts/media_licenses.json`` plus a sibling ``media_licenses.md``:
one entry per asset with its raw licence, a licence class, creator,
source URL, whether the edit actually uses it, and a ready-made credit
line for attribution licences. Local, deterministic, no network.

Licence classes
---------------
- ``not_cleared``: the licence text carries an explicit restriction
  (NC, SA, ND, "verify", personal/editorial use, rights-managed), or is
  non-empty and not recognised. A restriction wins over everything else
  the asset says about itself.
- ``user_supplied``: the licence text explicitly says user-, client- or
  customer-provided/supplied/owned; or the licence is empty and the
  subtype is ``user_supplied`` / ``library``.
- ``cleared`` / ``attribution``: from
  `tools.video.stock_sources.classify_license`.
- ``generated``: the licence is empty and the asset carries ``prompt`` or
  ``model``, or has subtype ``generated``.
- ``unknown``: empty licence and none of the above.

``not_cleared`` and ``unknown`` block commercial delivery when the asset
is used. Usage detection fails closed: anything the timeline cannot be
shown not to use is treated as used, and every edit_decisions reference
that matches no manifest asset becomes an ``unknown`` blocker. The report
never decides for the user: `cleared_for_delivery` is false whenever a
used asset blocks, and the compose director stops and surfaces that.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolTier,
)
from tools.video.stock_sources.base import (
    LICENSE_ATTRIBUTION,
    LICENSE_CLEARED,
    LICENSE_NOT_CLEARED,
    classify_license,
    has_restrictive_license_marker,
)

LICENSE_USER_SUPPLIED = "user_supplied"
LICENSE_GENERATED = "generated"
LICENSE_UNKNOWN = "unknown"

LICENSE_CLASSES = (
    LICENSE_CLEARED,
    LICENSE_ATTRIBUTION,
    LICENSE_NOT_CLEARED,
    LICENSE_USER_SUPPLIED,
    LICENSE_GENERATED,
    LICENSE_UNKNOWN,
)
BLOCKING_CLASSES = frozenset({LICENSE_NOT_CLEARED, LICENSE_UNKNOWN})

# Only these types can be placed by `cuts[].source`, so only these get
# usage detection. Everything else (audio mixed from the audio plan,
# fonts, LUTs, animations, diagrams, subtitles, 3D, code) is always used.
CUT_REFERENCEABLE_TYPES = frozenset({"video", "image"})

ORPHAN_REASON = "referenced by edit_decisions but absent from asset_manifest"

# Explicit statements only: bare "supplied" is ordinary provenance
# wording ("Licence supplied by Wikimedia") and must not qualify.
_USER_SUPPLIED_PATTERNS = tuple(re.compile(p) for p in (
    r"\b(user|client|customer)[-\s]?(provided|supplied|owned)\b",
    r"\b(provided|supplied|owned) by (the )?(user|client|customer)\b",
))
# Keys under edit_decisions.audio / .music whose string values name an
# asset. Other strings there still count towards usage, but only these
# are reported as orphans when they match nothing.
_REFERENCE_KEYS = frozenset({"asset_id", "asset_ids", "source", "path", "file", "src"})
_URL_PREFIX_RE = re.compile(r"^https?://[^/]*", re.IGNORECASE)
_NEWLINES_RE = re.compile(r"\r\n|\r|\n")
_QUOTED_TITLE_RE = re.compile(r'"([^"]+)"')
# `"Title" by Name. License: ...` — the form stock tools write.
_BY_AFTER_TITLE_RE = re.compile(r'"[^"]+"\s+by\s+(.+?)(?:\.\s|\.?$)')
_BY_RE = re.compile(r"\bby\s+(.+?)(?:\.\s|\.?$)")


class MediaLicenseReport(BaseTool):
    name = "media_license_report"
    version = "0.1.0"
    tier = ToolTier.ANALYZE
    capability = "license_audit"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = "No setup required. Reads project artifacts from disk."
    agent_skills = []

    capabilities = ["media_license_report", "attribution_credits", "delivery_rights_check"]
    supports = {
        "edit_decisions_usage": True,
        "attribution_credit_lines": True,
        "markdown_report": True,
        "fail_on_blockers": True,
    }
    best_for = [
        "listing every asset a project uses together with its licence",
        "building the attribution credits for an end tag or description",
        "checking a render is cleared for commercial delivery before publishing",
    ]
    not_good_for = [
        "legal advice (it classifies recorded licence strings, it does not verify them)",
        "discovering licences the asset manifest does not record",
    ]
    fallback_tools = []

    input_schema = {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": (
                    "Project workspace, e.g. projects/<id>. Default source of "
                    "artifacts/asset_manifest.json and artifacts/edit_decisions.json."
                ),
            },
            "asset_manifest_path": {
                "type": "string",
                "description": "Override path to asset_manifest.json.",
            },
            "edit_decisions_path": {
                "type": "string",
                "description": (
                    "Override path to edit_decisions.json. When no edit_decisions "
                    "exists every asset is treated as used."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Where to write the JSON report (default "
                    "<project_dir>/artifacts/media_licenses.json). Must not end in "
                    ".md; the markdown is always written alongside as <stem>.md."
                ),
            },
            "fail_on_blockers": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Return success=false when any used asset is not_cleared or "
                    "unknown. Only a real boolean true enables it. The report files "
                    "are written either way."
                ),
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=1, network_required=False
    )
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = ["project_dir", "asset_manifest_path", "edit_decisions_path"]
    side_effects = [
        "writes media_licenses.json and media_licenses.md (default under <project_dir>/artifacts/)",
    ]
    user_visible_verification = [
        "Read media_licenses.md: every used asset has a licence and no blockers remain",
        "Copy the 'Credits to include' block into the end tag or video description",
    ]

    def __init__(self, now: Optional[Callable[[], datetime]] = None) -> None:
        super().__init__()
        self._now = now or (lambda: datetime.now(timezone.utc).replace(microsecond=0))

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        try:
            project_dir = Path(inputs["project_dir"]) if inputs.get("project_dir") else None
            if inputs.get("asset_manifest_path"):
                manifest_path = Path(inputs["asset_manifest_path"])
            elif project_dir is not None:
                manifest_path = project_dir / "artifacts" / "asset_manifest.json"
            else:
                return ToolResult(
                    success=False,
                    error="media_license_report requires project_dir or asset_manifest_path",
                )

            artifacts_dir = project_dir / "artifacts" if project_dir is not None else manifest_path.parent
            output_path = (
                Path(inputs["output_path"]) if inputs.get("output_path")
                else artifacts_dir / "media_licenses.json"
            )
            if output_path.suffix.lower() == ".md":
                return ToolResult(
                    success=False,
                    error=(
                        f"output_path {output_path} ends in .md; pass the JSON report path "
                        "(.json). The markdown is written alongside it as <stem>.md."
                    ),
                )
            markdown_path = output_path.parent / f"{output_path.stem}.md"

            if not manifest_path.is_file():
                return ToolResult(success=False, error=f"asset_manifest not found: {manifest_path}")
            manifest = _read_json(manifest_path)
            if not isinstance(manifest.get("assets"), list):
                return ToolResult(
                    success=False,
                    error=(
                        f"asset_manifest {manifest_path} has no 'assets' list "
                        f"(got {type(manifest.get('assets')).__name__}); cannot report licences."
                    ),
                )

            if inputs.get("edit_decisions_path"):
                edit_path = Path(inputs["edit_decisions_path"])
                if not edit_path.is_file():
                    return ToolResult(
                        success=False, error=f"edit_decisions not found: {edit_path}"
                    )
            else:
                edit_path = artifacts_dir / "edit_decisions.json"
            edit_decisions = _read_json(edit_path) if edit_path.is_file() else None

            if project_dir is not None:
                project_id = project_dir.resolve().name
            else:
                parent = manifest_path.resolve().parent
                project_id = parent.parent.name if parent.name == "artifacts" else parent.name

            report = build_report(manifest, edit_decisions, project_id, self._now())

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            markdown_path.write_text(render_markdown(report), encoding="utf-8")

            data = {
                **report,
                "report_path": str(output_path),
                "markdown_path": str(markdown_path),
            }
            # Only a real boolean true: a string such as "false" must not
            # flip the result.
            fail = inputs.get("fail_on_blockers") is True and not report["cleared_for_delivery"]
            error = None
            if fail:
                ids = ", ".join(b["id"] for b in report["blockers"]) or "(no assets)"
                error = (
                    f"{len(report['blockers'])} used asset(s) are not cleared for "
                    f"commercial delivery: {ids}. See {markdown_path}."
                )
            return ToolResult(
                success=not fail,
                data=data,
                error=error,
                artifacts=[str(output_path), str(markdown_path)],
                cost_usd=0.0,
                duration_seconds=round(time.time() - start, 3),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"{type(e).__name__}: {e}",
                duration_seconds=round(time.time() - start, 3),
            )


# ----------------------------------------------------------------------
# Classification (module-level so tests can hit it directly)
# ----------------------------------------------------------------------


def classify_asset(asset: dict[str, Any]) -> str:
    """Licence class for one asset-manifest entry."""
    license_text = _oneline(asset.get("license")).strip()
    subtype = str(asset.get("subtype") or "").strip().lower()

    if license_text:
        # An explicit restriction blocks whatever else the asset says
        # about itself, including user-supplied wording.
        if has_restrictive_license_marker(license_text):
            return LICENSE_NOT_CLEARED
        if any(p.search(license_text.lower()) for p in _USER_SUPPLIED_PATTERNS):
            return LICENSE_USER_SUPPLIED
        # Recognised as cleared/attribution, else not_cleared. Non-empty
        # text that is not recognised never falls through to the
        # non-blocking generated/library classes.
        return classify_license(license_text)

    if asset.get("prompt") or asset.get("model") or subtype == "generated":
        return LICENSE_GENERATED
    if subtype in ("user_supplied", "library"):
        return LICENSE_USER_SUPPLIED
    return LICENSE_UNKNOWN


def parse_title(asset: dict[str, Any]) -> str:
    match = _QUOTED_TITLE_RE.search(_oneline(asset.get("generation_summary")))
    return match.group(1).strip() if match else ""


def parse_creator(asset: dict[str, Any]) -> str:
    creator = _oneline(asset.get("creator")).strip()
    if creator:
        return creator
    summary = _oneline(asset.get("generation_summary"))
    match = _BY_AFTER_TITLE_RE.search(summary) or _BY_RE.search(summary)
    return match.group(1).strip() if match else ""


def format_credit_line(
    title: str, creator: str, provider: str, license_text: str, url: str
) -> str:
    """`"<title>" by <creator> via <provider> (<license>) <url>`, empty parts omitted."""
    title, creator, provider, license_text, url = (
        _oneline(part).strip() for part in (title, creator, provider, license_text, url)
    )
    parts = [f'"{title}"'] if title else []
    if creator:
        parts.append(f"by {creator}")
    if provider:
        parts.append(f"via {provider}")
    if license_text:
        parts.append(f"({license_text})")
    if url:
        parts.append(url)
    return " ".join(parts)


# ----------------------------------------------------------------------
# Usage detection
# ----------------------------------------------------------------------


def _norm_path(value: str) -> str:
    """Comparable form of a path or URL: no scheme/host, `.`/`..` resolved, lowercase."""
    text = _URL_PREFIX_RE.sub("", _oneline(value).strip().replace("\\", "/"))
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts).lower()


def _basename(value: str) -> str:
    return _norm_path(value).rsplit("/", 1)[-1]


def _match_kind(ref: str, asset_id: str, asset_path: str) -> Optional[str]:
    """"id" or "path" when `ref` names this asset, else None."""
    if asset_id and ref.strip().lower() == asset_id.strip().lower():
        return "id"
    if not asset_path:
        return None
    a, b = _norm_path(ref), _norm_path(asset_path)
    if a and b and (a == b or b.endswith("/" + a) or a.endswith("/" + b)):
        return "path"
    return None


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _walk_strings(value: Any, where: str, key: Optional[str] = None) -> Iterator[tuple[str, str, bool]]:
    if isinstance(value, str):
        yield value, where, key in _REFERENCE_KEYS
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{where}.{k}", str(k))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{where}[{i}]", key)


def _usage_refs(edit_decisions: dict[str, Any]) -> list[tuple[str, str, bool]]:
    """(reference, location, is_asset_reference) for everything that can place media."""
    refs: list[tuple[str, str, bool]] = []
    for i, cut in enumerate(_as_list(edit_decisions.get("cuts"))):
        if isinstance(cut, dict) and isinstance(cut.get("source"), str):
            refs.append((cut["source"], f"cuts[{i}].source", True))
    for i, overlay in enumerate(_as_list(edit_decisions.get("overlays"))):
        if isinstance(overlay, dict) and isinstance(overlay.get("asset_id"), str):
            refs.append((overlay["asset_id"], f"overlays[{i}].asset_id", True))
    subtitles = edit_decisions.get("subtitles")
    if isinstance(subtitles, dict) and subtitles.get("enabled") is not False:
        for key in ("source", "font"):
            if isinstance(subtitles.get(key), str):
                refs.append((subtitles[key], f"subtitles.{key}", True))
    for key in ("music", "audio"):
        refs.extend(_walk_strings(edit_decisions.get(key), key))
    return [ref for ref in refs if ref[0].strip()]


# ----------------------------------------------------------------------
# Report construction
# ----------------------------------------------------------------------


def build_report(
    manifest: dict[str, Any],
    edit_decisions: Optional[dict[str, Any]],
    project_id: str,
    now: datetime,
) -> dict[str, Any]:
    raw_assets = manifest.get("assets")
    if not isinstance(raw_assets, list):
        raise ValueError("asset_manifest 'assets' must be a list")

    notes: list[str] = []
    assets = [a for a in raw_assets if isinstance(a, dict)]
    if len(assets) != len(raw_assets):
        notes.append(
            f"{len(raw_assets) - len(assets)} malformed asset_manifest entr(y/ies) "
            "skipped; any reference to them is reported as an orphan."
        )

    # Decide whether usage can be derived from the timeline at all.
    all_used_reason: Optional[str] = None
    if edit_decisions is None:
        all_used_reason = "edit_decisions not found; every asset is treated as used."
    elif edit_decisions.get("composition_mode") == "atelier":
        all_used_reason = (
            "edit_decisions.composition_mode is 'atelier': the render does not read "
            "cuts, so every asset is treated as used."
        )
    elif not _as_list(edit_decisions.get("cuts")):
        all_used_reason = (
            "edit_decisions has no cuts: usage cannot be derived from the timeline, "
            "so every asset is treated as used."
        )
    if all_used_reason:
        notes.append(all_used_reason)

    ids = [str(a.get("id") or "") for a in assets]
    paths = [str(a.get("path") or "") for a in assets]
    match_kinds: dict[int, set[str]] = {}
    orphans: dict[str, list[str]] = {}
    for ref, where, is_asset_ref in (_usage_refs(edit_decisions) if edit_decisions else []):
        hits = [
            (i, kind) for i in range(len(assets))
            if (kind := _match_kind(ref, ids[i], paths[i]))
        ]
        if not hits:
            # Over-matching is the safe direction for a licence gate.
            base = _basename(ref)
            hits = [
                (i, "basename") for i in range(len(assets))
                if base and paths[i] and _basename(paths[i]) == base
            ]
        for i, kind in hits:
            match_kinds.setdefault(i, set()).add(kind)
        if not hits and is_asset_ref:
            orphans.setdefault(ref, []).append(where)

    entries: list[dict[str, Any]] = []
    basename_only: list[str] = []
    for i, asset in enumerate(assets):
        asset_type = str(asset.get("type") or "")
        license_text = _oneline(asset.get("license"))
        provider = _oneline(asset.get("provider"))
        url = _oneline(asset.get("original_url"))
        license_class = classify_asset(asset)
        creator = parse_creator(asset)

        referenced = i in match_kinds
        used = (
            all_used_reason is not None
            or asset_type not in CUT_REFERENCEABLE_TYPES
            or referenced
        )
        if (
            all_used_reason is None
            and asset_type in CUT_REFERENCEABLE_TYPES
            and match_kinds.get(i) == {"basename"}
        ):
            basename_only.append(ids[i])

        attribution_required = license_class == LICENSE_ATTRIBUTION
        credit = (
            format_credit_line(
                parse_title(asset) or ids[i], creator, provider, license_text, url
            )
            if attribution_required
            else None
        )
        entry = {
            "id": ids[i],
            "type": asset_type,
            "scene_id": str(asset.get("scene_id") or ""),
            "path": paths[i],
            "source_tool": str(asset.get("source_tool") or ""),
            "provider": provider,
            "license": license_text,
            "license_class": license_class,
            "creator": creator,
            "original_url": url,
            "used": used,
            "attribution_required": attribution_required,
            "credit_line": credit,
        }
        evidence = _license_evidence(asset)
        if evidence:
            entry["license_evidence"] = evidence
        entries.append(entry)

    per_class = {cls: 0 for cls in LICENSE_CLASSES}
    for entry in entries:
        per_class[entry["license_class"]] += 1

    blockers = [
        {
            "id": e["id"],
            "type": e["type"],
            "scene_id": e["scene_id"],
            "path": e["path"],
            "license": e["license"],
            "license_class": e["license_class"],
            "reason": (
                "No licence recorded for this asset."
                if e["license_class"] == LICENSE_UNKNOWN
                else f"Licence {e['license']!r} is not cleared for commercial use "
                     "(non-commercial, share-alike, no-derivatives, restricted-use, "
                     "unverifiable or unrecognised)."
            ),
            "orphan_reference": False,
        }
        for e in entries
        if e["used"] and e["license_class"] in BLOCKING_CLASSES
    ]
    blockers += [
        {
            "id": ref,
            "type": "",
            "scene_id": "",
            "path": ref,
            "license": "",
            "license_class": LICENSE_UNKNOWN,
            "reason": ORPHAN_REASON,
            "orphan_reference": True,
        }
        for ref in orphans
    ]
    credits = [e["credit_line"] for e in entries if e["used"] and e["credit_line"]]

    if orphans:
        locations = ", ".join(where for wheres in orphans.values() for where in wheres)
        notes.append(
            f"{len(orphans)} edit_decisions reference(s) match no asset_manifest entry "
            f"and are blocked as unknown ({locations})."
        )
    if basename_only:
        notes.append(
            f"{len(basename_only)} asset(s) matched edit_decisions only by file name "
            f"({', '.join(basename_only)}); confirm they are the same files."
        )
    media_referenced = edit_decisions is not None and bool(
        _as_list(edit_decisions.get("cuts")) or _as_list(edit_decisions.get("overlays"))
    )
    empty_manifest_with_media = not entries and media_referenced
    if empty_manifest_with_media:
        notes.append(
            "asset_manifest lists no assets but edit_decisions places media; "
            "not cleared for delivery."
        )
    if all_used_reason is None:
        unused = sum(1 for e in entries if not e["used"])
        if unused:
            notes.append(
                f"{unused} asset(s) in the manifest are not referenced by edit_decisions "
                "and are not counted as blockers or credits."
            )
    used_generated = sum(1 for e in entries if e["used"] and e["license_class"] == LICENSE_GENERATED)
    if used_generated:
        notes.append(
            f"{used_generated} generated asset(s): commercial rights depend on the "
            "generating provider's terms; confirm them before delivery."
        )
    used_supplied = sum(1 for e in entries if e["used"] and e["license_class"] == LICENSE_USER_SUPPLIED)
    if used_supplied:
        notes.append(
            f"{used_supplied} user-supplied asset(s): rights are the user's or client's "
            "responsibility; confirm they hold them."
        )

    return {
        "version": "1.0",
        "project_id": project_id,
        "generated_at": now.isoformat(),
        "summary": {
            "per_class": per_class,
            "total_assets": len(entries),
            "used_count": sum(1 for e in entries if e["used"]),
            "blockers_count": len(blockers),
        },
        "cleared_for_delivery": not blockers and not empty_manifest_with_media,
        "blockers": blockers,
        "attribution_credits": credits,
        "assets": entries,
        "notes": notes,
    }


# ----------------------------------------------------------------------
# Markdown
# ----------------------------------------------------------------------


def _oneline(value: Any) -> str:
    return _NEWLINES_RE.sub(" ", str(value or ""))


def _cell(value: Any) -> str:
    text = "yes" if value is True else "no" if value is False else _oneline(value)
    return text.replace("|", "\\|") or "—"


def _code(value: Any) -> str:
    text = _oneline(value)
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    cleared = "yes" if report["cleared_for_delivery"] else "**NO**"
    lines = [
        f"# Media licence report — {_oneline(report['project_id'])}",
        "",
        f"Generated {report['generated_at']}. Cleared for commercial delivery: {cleared}.",
        "",
        "## Summary",
        "",
        "| Licence class | Assets |",
        "|---|---|",
    ]
    lines += [f"| {cls} | {count} |" for cls, count in summary["per_class"].items()]
    lines += [
        f"| **total** | {summary['total_assets']} |",
        f"| **used** | {summary['used_count']} |",
        f"| **blockers** | {summary['blockers_count']} |",
        "",
        "## Credits to include",
        "",
    ]
    if report["attribution_credits"]:
        lines.append("```text")
        # A ``` inside a credit would close the fence early.
        lines += [_oneline(credit).replace("```", "` ` `") for credit in report["attribution_credits"]]
        lines.append("```")
    else:
        lines.append("No attribution credits required.")
    lines += ["", "## Blocked for commercial delivery", ""]
    if report["blockers"]:
        for b in report["blockers"]:
            if b.get("orphan_reference"):
                lines.append(f"- {_code(b['id'])} (not in asset_manifest): {_oneline(b['reason'])}")
            else:
                lines.append(
                    f"- {_code(b['id'])} ({_oneline(b['type'])}, scene "
                    f"{_oneline(b['scene_id']) or '—'}): {_oneline(b['reason'])}"
                )
    else:
        lines.append("None.")
    lines += [
        "",
        "## All assets",
        "",
        "| ID | Type | Scene | Used | Class | Licence | Creator | Provider | Source URL | Evidence |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for e in report["assets"]:
        lines.append("| " + " | ".join(_cell(v) for v in (
            e["id"], e["type"], e["scene_id"], e["used"], e["license_class"],
            e["license"], e["creator"], e["provider"], e["original_url"],
            (e.get("license_evidence") or {}).get("url"),
        )) + " |")
    if report["notes"]:
        lines += ["", "## Notes", ""]
        lines += [f"- {_oneline(note)}" for note in report["notes"]]
    return "\n".join(lines) + "\n"


_EVIDENCE_REQUIRED = ("url", "method", "matched_text", "fetched_at", "resolver_version")


def _license_evidence(asset: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The manifest's license_evidence (written by license_resolver), well-formed fields only.

    `previous_licenses` is carried as a list of strings; a legacy
    `previous_license` string is migrated to the front of it.
    """
    raw = asset.get("license_evidence")
    if not isinstance(raw, dict):
        return None
    evidence: dict[str, Any] = {k: raw[k] for k in _EVIDENCE_REQUIRED if isinstance(raw.get(k), str)}
    if len(evidence) != len(_EVIDENCE_REQUIRED):
        return None
    history = raw.get("previous_licenses")
    history = [v for v in history if isinstance(v, str)] if isinstance(history, list) else []
    legacy = raw.get("previous_license")
    if isinstance(legacy, str) and legacy.strip() and legacy not in history:
        history.insert(0, legacy)
    if history:
        evidence["previous_licenses"] = history
    return evidence


def _read_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data
