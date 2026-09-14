"""License resolver: fill missing or unverified licences from each asset's source.

Reads a project's ``artifacts/asset_manifest.json``, asks each selected
asset's source what its licence is, and writes the answer back into the
manifest together with the evidence it was read from. A licence stays
blank (or stays "verify per item") only when the source genuinely will
not say, and every such asset is reported with the reason.

Strategy is chosen by the asset's ``provider`` field first. Only the
page/API strategies may also be chosen by the ``original_url`` host:

- ``wikimedia``: Commons API ``extmetadata`` by page id (``?curid=``) or
  ``File:`` title, from Commons URLs only (other wikis are never
  queried). Licence = LicenseShortName, fallback UsageTerms; creator =
  Artist.
- ``archive_org``: ``/metadata/<identifier>`` → ``licenseurl``, fallback
  the adapter's collection rule.
- ``freesound``: API v2 ``/sounds/<id>/`` (needs FREESOUND_API_KEY).
- ``loc``: ``<item_url>?fo=json`` → ``item.rights``; only a positive
  public-domain statement resolves.
- ``mixkit`` / ``esa``: the clip page's own licence markers (see the
  dated marker constants below), bound to the clip URL.
- pexels, pixabay, coverr, unsplash, videvo, dareful, nasa, nara, noaa,
  pond5_pd: the site-wide licence from the adapter's constant, only for
  an explicit ``provider`` and only when ``original_url`` is on the
  provider's own site. These hosts also serve differently licensed
  media, so the host alone never selects a site policy.
- ``jaxa`` and unknown sources: unresolved.

All HTTP goes through one injectable ``fetch(url, params, headers,
timeout) -> (status, text, final_url)`` (a 2-tuple is accepted too) so
tests run offline. An API or page answer is authoritative: when it
disagrees with a non-empty manifest licence the source value is taken and
the old value is appended to ``license_evidence.previous_licenses``. A
site policy or the archive.org collection rule is not: it only fills a
blank licence or replaces a known adapter placeholder. User-declared
licences are never re-checked.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import hashlib
import html
import json
import os
import re
import stat
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import jsonschema

from schemas.artifacts import load_schema, validate_artifact
from tools.analysis.media_license_report import (
    LICENSE_GENERATED,
    LICENSE_USER_SUPPLIED,
    _USER_SUPPLIED_PATTERNS,
    classify_asset,
)
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
from tools.video.stock_sources import (
    coverr,
    dareful,
    esa,
    jaxa,
    mixkit,
    nara,
    nasa,
    noaa,
    pexels,
    pixabay_video,
    pond5_pd,
    unsplash,
    videvo,
    wikimedia,
)
from tools.video.stock_sources.archive_org import _license_from_collection, _to_text
from tools.video.stock_sources.base import LICENSE_NOT_CLEARED, classify_license, has_restrictive_license_marker
from tools.video.stock_sources.loc import _LICENSE_CHECK as _LOC_LICENSE_CHECK
from tools.video.stock_sources.loc import _LICENSE_PD as _LOC_LICENSE_PD

FetchResult = Union[tuple[int, str], tuple[int, str, str]]
Fetch = Callable[[str, Optional[dict[str, Any]], dict[str, str], float], FetchResult]

METHOD_API = "api"
METHOD_PAGE = "page"
METHOD_SITE_POLICY = "site_policy"

STATUS_RESOLVED = "resolved"
STATUS_UNCHANGED = "unchanged"
STATUS_UNRESOLVED = "unresolved"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"

MATCHED_TEXT_LIMIT = 300
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = frozenset({"text/html", "application/json", "text/plain"})
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_DEADLINE_SECONDS = 30.0
USER_DECLARED_REASON = "user-declared, not re-checked"

_monotonic = time.monotonic

_COMMONS_API_URL = wikimedia._API_URL
_DEFAULT_USER_AGENT = wikimedia._USER_AGENT
_COMMONS_HOST = "commons.wikimedia.org"
_COMMONS_UPLOAD_HOST = "upload.wikimedia.org"
_COMMONS_UPLOAD_PREFIX = "/wikipedia/commons/"
_ARCHIVE_METADATA_URL = "https://archive.org/metadata"
_FREESOUND_API_URL = "https://freesound.org/apiv2"
_ESA_PATH_PREFIX = "/ESA_Multimedia/"
_LOC_PATH_PREFIXES = ("/item/", "/resource/")

# Environment variables whose values must never reach a reason, note or error.
_SECRET_ENV_VARS = ("FREESOUND_API_KEY",)
_AUTH_VALUE_RE = re.compile(r"\b(Token|Bearer)\s+[^\s'\",)]+")

# Hosts each source serves its files and pages from.
_SOURCE_HOSTS: dict[str, tuple[str, ...]] = {
    "wikimedia": ("wikimedia.org", "wikipedia.org"),
    "archive_org": ("archive.org",),
    "freesound": ("freesound.org",),
    "loc": ("loc.gov",),
    "mixkit": ("mixkit.co",),
    "esa": ("esa.int",),
    "jaxa": ("jaxa.jp",),
    "pexels": ("pexels.com",),
    "pixabay_video": ("pixabay.com",),
    "coverr": ("coverr.co",),
    "unsplash": ("unsplash.com",),
    "videvo": ("videvo.net",),
    "dareful": ("dareful.com",),
    "nasa": ("nasa.gov",),
    "nara": ("archives.gov",),
    "noaa": ("noaa.gov",),
    "pond5_pd": ("pond5.com",),
}
# Hosts that suffix-match a provider's domain but serve other licences.
_EXCLUDED_HOSTS: dict[str, tuple[str, ...]] = {
    "unsplash": ("plus.unsplash.com",),  # Unsplash+ premium, not the Unsplash License
}

_PROVIDER_ALIASES = {
    **{key: key for key in _SOURCE_HOSTS},
    "wikimedia_commons": "wikimedia",
    "commons": "wikimedia",
    "archive.org": "archive_org",
    "internet_archive": "archive_org",
    "freesound_music": "freesound",
    "library_of_congress": "loc",
    "pixabay": "pixabay_video",
    "pixabay_music": "pixabay_video",
}

# Site-wide licences: the first entry is applied; any entry already in the
# manifest is accepted as correct (Videvo serves two licences).
_SITE_POLICIES: dict[str, tuple[str, ...]] = {
    "pexels": (pexels._PEXELS_LICENSE,),
    "pixabay_video": (pixabay_video._LICENSE,),
    "coverr": (coverr._LICENSE,),
    "unsplash": (unsplash._UNSPLASH_LICENSE,),
    "videvo": (videvo._LICENSE_ATTR, videvo._LICENSE_CC),
    "dareful": (dareful._LICENSE,),
    "nasa": (nasa._LICENSE,),
    "nara": (nara._LICENSE,),
    "noaa": (noaa._LICENSE,),
    "pond5_pd": (pond5_pd._LICENSE,),
}

# Only page/API strategies route by host. Every site-policy host also serves
# media under other licences (paid Pond5 stock, Videvo premium, Unsplash+,
# NARA donated material), so a site policy needs an explicit provider.
_HOST_ROUTABLE = frozenset({"wikimedia", "archive_org", "freesound", "loc", "mixkit", "esa", "jaxa"})

# Providers whose adapter licence is a per-item placeholder, so their assets
# are always re-checked against the source.
_ALWAYS_RECHECK = frozenset({"mixkit", "esa", "loc"})
_VERIFY_RE = re.compile(r"\bverify\b")


def _normalise(license_text: str) -> str:
    return " ".join(str(license_text or "").split()).casefold()


# Placeholder strings the adapters write when they cannot tell the licence.
# A non-authoritative resolution (site policy, collection rule) may replace
# these, and nothing else that is non-empty.
_PLACEHOLDER_LICENSES = frozenset(_normalise(text) for text in (
    mixkit._LICENSE,
    esa._LICENSE,
    jaxa._LICENSE,
    _LOC_LICENSE_CHECK,
    wikimedia._COMMONS_LICENSE,
    _license_from_collection(""),
))

# Library of Congress: a sentence resolves to public domain only when it says
# so positively. Anything hedged or negated fails closed.
_LOC_POSITIVE_RE = re.compile(r"\bpublic domain\b|\bno known restrictions\b")
_LOC_HEDGE_RE = re.compile(r"\bnot\b|n't\b|\bmay\s+be\b|\brestricted\b|\bpermission\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.;!?])\s+|\n+")

# Mixkit markers, recorded 2026-09-14 from live clip pages
# (free-stock-video/forest-under-a-tropical-rain-6890/ is Restricted,
# free-stock-video/window-on-a-rainy-day-2846/ is Free). Each clip page links
# its own licence, e.g.
#   <a href="/license/#videoRestricted" ... data-license="videoRestricted">Mixkit Restricted License</a>
#   <a href="/license/#videoFree" ... data-license="videoFree">Mixkit Stock Video Free License</a>
# and its JSON-LD VideoObject ("@id":"https://mixkit.co/free-stock-video/<slug>-<id>/#video")
# carries "copyrightNotice":"Mixkit Restricted License" or "copyrightNotice":"Free".
# The JSON-LD "license" URL reads #videoFree on Restricted clips too, so it is
# deliberately ignored. Listing pages carry a generic data-license="videoFree"
# "Mixkit License" link and other clips' notices, hence the clip-URL, @id and
# redirect checks.
_MIXKIT_DATA_LICENSE_RESTRICTED = 'data-license="videoRestricted"'
_MIXKIT_DATA_LICENSE_FREE = 'data-license="videoFree"'
_MIXKIT_NOTICE_RESTRICTED = '"copyrightNotice":"Mixkit Restricted License"'
_MIXKIT_NOTICE_FREE = '"copyrightNotice":"Free"'
_MIXKIT_RESTRICTED_LICENSE = "Mixkit Restricted License"
_MIXKIT_FREE_LICENSE = "Mixkit Stock Video Free License"
_MIXKIT_CLIP_PATH_RE = re.compile(r"^/free-stock-video/[^/]+-\d+/?$")

# ESA markers, recorded 2026-09-14 from the live page
# esa.int/ESA_Multimedia/Videos/2020/12/Gaia_s_stellar_motion_for_the_next_1.6_million_years:
#   <ul class="modal__meta_licence">
#     <li><b>CREDIT</b><br> ESA/Gaia/DPAC; CC BY-SA 3.0 IGO. Acknowledgement: ...</li>
#     <li><b>LICENCE</b><br> <a ...>CC BY-SA 3.0 IGO</a> or <a ...>ESA Standard Licence</a>
#         <br>(content can be used under either licence)</li>
#   </ul>
# Licences are read from the LICENCE item only (credits name third-party
# components). Listed licences are alternatives, so CC BY 4.0 wins when offered.
_ESA_LICENCE_BLOCK_RE = re.compile(r'<ul class="modal__meta_licence">(.*?)</ul>', re.S)
_ESA_LICENCE_ITEM_RE = re.compile(r"<b>\s*LICEN[CS]E\s*</b>(.*?)</li>", re.S | re.I)
_ESA_CREDIT_ITEM_RE = re.compile(r"<b>\s*CREDIT\s*</b>(.*?)</li>", re.S | re.I)
_ESA_CC_BY = "CC BY 4.0"
_ESA_MARKERS = (
    (_ESA_CC_BY, re.compile(r"\bCC BY 4\.0\b")),
    ("CC BY-SA 3.0 IGO", re.compile(r"\bCC BY-SA 3\.0 IGO\b")),
    ("ESA Standard Licence", re.compile(r"\bESA Standard Licen[cs]e\b", re.I)),
)

_TAG_RE = re.compile(r"<[^>]+>")
_FREESOUND_ID_RE = re.compile(r"/sounds/(\d+)")
_CHARSET_RE = re.compile(r"charset=([\w.-]+)", re.I)
_EVIDENCE_REQUIRED = ("url", "method", "matched_text", "fetched_at", "resolver_version")
_WRITTEN_FIELDS = ("license", "license_class", "creator", "license_evidence")


class _Unresolved(Exception):
    """The source was reached (or could not be asked) and does not say."""


class _SourceError(Exception):
    """The source could not be read: HTTP error, bad response, bounds exceeded."""


class ManifestChangedError(RuntimeError):
    """The manifest on disk changed while the resolver was running."""


@dataclass
class _Resolution:
    license: str
    method: str
    url: str
    matched_text: str
    creator: str = ""
    # False for site policies and the archive.org collection rule: no item-level
    # source was consulted, so they may not replace non-placeholder text.
    authoritative: bool = True


@dataclass
class _Context:
    fetch: Fetch
    headers: dict[str, str]
    timeout: float
    deadline_seconds: float
    monotonic: Callable[[], float]
    deadline_at: float = field(default=0.0)


class LicenseResolver(BaseTool):
    name = "license_resolver"
    version = "0.2.0"
    tier = ToolTier.ANALYZE
    capability = "license_audit"
    provider = "openmontage"
    stability = ToolStability.BETA
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.API

    dependencies = ["python:requests"]
    install_instructions = (
        "No API key needed for Wikimedia, archive.org, Library of Congress, Mixkit, ESA "
        "or site-policy sources. Freesound needs FREESOUND_API_KEY "
        "(https://freesound.org/apiv2/apply/)."
    )
    agent_skills = []

    capabilities = ["license_resolution", "license_evidence"]
    supports = {
        "write_back": True,
        "backup": True,
        "only_unresolved": True,
        "asset_subset": True,
        "evidence": True,
    }
    best_for = [
        "filling blank or 'verify per item' licences in asset_manifest from the asset's source",
        "recording where each licence was read, before media_license_report",
    ]
    not_good_for = [
        "legal advice (it records what the source states)",
        "generated or user-supplied assets (skipped unless named in asset_ids)",
        "JAXA and other sources that publish no readable per-item licence",
    ]
    fallback_tools = []

    input_schema = {
        "type": "object",
        "properties": {
            "project_dir": {
                "type": "string",
                "description": "Project workspace, e.g. projects/<id>. Default source of artifacts/asset_manifest.json.",
            },
            "asset_manifest_path": {
                "type": "string",
                "description": "Override path to asset_manifest.json.",
            },
            "only_unresolved": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Only assets whose licence is empty, is not cleared and says 'verify', or whose "
                    "source is mixkit/esa/loc. Pass false to re-check every asset."
                ),
            },
            "asset_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict to these asset ids (every id must exist in the manifest). Naming a "
                    "generated or user-supplied asset here resolves it anyway."
                ),
            },
            "write_back": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Write results into the manifest (atomic replace). The first pre-resolver "
                    "manifest is kept as <manifest>.bak and every changing run also writes "
                    "<stem>.<UTC>.bak. Pass false for a dry report."
                ),
            },
            "timeout_seconds": {
                "type": "number",
                "default": DEFAULT_TIMEOUT_SECONDS,
                "minimum": 1,
                "description": "Budget per HTTP request.",
            },
            "deadline_seconds": {
                "type": "number",
                "default": DEFAULT_DEADLINE_SECONDS,
                "minimum": 1,
                "description": "Overall budget per asset, across its requests.",
            },
            "user_agent": {
                "type": "string",
                "description": "User-Agent header for every request.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=128, vram_mb=0, disk_mb=1, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=0, retryable_errors=[])
    idempotency_key_fields = ["project_dir", "asset_manifest_path", "asset_ids", "only_unresolved"]
    side_effects = [
        "HTTP GETs to each asset's source (Wikimedia Commons, archive.org, Freesound, loc.gov, mixkit.co, esa.int)",
        "rewrites asset_manifest.json (atomic, mode preserved) when write_back is true and something changed",
        "keeps the first asset_manifest.json.bak and writes asset_manifest.<UTC>.bak on every change",
    ]
    user_visible_verification = [
        "Every resolved asset carries license_evidence.url; open a few and confirm the licence",
        "Read the unresolved reasons; those licences still block delivery until settled",
        "List method=site_policy results separately: no page or API was consulted for those items",
    ]

    def __init__(
        self,
        fetch: Optional[Fetch] = None,
        now: Optional[Callable[[], datetime]] = None,
        monotonic: Optional[Callable[[], float]] = None,
    ) -> None:
        super().__init__()
        self._fetch = fetch or _requests_fetch
        self._now = now or (lambda: datetime.now(timezone.utc).replace(microsecond=0))
        self._monotonic = monotonic or (lambda: _monotonic())

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        start = time.time()
        try:
            if inputs.get("asset_manifest_path"):
                manifest_path = Path(inputs["asset_manifest_path"])
            elif inputs.get("project_dir"):
                manifest_path = Path(inputs["project_dir"]) / "artifacts" / "asset_manifest.json"
            else:
                return ToolResult(
                    success=False,
                    error="license_resolver requires project_dir or asset_manifest_path",
                )
            if not manifest_path.is_file():
                return ToolResult(success=False, error=f"asset_manifest not found: {manifest_path}")
            original_bytes = manifest_path.read_bytes()
            manifest = json.loads(original_bytes.decode("utf-8"))
            if not isinstance(manifest, dict) or not isinstance(manifest.get("assets"), list):
                return ToolResult(
                    success=False,
                    error=f"asset_manifest {manifest_path} has no 'assets' list; nothing to resolve.",
                )

            asset_ids = inputs.get("asset_ids")
            if asset_ids is not None:
                if not isinstance(asset_ids, list) or not all(isinstance(i, str) for i in asset_ids):
                    return ToolResult(success=False, error="asset_ids must be a list of strings")
                known = {str(a.get("id")) for a in manifest["assets"] if isinstance(a, dict)}
                missing = [i for i in asset_ids if i not in known]
                if missing:
                    return ToolResult(
                        success=False,
                        error=f"asset_ids not in asset_manifest: {', '.join(missing)}",
                    )
            wanted = set(asset_ids) if asset_ids is not None else None
            only_unresolved = inputs.get("only_unresolved") is not False
            write_back = inputs.get("write_back") is not False
            ctx = _Context(
                fetch=self._fetch,
                headers={"User-Agent": str(inputs.get("user_agent") or _DEFAULT_USER_AGENT)},
                timeout=float(inputs.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                deadline_seconds=float(inputs.get("deadline_seconds") or DEFAULT_DEADLINE_SECONDS),
                monotonic=self._monotonic,
            )

            updated = copy.deepcopy(manifest)
            counts = {STATUS_RESOLVED: 0, STATUS_UNRESOLVED: 0, STATUS_UNCHANGED: 0, "errors": 0, STATUS_SKIPPED: 0}
            entries: list[dict[str, Any]] = []
            notes: list[str] = []
            for asset in updated["assets"]:
                if not isinstance(asset, dict):
                    continue
                if wanted is not None and str(asset.get("id")) not in wanted:
                    counts[STATUS_SKIPPED] += 1
                    continue
                if only_unresolved and not needs_resolution(asset):
                    counts[STATUS_SKIPPED] += 1
                    continue
                reason = skip_reason(asset, explicitly_named=wanted is not None)
                if reason:
                    counts[STATUS_SKIPPED] += 1
                    entries.append(_skipped_entry(asset, reason))
                    continue
                entry = self._resolve_asset(asset, ctx, notes)
                counts["errors" if entry["status"] == STATUS_ERROR else entry["status"]] += 1
                entries.append(entry)

            first_backup: Optional[Path] = None
            backup_path: Optional[Path] = None
            written = False
            if write_back and updated != manifest:
                if _schema_valid(manifest) and not _schema_valid(updated):
                    return ToolResult(
                        success=False,
                        error=(
                            "refusing to write: the resolved manifest no longer validates against "
                            "asset_manifest.schema.json"
                        ),
                        data={"counts": counts, "assets": entries, "notes": notes},
                    )
                first_backup, backup_path = write_manifest_atomically(
                    manifest_path, updated, original_bytes, self._now()
                )
                written = True
            elif manifest_path.with_name(manifest_path.name + ".bak").exists():
                first_backup = manifest_path.with_name(manifest_path.name + ".bak")

            return ToolResult(
                success=True,
                data={
                    "counts": counts,
                    "assets": entries,
                    "notes": notes,
                    "manifest_path": str(manifest_path),
                    "backup_path": str(backup_path) if backup_path else None,
                    "first_backup_path": str(first_backup) if first_backup else None,
                    "written": written,
                },
                artifacts=[str(manifest_path)] if written else [],
                cost_usd=0.0,
                duration_seconds=round(time.time() - start, 3),
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=_redact(f"{type(e).__name__}: {e}"),
                duration_seconds=round(time.time() - start, 3),
            )

    # ------------------------------------------------------------------
    # Per asset
    # ------------------------------------------------------------------

    def _resolve_asset(
        self, asset: dict[str, Any], ctx: _Context, notes: list[str]
    ) -> dict[str, Any]:
        asset_id = str(asset.get("id") or "")
        before = str(asset.get("license") or "")
        entry: dict[str, Any] = {
            "id": asset_id,
            "provider": str(asset.get("provider") or ""),
            "status": STATUS_UNRESOLVED,
            "before": before,
            "after": before,
            "license_class": classify_license(before) if before.strip() else "unknown",
            "method": None,
            "reason": None,
            "evidence_url": None,
        }
        ctx.deadline_at = ctx.monotonic() + ctx.deadline_seconds
        try:
            resolution = resolve(asset, ctx)
        except _Unresolved as e:
            entry["reason"] = _redact(str(e))
            return entry
        except Exception as e:  # _SourceError, transport and parse failures
            text = str(e) if isinstance(e, _SourceError) else f"{type(e).__name__}: {e}"
            entry.update(status=STATUS_ERROR, reason=_redact(text))
            return entry

        after = resolution.license.strip()
        same = _normalise(before) == _normalise(after)
        disagrees = bool(before.strip()) and not same
        if disagrees and not resolution.authoritative and _normalise(before) not in _PLACEHOLDER_LICENSES:
            label = "site policy" if resolution.method == METHOD_SITE_POLICY else "collection heuristic"
            entry["reason"] = (
                f"{label} cannot override an item-level caveat: the manifest licence {before!r} "
                f"is kept (the {label} says {after!r})"
            )
            return entry

        existing = asset.get("license_evidence") if isinstance(asset.get("license_evidence"), dict) else None
        history = _previous_licenses(existing)
        if disagrees:
            if not history or history[-1] != before:
                history.append(before)
            note = _redact(
                f"{asset_id}: manifest licence {before!r} disagrees with the source "
                f"({resolution.url}: {_clip(resolution.matched_text)!r}); set to the source value {after!r}."
            )
            notes.append(note)
            entry["notes"] = [note]

        if same and existing is not None and all(isinstance(existing.get(k), str) for k in _EVIDENCE_REQUIRED):
            # Unchanged: keep the evidence already recorded (no fetched_at churn).
            evidence = {k: v for k, v in existing.items() if k not in ("previous_license", "previous_licenses")}
            license_text = before
        else:
            evidence = {
                "url": resolution.url,
                "method": resolution.method,
                "matched_text": _clip(resolution.matched_text),
                "fetched_at": self._now().isoformat(),
                "resolver_version": self.version,
            }
            license_text = before if same and before.strip() else after
        if history:
            evidence["previous_licenses"] = history

        fields: dict[str, Any] = {
            "license": license_text,
            "license_class": classify_license(license_text),
            "license_evidence": evidence,
        }
        if resolution.creator and not str(asset.get("creator") or "").strip():
            fields["creator"] = resolution.creator
        schema_error = _fields_schema_error(fields)
        if schema_error:
            entry.update(
                status=STATUS_ERROR,
                reason=_redact(f"resolved fields fail the asset_manifest schema, not written: {schema_error}"),
            )
            return entry

        asset.update(fields)
        entry.update(
            status=STATUS_RESOLVED if (disagrees or not before.strip()) else STATUS_UNCHANGED,
            after=license_text,
            license_class=fields["license_class"],
            method=resolution.method,
            evidence_url=resolution.url,
        )
        return entry


# ----------------------------------------------------------------------
# Selection and routing (module-level so tests can hit them directly)
# ----------------------------------------------------------------------


def needs_resolution(asset: dict[str, Any]) -> bool:
    """Default `only_unresolved` selection."""
    license_text = " ".join(str(asset.get("license") or "").lower().split())
    if not license_text:
        return True
    if classify_license(license_text) == LICENSE_NOT_CLEARED and _VERIFY_RE.search(license_text):
        return True
    return route_for(asset) in _ALWAYS_RECHECK


def skip_reason(asset: dict[str, Any], explicitly_named: bool = False) -> Optional[str]:
    """Why a selected asset must not be resolved, or None."""
    license_text = " ".join(str(asset.get("license") or "").split())
    subtype = str(asset.get("subtype") or "").strip().lower()
    if license_text and (
        subtype in ("user_supplied", "library")
        or any(p.search(license_text.lower()) for p in _USER_SUPPLIED_PATTERNS)
    ):
        return USER_DECLARED_REASON
    if not explicitly_named:
        license_class = classify_asset(asset)
        if license_class in (LICENSE_GENERATED, LICENSE_USER_SUPPLIED):
            return (
                f"classified {license_class} by media_license_report, so there is no source licence "
                "to check; name it in asset_ids to resolve it anyway"
            )
    return None


def route_for(asset: dict[str, Any]) -> str:
    """Source key: provider field first, then original_url host (page/API sources only); "" if none."""
    provider = str(asset.get("provider") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if provider in _PROVIDER_ALIASES:
        return _PROVIDER_ALIASES[provider]
    host = _host(asset.get("original_url"))
    for key, domains in _SOURCE_HOSTS.items():
        if key in _HOST_ROUTABLE and any(_host_matches(host, d) for d in domains):
            return key
    return ""


def resolve(asset: dict[str, Any], ctx: _Context) -> _Resolution:
    url = str(asset.get("original_url") or "").strip()
    host = _host(url)
    route = route_for(asset)
    if not route:
        provider = str(asset.get("provider") or "")
        for key in _SITE_POLICIES:
            if any(_host_matches(host, d) for d in _SOURCE_HOSTS[key]):
                raise _Unresolved(
                    f"host {host!r} also serves media under other licences; its site-wide "
                    f"licence applies only when provider is explicitly {key!r}"
                )
        raise _Unresolved(
            f"no licence resolver for provider {provider!r} "
            f"{'or host ' + repr(host) if host else '(no original_url)'}"
        )
    if route == "jaxa":
        raise _Unresolved(
            "JAXA Digital Archives publishes no machine-readable per-item licence and its terms "
            "restrict use; settle it with the rights holder."
        )
    domains = _SOURCE_HOSTS[route]
    if not any(_host_matches(host, d) for d in domains):
        scope = (
            "the site-wide licence only applies to files from the provider's own site"
            if route in _SITE_POLICIES
            else "cannot ask the source"
        )
        raise _Unresolved(
            f"original_url host {host or '(none)'!r} is not a {route} host "
            f"({', '.join(domains)}); {scope}"
        )
    if host in _EXCLUDED_HOSTS.get(route, ()):
        raise _Unresolved(f"{host} serves media under a different licence than the {route} site policy")
    if route in _SITE_POLICIES:
        return _resolve_site_policy(route, asset, url)
    return _RESOLVERS[route](url, ctx)


# ----------------------------------------------------------------------
# Source strategies
# ----------------------------------------------------------------------


def _resolve_wikimedia(url: str, ctx: _Context) -> _Resolution:
    identifier = commons_identifier(url)
    if identifier is None:
        raise _Unresolved(_commons_reject_reason(url))
    key, value = identifier
    params = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "extmetadata",
        "format": "json",
        "formatversion": "2",
        key: value,
    }
    data, _ = _get_json(_COMMONS_API_URL, params, ctx)
    pages = (data.get("query") or {}).get("pages") or []
    if isinstance(pages, dict):  # formatversion=1 shape
        pages = list(pages.values())
    page = pages[0] if pages and isinstance(pages[0], dict) else {}
    if not page or page.get("missing") or page.get("invalid"):
        raise _Unresolved(f"Commons has no file page for {key}={value}")
    title = str(page.get("title") or "")
    if not title.startswith("File:"):
        raise _Unresolved(f"Commons page for {key}={value} is {title or '(untitled)'!r}, not a File: page")
    if key == "pageids" and str(page.get("pageid")) != value:
        raise _Unresolved(f"Commons returned page id {page.get('pageid')!r} for pageids={value}")
    if key == "titles" and _title_key(title) != _title_key(value):
        raise _Unresolved(f"Commons returned {title!r} for titles={value}")
    infos = page.get("imageinfo") or []
    meta = (infos[0].get("extmetadata") if infos and isinstance(infos[0], dict) else None) or {}
    short_name = wikimedia._meta_value(meta, "LicenseShortName")
    usage_terms = wikimedia._meta_value(meta, "UsageTerms")
    license_text = short_name or usage_terms
    if not license_text:
        raise _Unresolved(
            f"Commons file page {title} states no licence (LicenseShortName and UsageTerms are empty)"
        )
    artist = wikimedia._meta_value(meta, "Artist")
    matched = f"title: {title}; " + (
        f"LicenseShortName: {short_name}" if short_name else f"UsageTerms: {usage_terms}"
    )
    if artist:
        matched += f"; Artist: {artist}"
    return _Resolution(license_text, METHOD_API, _url_with(_COMMONS_API_URL, params), matched, artist)


def _resolve_archive_org(url: str, ctx: _Context) -> _Resolution:
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) < 2 or parts[0] not in ("details", "download", "metadata", "embed", "stream"):
        raise _Unresolved("original_url names no archive.org item (/details/<identifier>)")
    identifier = parts[1]
    meta_url = f"{_ARCHIVE_METADATA_URL}/{identifier}"
    data, _ = _get_json(meta_url, None, ctx)
    metadata = data.get("metadata")
    if not isinstance(metadata, dict) or not metadata:
        raise _Unresolved(f"archive.org has no metadata for item {identifier!r}")
    creator = _to_text(metadata.get("creator"))
    license_url = _to_text(metadata.get("licenseurl"))
    if license_url:
        return _Resolution(license_url, METHOD_API, meta_url, f"licenseurl: {license_url}", creator)
    collection = _to_text(metadata.get("collection"))
    license_text = _license_from_collection(collection)
    if has_restrictive_license_marker(license_text):
        raise _Unresolved(
            f"archive.org item {identifier!r} has no licenseurl and its collection(s) "
            f"({collection or 'none'}) carry no known licence"
        )
    return _Resolution(
        license_text,
        METHOD_API,
        meta_url,
        f"collection: {collection} (no licenseurl)",
        creator,
        authoritative=False,
    )


def _resolve_freesound(url: str, ctx: _Context) -> _Resolution:
    match = _FREESOUND_ID_RE.search(urlparse(url).path)
    if not match:
        raise _Unresolved("original_url names no Freesound sound id (/sounds/<id>/)")
    api_key = (os.environ.get("FREESOUND_API_KEY") or "").strip()
    if not api_key:
        raise _Unresolved("FREESOUND_API_KEY is not set; the Freesound API needs it to read a sound's licence")
    if any(ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise _Unresolved("FREESOUND_API_KEY contains control characters; it was not sent (fix the value)")
    api_url = f"{_FREESOUND_API_URL}/sounds/{match.group(1)}/"
    params = {"fields": "license,username"}
    # The token goes in a header so it never lands in the evidence URL.
    data, _ = _get_json(api_url, params, ctx, {"Authorization": f"Token {api_key}"})
    license_text = str(data.get("license") or "").strip()
    if not license_text:
        raise _Unresolved(f"Freesound returned no licence for sound {match.group(1)}")
    return _Resolution(
        license_text,
        METHOD_API,
        _url_with(api_url, params),
        f"license: {license_text}",
        str(data.get("username") or "").strip(),
    )


def _resolve_loc(url: str, ctx: _Context) -> _Resolution:
    parsed = urlparse(url)
    if not parsed.path.startswith(_LOC_PATH_PREFIXES):
        raise _Unresolved("Library of Congress original_url is not an /item/ or /resource/ page")
    item_url = f"{parsed.scheme or 'https'}://{parsed.netloc}{parsed.path}"
    params = {"fo": "json"}
    data, final_url = _get_json(item_url, params, ctx)
    if not urlparse(final_url).path.startswith(_LOC_PATH_PREFIXES):
        raise _Unresolved(f"{item_url} redirected away from an item page ({final_url})")
    item = data.get("item")
    rights = item.get("rights") if isinstance(item, dict) else None
    if isinstance(rights, list):
        rights_text = " ".join(r for r in rights if isinstance(r, str))
    else:
        rights_text = str(rights or "")
    rights_text = " ".join(rights_text.split())
    if not rights_text:
        raise _Unresolved("Library of Congress item has no rights statement")
    if not loc_rights_are_public_domain(rights_text):
        raise _Unresolved(
            f"Library of Congress rights statement is not a positive public-domain statement: "
            f"{_clip(rights_text)!r}"
        )
    return _Resolution(_LOC_LICENSE_PD, METHOD_API, _url_with(item_url, params), f"rights: {rights_text}")


def loc_rights_are_public_domain(rights_text: str) -> bool:
    """True only for an unhedged "public domain" / "no known restrictions" sentence.

    Any sentence carrying a restrictive licence marker anywhere in the
    statement also fails closed.
    """
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(rights_text) if s.strip()]
    if any(has_restrictive_license_marker(s) for s in sentences):
        return False
    return any(
        _LOC_POSITIVE_RE.search(s.lower()) and not _LOC_HEDGE_RE.search(s.lower())
        for s in sentences
    )


def _resolve_mixkit(url: str, ctx: _Context) -> _Resolution:
    parsed = urlparse(url)
    if not _MIXKIT_CLIP_PATH_RE.match(parsed.path):
        raise _Unresolved("original_url is not a Mixkit clip page (/free-stock-video/<slug>-<id>/)")
    text, final_url = _get_text(url, None, ctx)
    if urlparse(final_url).path.rstrip("/") != parsed.path.rstrip("/"):
        raise _Unresolved(f"{url} redirected to a different page ({final_url})")
    clip_id = f"https://mixkit.co{parsed.path.rstrip('/')}/#video"
    id_markers = (f'"@id":"{clip_id}"', f'"@id":"{clip_id.replace("/", chr(92) + "/")}"')
    positions = [text.find(m) for m in id_markers if m in text]
    if not positions:
        raise _Unresolved(f"Mixkit page does not describe this clip (no JSON-LD \"@id\":\"{clip_id}\")")
    start = min(positions)
    next_id = text.find('"@id"', start + 6)
    clip_object = text[start: next_id if next_id != -1 else len(text)]

    restricted = [m for m in (_MIXKIT_DATA_LICENSE_RESTRICTED, _MIXKIT_NOTICE_RESTRICTED) if m in text]
    free = [m for m in (_MIXKIT_DATA_LICENSE_FREE,) if m in text]
    if _MIXKIT_NOTICE_FREE in clip_object:
        free.append(_MIXKIT_NOTICE_FREE)
    # Restricted wins: a page showing both fails closed.
    if restricted:
        license_text, found = _MIXKIT_RESTRICTED_LICENSE, restricted
    elif free:
        license_text, found = _MIXKIT_FREE_LICENSE, free
    else:
        raise _Unresolved("Mixkit clip page carries neither the Free nor the Restricted licence marker")
    return _Resolution(license_text, METHOD_PAGE, url, " | ".join(_context(text, m) for m in found))


def _resolve_esa(url: str, ctx: _Context) -> _Resolution:
    if not urlparse(url).path.startswith(_ESA_PATH_PREFIX):
        raise _Unresolved(f"ESA original_url is not an {_ESA_PATH_PREFIX} page")
    page, final_url = _get_text(url, None, ctx)
    if not urlparse(final_url).path.startswith(_ESA_PATH_PREFIX):
        raise _Unresolved(f"{url} redirected away from {_ESA_PATH_PREFIX} ({final_url})")
    block = _ESA_LICENCE_BLOCK_RE.search(page)
    if not block:
        raise _Unresolved("ESA page has no licence block (ul.modal__meta_licence)")
    licence_item = _ESA_LICENCE_ITEM_RE.search(block.group(1))
    if not licence_item:
        raise _Unresolved("ESA licence block has no LICENCE item; credits are not read as licences")
    licence_text = _strip_html(licence_item.group(1))
    found = [name for name, pattern in _ESA_MARKERS if pattern.search(licence_text)]
    if not found:
        raise _Unresolved(f"ESA LICENCE item names no known licence: {_clip(licence_text)!r}")
    license_text = _ESA_CC_BY if _ESA_CC_BY in found else " or ".join(found)
    credit = _ESA_CREDIT_ITEM_RE.search(block.group(1))
    creator = _strip_html(credit.group(1)).split(";")[0].strip() if credit else ""
    return _Resolution(license_text, METHOD_PAGE, url, f"LICENCE: {licence_text}", creator)


def _resolve_site_policy(route: str, asset: dict[str, Any], url: str) -> _Resolution:
    licences = _SITE_POLICIES[route]
    current = str(asset.get("license") or "").strip()
    license_text = current if current in licences else licences[0]
    return _Resolution(
        license_text,
        METHOD_SITE_POLICY,
        url,
        f"{route} site-wide licence for {_host(url)}: {license_text}",
        authoritative=False,
    )


_RESOLVERS: dict[str, Callable[[str, _Context], _Resolution]] = {
    "wikimedia": _resolve_wikimedia,
    "archive_org": _resolve_archive_org,
    "freesound": _resolve_freesound,
    "loc": _resolve_loc,
    "mixkit": _resolve_mixkit,
    "esa": _resolve_esa,
}


def commons_identifier(url: str) -> Optional[tuple[str, str]]:
    """("pageids", id) or ("titles", "File:...") for a Wikimedia Commons URL; None otherwise."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path)
    if host == _COMMONS_HOST:
        query = parse_qs(parsed.query)
        curid = (query.get("curid") or [""])[0]
        if curid.isdigit():
            return "pageids", curid
        title = (query.get("title") or [""])[0]
        if title.startswith("File:"):
            return "titles", title.replace("_", " ")
        if "/wiki/File:" in path:
            return "titles", "File:" + path.split("/wiki/File:", 1)[1].replace("_", " ")
        return None
    if host == _COMMONS_UPLOAD_HOST and path.startswith(_COMMONS_UPLOAD_PREFIX):
        parts = [p for p in path[len(_COMMONS_UPLOAD_PREFIX):].split("/") if p]
        if not parts:
            return None
        # thumb/a/ab/Name.jpg/800px-Name.jpg and transcoded/a/ab/Name.webm/Name.webm.720p.vp9.webm
        # name the file second to last.
        name = parts[-2] if parts[0] in ("thumb", "transcoded") and len(parts) >= 2 else parts[-1]
        return "titles", "File:" + name.replace("_", " ")
    return None


def _commons_reject_reason(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == _COMMONS_UPLOAD_HOST:
        return (
            f"upload.wikimedia.org path {unquote(parsed.path)!r} is not under {_COMMONS_UPLOAD_PREFIX} "
            "(a file local to another wiki); only Commons files are resolved"
        )
    if host != _COMMONS_HOST:
        return (
            f"{host or '(no host)'} is not Wikimedia Commons; page ids and file names from other "
            "wikis do not identify Commons files, so Commons was not queried"
        )
    return "original_url names no Commons page id (?curid=) or File: title"


def _title_key(title: str) -> str:
    name = " ".join(title.replace("_", " ").split()).partition(":")[2].strip()
    return name[:1].upper() + name[1:]


# ----------------------------------------------------------------------
# HTTP and helpers
# ----------------------------------------------------------------------


def _requests_fetch(
    url: str, params: Optional[dict[str, Any]], headers: dict[str, str], timeout: float
) -> tuple[int, str, str]:
    """Bounded GET: `timeout` is the whole request's budget, the body is capped and typed."""
    import requests  # lazy: keeps discovery import-light

    started = _monotonic()
    with requests.get(
        url, params=params, headers=headers, timeout=(timeout, timeout), stream=True
    ) as response:
        content_type = str(response.headers.get("Content-Type") or "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if response.status_code == 200 and media_type not in ALLOWED_CONTENT_TYPES:
            raise _SourceError(f"unsupported content type {media_type or '(none)'!r} from {response.url}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise _SourceError(f"response from {response.url} exceeds {MAX_RESPONSE_BYTES} bytes")
            if _monotonic() - started > timeout:
                raise _SourceError(f"reading {response.url} exceeded its {timeout:g}s budget")
            chunks.append(chunk)
        charset = _CHARSET_RE.search(content_type)
        encoding = charset.group(1) if charset else "utf-8"
        try:
            text = b"".join(chunks).decode(encoding, errors="replace")
        except LookupError:
            text = b"".join(chunks).decode("utf-8", errors="replace")
        return response.status_code, text, str(response.url or url)


def _get_text(
    url: str, params: Optional[dict[str, Any]], ctx: _Context, extra_headers: Optional[dict[str, str]] = None
) -> tuple[str, str]:
    """(body, final_url) of a 200 response that stayed on the requested host."""
    remaining = ctx.deadline_at - ctx.monotonic()
    if remaining <= 0:
        raise _SourceError(f"per-asset deadline of {ctx.deadline_seconds:g}s exceeded before fetching {url}")
    response = ctx.fetch(url, params, {**ctx.headers, **(extra_headers or {})}, min(ctx.timeout, remaining))
    if len(response) == 2:
        status, text = response  # type: ignore[misc]
        final_url = url
    else:
        status, text, final_url = response  # type: ignore[misc]
        final_url = final_url or url
    if ctx.monotonic() > ctx.deadline_at:
        raise _SourceError(f"per-asset deadline of {ctx.deadline_seconds:g}s exceeded fetching {url}")
    if _bare_host(_host(final_url)) != _bare_host(_host(url)):
        raise _Unresolved(f"{url} redirected to another host ({_host(final_url) or 'unknown'})")
    if status != 200:
        raise _SourceError(f"HTTP {status} from {_url_with(url, params)}")
    return text, final_url


def _get_json(
    url: str, params: Optional[dict[str, Any]], ctx: _Context, extra_headers: Optional[dict[str, str]] = None
) -> tuple[dict[str, Any], str]:
    text, final_url = _get_text(url, params, ctx, extra_headers)
    try:
        data = json.loads(text)
    except ValueError as e:
        raise _SourceError(f"non-JSON response from {_url_with(url, params)}: {e}") from e
    if not isinstance(data, dict):
        raise _SourceError(f"unexpected JSON from {_url_with(url, params)} (not an object)")
    return data, final_url


def _url_with(url: str, params: Optional[dict[str, Any]]) -> str:
    return f"{url}?{urlencode(params)}" if params else url


def _host(url: Any) -> str:
    return (urlparse(str(url or "").strip()).hostname or "").lower()


def _bare_host(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _host_matches(host: str, domain: str) -> bool:
    return bool(host) and (host == domain or host.endswith("." + domain))


def _strip_html(text: str) -> str:
    return " ".join(_TAG_RE.sub(" ", html.unescape(text)).split())


def _context(text: str, marker: str, radius: int = 60) -> str:
    """The marker with a little surrounding text, tags stripped."""
    index = text.find(marker)
    snippet = text[max(0, index - radius): index + len(marker) + radius]
    return _strip_html(snippet.replace(marker, f" {marker} ")) or marker


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MATCHED_TEXT_LIMIT else text[: MATCHED_TEXT_LIMIT - 1] + "…"


def _redact(text: str) -> str:
    """Remove secret env values (raw, stripped, repr-escaped) and auth header values."""
    for name in _SECRET_ENV_VARS:
        raw = os.environ.get(name) or ""
        for secret in {raw, raw.strip()}:
            if len(secret.strip()) < 4:
                continue
            for form in (secret, secret.encode("unicode_escape").decode("ascii"), repr(secret)[1:-1]):
                text = text.replace(form, "[REDACTED]")
    return _AUTH_VALUE_RE.sub(r"\1 [REDACTED]", text)


def _previous_licenses(evidence: Optional[dict[str, Any]]) -> list[str]:
    """Append-only history, migrating the 0.1.0 `previous_license` string."""
    if not evidence:
        return []
    history = [v for v in evidence.get("previous_licenses") or [] if isinstance(v, str)] if isinstance(
        evidence.get("previous_licenses"), list
    ) else []
    legacy = evidence.get("previous_license")
    if isinstance(legacy, str) and legacy.strip() and legacy not in history:
        history.insert(0, legacy)
    return history


def _skipped_entry(asset: dict[str, Any], reason: str) -> dict[str, Any]:
    license_text = str(asset.get("license") or "")
    return {
        "id": str(asset.get("id") or ""),
        "provider": str(asset.get("provider") or ""),
        "status": STATUS_SKIPPED,
        "before": license_text,
        "after": license_text,
        "license_class": classify_asset(asset),
        "method": None,
        "reason": reason,
        "evidence_url": None,
    }


@functools.lru_cache(maxsize=1)
def _fields_validator() -> jsonschema.protocols.Validator:
    properties = load_schema("asset_manifest")["properties"]["assets"]["items"]["properties"]
    schema = {
        "type": "object",
        "properties": {name: properties[name] for name in _WRITTEN_FIELDS},
        "additionalProperties": False,
    }
    return jsonschema.Draft202012Validator(schema)


def _fields_schema_error(fields: dict[str, Any]) -> Optional[str]:
    """The resolver's own written fields checked alone, whatever the rest of the manifest holds."""
    error = jsonschema.exceptions.best_match(_fields_validator().iter_errors(fields))
    if error is None:
        return None
    where = "/".join(str(p) for p in error.absolute_path) or "(fields)"
    return f"{where}: {error.message[:200]}"


def _schema_valid(manifest: dict[str, Any]) -> bool:
    try:
        validate_artifact("asset_manifest", manifest)
    except Exception:
        return False
    return True


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new_file(path: Path, data: bytes, mode: int) -> None:
    with open(path, "xb") as f:  # never overwrites
        f.write(data)
    os.chmod(path, mode)


def write_manifest_atomically(
    path: Path, manifest: dict[str, Any], original_bytes: bytes, now: datetime
) -> tuple[Path, Path]:
    """Replace `path` with `manifest` via a same-directory temp file.

    Keeps the first `<name>.bak` (created only if absent) and writes a new
    `<stem>.<UTC>.bak` holding the manifest being replaced. Refuses with
    ManifestChangedError, writing nothing, when the file on disk no longer
    matches `original_bytes`. The original file mode is preserved.
    """
    digest = _sha256(original_bytes)
    mode = stat.S_IMODE(path.stat().st_mode)
    first_backup = path.with_name(path.name + ".bak")
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stamped = path.with_name(f"{path.stem}.{stamp}.bak")
    counter = 1
    while stamped.exists():
        stamped = path.with_name(f"{path.stem}.{stamp}-{counter}.bak")
        counter += 1

    def _check_unchanged() -> None:
        if _sha256(path.read_bytes()) != digest:
            raise ManifestChangedError(
                f"{path} changed on disk while license_resolver was running; nothing was written. "
                "Re-run the resolver on the current manifest."
            )

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    created: list[Path] = []
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        _check_unchanged()
        if not first_backup.exists():
            _write_new_file(first_backup, original_bytes, mode)
            created.append(first_backup)
        _write_new_file(stamped, original_bytes, mode)
        created.append(stamped)
        _check_unchanged()  # just before the replace
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)
        for backup in created:
            with contextlib.suppress(FileNotFoundError):
                backup.unlink()
        raise
    return first_backup, stamped
