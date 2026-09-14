"""license_resolver: resolve blank/unverified licences from each asset's source.

Offline: every HTTP call goes through an injected fake fetch (or a fake
``requests.get`` for the default fetch). The Wikimedia, archive.org,
Mixkit and ESA fixtures are trimmed from live responses recorded on
2026-09-14; the Freesound and Library of Congress bodies are synthetic
(freesound.org was down, loc.gov was not inspected).

Tests named ``test_<item>_...`` are the regression tests for review
licence-resolver-review-01 (B1-B4, S1-S8, O1-O4).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest
import requests

from schemas.artifacts import validate_artifact
from tools.analysis import license_resolver as lr
from tools.analysis.license_resolver import LicenseResolver
from tools.tool_registry import ToolRegistry
from tools.video.stock_sources import (
    coverr,
    dareful,
    esa,
    nara,
    nasa,
    noaa,
    pexels,
    pixabay_video,
    pond5_pd,
    unsplash,
    videvo,
)
from tools.video.stock_sources.base import classify_license
from tools.video.stock_sources.loc import _LICENSE_CHECK as LOC_CHECK
from tools.video.stock_sources.loc import _LICENSE_PD as LOC_PD
from tools.video.stock_sources.mixkit import _LICENSE as MIXKIT_PLACEHOLDER

REPO_ROOT = Path(__file__).resolve().parents[2]
RAIN_CITY = REPO_ROOT / "projects" / "rain-city-montage" / "artifacts"
COMPOSE_DIRECTOR = REPO_ROOT / "skills" / "pipelines" / "documentary-montage" / "compose-director.md"
FIXED_NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 9, 15, 13, 0, 0, tzinfo=timezone.utc)
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
CURID_URL = "https://commons.wikimedia.org/?curid=43347881"

# ---------------------------------------------------------------------
# Fixtures (trimmed live responses, 2026-09-14)
# ---------------------------------------------------------------------

# commons API, pageids=43347881, iiprop=extmetadata (trimmed to licence fields)
WIKI_PUDDLES = {
    "batchcomplete": True,
    "query": {"pages": [{
        "pageid": 43347881,
        "ns": 6,
        "title": "File:Puddles of rain.webm",
        "imageinfo": [{"extmetadata": {
            "ObjectName": {"value": "Puddles of rain", "source": "mediawiki-metadata"},
            "Artist": {
                "value": "<a href=\"//commons.wikimedia.org/wiki/User:Secretlondon\" "
                         "title=\"User:Secretlondon\">Secretlondon</a>",
                "source": "commons-desc-page",
            },
            "LicenseShortName": {"value": "CC BY-SA 4.0", "source": "commons-desc-page", "hidden": ""},
            "UsageTerms": {"value": "Creative Commons Attribution-Share Alike 4.0", "source": "commons-desc-page"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0", "source": "commons-desc-page"},
        }}],
    }]},
}

# https://archive.org/metadata/203325_Marathon_Trims_R1 (trimmed; no licenseurl)
IA_PRELINGER = {
    "metadata": {
        "identifier": "203325_Marathon_Trims_R1",
        "mediatype": "movies",
        "collection": "prelinger",
        "title": "[Marathon Trims] (Reel 1)",
    },
    "files": [],
}
IA_META_URL = "https://archive.org/metadata/203325_Marathon_Trims_R1"
IA_DETAILS_URL = "https://archive.org/details/203325_Marathon_Trims_R1"

# mixkit.co/free-stock-video/forest-under-a-tropical-rain-6890/ (trimmed)
MIXKIT_RESTRICTED_PAGE = (
    '<script type="application/ld+json">{"@type":"VideoObject",'
    '"@id":"https://mixkit.co/free-stock-video/forest-under-a-tropical-rain-6890/#video",'
    '"name":"Forest under a tropical rain","copyrightNotice":"Mixkit Restricted License",'
    '"isAccessibleForFree":true,"license":"https://mixkit.co/license/#videoFree"}</script>\n'
    '<p>Free to use under the\n'
    '      <a href="/license/#videoRestricted" data-controller="license--modal" '
    'data-action="click->license--modal#open" data-license="videoRestricted">Mixkit Restricted License</a>.</p>'
)

# mixkit.co/free-stock-video/window-on-a-rainy-day-2846/ (trimmed)
MIXKIT_FREE_PAGE = (
    '<script type="application/ld+json">{"@type":"VideoObject",'
    '"@id":"https://mixkit.co/free-stock-video/window-on-a-rainy-day-2846/#video",'
    '"name":"Window on a rainy day","copyrightNotice":"Free",'
    '"isAccessibleForFree":true,"license":"https://mixkit.co/license/#videoFree"}</script>\n'
    '<p>Free to use under the\n'
    '      <a href="/license/#videoFree" data-controller="license--modal" '
    'data-action="click->license--modal#open" data-license="videoFree">Mixkit Stock Video Free License</a>.</p>'
)

# esa.int/ESA_Multimedia/Videos/2020/12/Gaia_s_stellar_motion_for_the_next_1.6_million_years (trimmed)
ESA_GAIA_PAGE = """
<div class="modal__text"><p>The stars are constantly moving across the sky.</p></div>
<ul class="modal__meta_licence">
    <li>
        <b>CREDIT</b><br>
            ESA/Gaia/DPAC; CC BY-SA 3.0 IGO. Acknowledgement: A. Brown, S. Jordan, T. Roegiers, X. Luri, E. Masana, T. Prusti and A. Moitinho.
    </li>
    <li>
        <b>LICENCE</b><br>
<a href="https://creativecommons.org/licenses/by-sa/3.0/igo/" target="_blank_">CC BY-SA 3.0 IGO</a> or <a href="/ESA_Multimedia/Terms_and_conditions_of_use_of_images_and_videos_available_on_the_esa_website" target="_blank_">ESA Standard Licence</a>	<br>(content can be used under either licence)
    </li>
</ul>
<ul class="modal__meta">
"""

ESA_GAIA_URL = "https://www.esa.int/ESA_Multimedia/Videos/2020/12/Gaia_s_stellar_motion_for_the_next_1.6_million_years"
MIXKIT_RESTRICTED_URL = "https://mixkit.co/free-stock-video/forest-under-a-tropical-rain-6890/"
MIXKIT_FREE_URL = "https://mixkit.co/free-stock-video/window-on-a-rainy-day-2846/"
LOC_ITEM = "https://www.loc.gov/item/00694116/"


class FakeFetch:
    """Maps URL -> (status, body[, final_url]) or an exception; fails on any other URL."""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = routes or {}
        self.calls: list[dict] = []

    def __call__(self, url, params, headers, timeout):
        self.calls.append({"url": url, "params": dict(params or {}), "headers": dict(headers), "timeout": timeout})
        if url not in self.routes:
            raise AssertionError(f"unexpected fetch: {url}")
        response = self.routes[url]
        if callable(response) and not isinstance(response, Exception):
            response = response()
        if isinstance(response, Exception):
            raise response
        status, body, *final = response
        text = body if isinstance(body, str) else json.dumps(body)
        return (status, text, *final)


def _asset(asset_id: str, **fields) -> dict:
    return {
        "id": asset_id,
        "type": "video",
        "path": f"projects/p/assets/video/{asset_id}.mp4",
        "source_tool": "direct_clip_search",
        "scene_id": "s01",
        **fields,
    }


def _write_manifest(tmp_path: Path, assets: list[dict], **top) -> Path:
    artifacts = tmp_path / "proj" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / "asset_manifest.json"
    path.write_text(json.dumps({"version": "1.0", "assets": assets, **top}, indent=2), encoding="utf-8")
    return path


def _run(tmp_path: Path, assets: list[dict], routes: dict | None = None, now=FIXED_NOW, **inputs):
    path = _write_manifest(tmp_path, assets)
    fetch = FakeFetch(routes)
    tool = LicenseResolver(fetch=fetch, now=lambda: now)
    result = tool.execute({"asset_manifest_path": str(path), **inputs})
    return result, fetch, path


def _entry(result, asset_id: str) -> dict:
    return next(e for e in result.data["assets"] if e["id"] == asset_id)


def _written_asset(path: Path, asset_id: str) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return next(a for a in manifest["assets"] if a["id"] == asset_id)


def _wiki_body(**page_fields) -> dict:
    body = json.loads(json.dumps(WIKI_PUDDLES))
    page = body["query"]["pages"][0]
    meta = page["imageinfo"][0]["extmetadata"]
    if "short" in page_fields:
        meta["LicenseShortName"]["value"] = page_fields.pop("short")
    page.update(page_fields)
    return body


def _mixkit_page(clip_url: str, *, notice: str | None = None, data_license: str | None = None,
                 extra: str = "") -> str:
    notice_json = f',"copyrightNotice":"{notice}"' if notice else ""
    page = (
        f'<script type="application/ld+json">{{"@type":"VideoObject","@id":"{clip_url}#video",'
        f'"name":"clip"{notice_json},"license":"https://mixkit.co/license/#videoFree"}}</script>{extra}'
    )
    if data_license:
        page += f'<a href="/license/#{data_license}" data-license="{data_license}">Mixkit licence</a>'
    return page


def _backups(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir() if p.name.endswith(".bak"))


# ---------------------------------------------------------------------
# Wikimedia
# ---------------------------------------------------------------------


def test_wikimedia_by_curid_fills_blank_licence_creator_and_evidence(tmp_path):
    asset = _asset("clip", provider="wikimedia", license="", original_url=CURID_URL)
    result, fetch, path = _run(tmp_path, [asset], {COMMONS_API: (200, WIKI_PUDDLES)})

    assert result.success, result.error
    assert result.data["counts"] == {"resolved": 1, "unresolved": 0, "unchanged": 0, "errors": 0, "skipped": 0}
    call = fetch.calls[0]
    assert call["params"]["pageids"] == "43347881"
    assert call["params"]["prop"] == "imageinfo" and call["params"]["iiprop"] == "extmetadata"
    assert call["timeout"] == 20.0
    assert call["headers"]["User-Agent"].startswith("OpenMontageBot")

    written = _written_asset(path, "clip")
    assert written["license"] == "CC BY-SA 4.0"
    assert written["license_class"] == "not_cleared"
    assert written["creator"] == "Secretlondon"
    evidence = written["license_evidence"]
    assert evidence["method"] == "api"
    assert evidence["url"].startswith(COMMONS_API + "?") and "pageids=43347881" in evidence["url"]
    assert evidence["matched_text"] == (
        "title: File:Puddles of rain.webm; LicenseShortName: CC BY-SA 4.0; Artist: Secretlondon"
    )
    assert evidence["fetched_at"] == "2026-09-14T12:00:00+00:00"
    assert evidence["resolver_version"] == LicenseResolver.version
    assert "previous_licenses" not in evidence

    assert _entry(result, "clip") == {
        "id": "clip", "provider": "wikimedia", "status": "resolved", "before": "",
        "after": "CC BY-SA 4.0", "license_class": "not_cleared", "method": "api",
        "reason": None, "evidence_url": evidence["url"],
    }


@pytest.mark.parametrize("url", [
    "https://commons.wikimedia.org/wiki/File:Puddles_of_rain.webm",
    "https://commons.wikimedia.org/w/index.php?title=File:Puddles_of_rain.webm",
    "https://upload.wikimedia.org/wikipedia/commons/5/5f/Puddles_of_rain.webm",
    "https://upload.wikimedia.org/wikipedia/commons/transcoded/5/5f/Puddles_of_rain.webm/"
    "Puddles_of_rain.webm.720p.vp9.webm",
    # S8: thumb form names the file second to last
    "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5f/Puddles_of_rain.webm/"
    "800px--Puddles_of_rain.webm.jpg",
])
def test_wikimedia_by_file_title(tmp_path, url):
    result, fetch, _ = _run(tmp_path, [_asset("clip", provider="wikimedia", original_url=url)],
                            {COMMONS_API: (200, WIKI_PUDDLES)})
    assert fetch.calls[0]["params"]["titles"] == "File:Puddles of rain.webm"
    assert "pageids" not in fetch.calls[0]["params"]
    assert _entry(result, "clip")["after"] == "CC BY-SA 4.0"


def test_wikimedia_falls_back_to_usage_terms(tmp_path):
    result, _, path = _run(tmp_path, [_asset("clip", provider="wikimedia", original_url=CURID_URL)],
                           {COMMONS_API: (200, _wiki_body(short=""))})
    written = _written_asset(path, "clip")
    assert written["license"] == "Creative Commons Attribution-Share Alike 4.0"
    assert written["license_evidence"]["matched_text"].startswith(
        "title: File:Puddles of rain.webm; UsageTerms: "
    )
    assert result.data["counts"]["resolved"] == 1


@pytest.mark.parametrize("body, reason", [
    ({"query": {"pages": [{"pageid": 0, "missing": True}]}}, "no file page"),
    ({"query": {"pages": [{"pageid": 1, "title": "File:X.webm", "imageinfo": [{"extmetadata": {}}]}]}},
     "states no licence"),
])
def test_wikimedia_unresolved_when_commons_does_not_say(tmp_path, body, reason):
    result, _, path = _run(tmp_path, [_asset("clip", provider="wikimedia",
                                             original_url="https://commons.wikimedia.org/?curid=1")],
                           {COMMONS_API: (200, body)})
    entry = _entry(result, "clip")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert result.data["written"] is False
    assert "license_evidence" not in _written_asset(path, "clip")


def test_wikimedia_url_without_identifier_is_unresolved_without_fetching(tmp_path):
    result, fetch, _ = _run(tmp_path, [_asset("clip", provider="wikimedia",
                                              original_url="https://commons.wikimedia.org/wiki/Main_Page")])
    assert fetch.calls == []
    assert "curid" in _entry(result, "clip")["reason"]


@pytest.mark.parametrize("url, reason", [
    ("https://en.wikipedia.org/w/index.php?curid=5043734", "is not Wikimedia Commons"),
    ("https://en.wikipedia.org/?curid=5043734", "is not Wikimedia Commons"),
    ("https://en.wikipedia.org/wiki/File:Example.jpg", "is not Wikimedia Commons"),
    ("https://species.wikimedia.org/?curid=9", "is not Wikimedia Commons"),
    ("https://meta.wikimedia.org/w/index.php?title=File:Example.jpg", "is not Wikimedia Commons"),
    ("https://upload.wikimedia.org/wikipedia/en/a/a9/Example.jpg", "not under /wikipedia/commons/"),
])
def test_b1_non_commons_wikimedia_urls_are_never_queried(tmp_path, url, reason):
    routes = {COMMONS_API: (200, _wiki_body(short="CC0"))}
    result, fetch, path = _run(tmp_path, [_asset("clip", provider="wikimedia", license="", original_url=url)], routes)
    entry = _entry(result, "clip")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert fetch.calls == []
    assert _written_asset(path, "clip")["license"] == ""


@pytest.mark.parametrize("url, body, reason", [
    (CURID_URL, _wiki_body(title="Main Page", ns=0), "not a File: page"),
    (CURID_URL, _wiki_body(pageid=999), "returned page id 999"),
    ("https://commons.wikimedia.org/wiki/File:Other_file.webm", WIKI_PUDDLES, "returned 'File:Puddles of rain.webm'"),
])
def test_b1_wrong_commons_page_is_unresolved(tmp_path, url, body, reason):
    result, _, path = _run(tmp_path, [_asset("clip", provider="wikimedia", license="", original_url=url)],
                           {COMMONS_API: (200, body)})
    entry = _entry(result, "clip")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert _written_asset(path, "clip")["license"] == ""


def test_b1_matched_text_names_the_file_page(tmp_path):
    _, _, path = _run(tmp_path, [_asset("clip", provider="wikimedia", original_url=CURID_URL)],
                      {COMMONS_API: (200, WIKI_PUDDLES)})
    assert _written_asset(path, "clip")["license_evidence"]["matched_text"].startswith(
        "title: File:Puddles of rain.webm;"
    )


# ---------------------------------------------------------------------
# archive.org
# ---------------------------------------------------------------------


def test_archive_org_collection_rule_when_no_licenseurl(tmp_path):
    result, _, path = _run(tmp_path, [_asset("ia", provider="archive_org", license="", original_url=IA_DETAILS_URL)],
                           {IA_META_URL: (200, IA_PRELINGER)})
    written = _written_asset(path, "ia")
    assert written["license"] == "Public Domain (Prelinger Archives)"
    assert written["license_class"] == "cleared"
    assert written["license_evidence"] == {
        "url": IA_META_URL, "method": "api", "matched_text": "collection: prelinger (no licenseurl)",
        "fetched_at": "2026-09-14T12:00:00+00:00", "resolver_version": LicenseResolver.version,
    }
    assert result.data["counts"]["resolved"] == 1


def test_archive_org_licenseurl_wins_and_creator_is_filled(tmp_path):
    # Synthetic body: same shape as the live metadata response, with a licenseurl.
    url = "https://archive.org/metadata/some_item"
    body = {"metadata": {"identifier": "some_item", "collection": ["opensource_movies"],
                         "licenseurl": "http://creativecommons.org/licenses/by/4.0/", "creator": "Jane Doe"}}
    _, _, path = _run(tmp_path, [_asset("ia", provider="archive.org", license="",
                                        original_url="https://archive.org/download/some_item/file.mp4")],
                      {url: (200, body)})
    written = _written_asset(path, "ia")
    assert written["license"] == "http://creativecommons.org/licenses/by/4.0/"
    assert written["license_class"] == "attribution"
    assert written["creator"] == "Jane Doe"
    assert written["license_evidence"]["matched_text"] == "licenseurl: http://creativecommons.org/licenses/by/4.0/"


@pytest.mark.parametrize("body, reason", [
    ({"metadata": {"identifier": "x", "collection": ["opensource_movies"]}}, "carry no known licence"),
    ({}, "no metadata"),
])
def test_archive_org_unresolved(tmp_path, body, reason):
    result, _, _ = _run(tmp_path, [_asset("ia", provider="archive_org",
                                          original_url="https://archive.org/details/x")],
                        {"https://archive.org/metadata/x": (200, body)})
    entry = _entry(result, "ia")
    assert entry["status"] == "unresolved" and reason in entry["reason"]


# ---------------------------------------------------------------------
# Freesound
# ---------------------------------------------------------------------


FREESOUND_API = "https://freesound.org/apiv2/sounds/12345/"
FREESOUND_URL = "https://freesound.org/people/rainman/sounds/12345/"
FREESOUND_BODY = {"license": "http://creativecommons.org/publicdomain/zero/1.0/", "username": "rainman"}


def test_freesound_with_key_uses_token_header_not_url(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", "secret-token")
    asset = _asset("sfx", type="sfx", provider="freesound", license="", original_url=FREESOUND_URL)
    result, fetch, path = _run(tmp_path, [asset], {FREESOUND_API: (200, FREESOUND_BODY)})

    assert fetch.calls[0]["params"] == {"fields": "license,username"}
    assert fetch.calls[0]["headers"]["Authorization"] == "Token secret-token"
    written = _written_asset(path, "sfx")
    assert written["license"] == FREESOUND_BODY["license"]
    assert written["license_class"] == "cleared"
    assert written["creator"] == "rainman"
    assert "secret-token" not in json.dumps(written) and "secret-token" not in json.dumps(result.data)
    assert written["license_evidence"]["url"] == FREESOUND_API + "?fields=license%2Cusername"


def test_freesound_without_key_is_unresolved_and_never_fetches(tmp_path, monkeypatch):
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    result, fetch, _ = _run(tmp_path, [_asset("sfx", provider="freesound",
                                              original_url="https://freesound.org/people/a/sounds/1/")])
    assert fetch.calls == []
    entry = _entry(result, "sfx")
    assert entry["status"] == "unresolved" and "FREESOUND_API_KEY" in entry["reason"]


def test_s1_freesound_key_with_trailing_newline_is_stripped(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", "secret-token-123\n")
    asset = _asset("sfx", type="sfx", provider="freesound", license="", original_url=FREESOUND_URL)
    result, fetch, _ = _run(tmp_path, [asset], {FREESOUND_API: (200, FREESOUND_BODY)})
    assert fetch.calls[0]["headers"]["Authorization"] == "Token secret-token-123"
    assert _entry(result, "sfx")["status"] == "resolved"


@pytest.mark.parametrize("key", ["secret\rtoken-123", "secret\x7ftoken-123", "secret\ntoken-123"])
def test_s1_key_with_control_characters_is_rejected_unsent(tmp_path, monkeypatch, key):
    monkeypatch.setenv("FREESOUND_API_KEY", key)
    asset = _asset("sfx", type="sfx", provider="freesound", license="", original_url=FREESOUND_URL)
    result, fetch, _ = _run(tmp_path, [asset], {FREESOUND_API: (200, FREESOUND_BODY)})
    entry = _entry(result, "sfx")
    assert entry["status"] == "unresolved" and "control characters" in entry["reason"]
    assert fetch.calls == []
    assert "token-123" not in json.dumps(result.data)


def test_s1_key_is_redacted_from_exception_text(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", "SECRETKEY123\n")
    # What requests raises for a bad header value embeds the value.
    boom = ValueError(
        "Invalid leading whitespace, reserved character(s), or return character(s) in header value: "
        "'Token SECRETKEY123\\n' (raw SECRETKEY123)"
    )
    asset = _asset("sfx", type="sfx", provider="freesound", license="", original_url=FREESOUND_URL)
    result, _, _ = _run(tmp_path, [asset], {FREESOUND_API: boom})
    entry = _entry(result, "sfx")
    assert entry["status"] == "error"
    assert "SECRETKEY123" not in json.dumps(result.data)
    assert "[REDACTED]" in entry["reason"]


# ---------------------------------------------------------------------
# Library of Congress
# ---------------------------------------------------------------------


@pytest.mark.parametrize("rights", [
    ["The Library of Congress believes this film is in the public domain."],
    "No known restrictions on publication.",
    ["Public domain."],
])
def test_loc_positive_rights_resolve_to_public_domain(tmp_path, rights):
    # Synthetic body in the ?fo=json item shape.
    asset = _asset("loc1", provider="loc", license=LOC_CHECK, original_url=LOC_ITEM + "?q=rain")
    result, fetch, path = _run(tmp_path, [asset], {LOC_ITEM: (200, {"item": {"rights": rights}})})
    assert fetch.calls[0]["params"] == {"fo": "json"}
    written = _written_asset(path, "loc1")
    assert written["license"] == LOC_PD
    assert written["license_evidence"]["matched_text"].startswith("rights: ")
    assert written["license_evidence"]["url"] == LOC_ITEM + "?fo=json"
    assert _entry(result, "loc1")["status"] == "resolved"


def test_loc_without_rights_is_unresolved(tmp_path):
    result, _, _ = _run(tmp_path, [_asset("loc1", provider="loc", original_url=LOC_ITEM)],
                        {LOC_ITEM: (200, {"item": {"title": "x"}})})
    assert "no rights statement" in _entry(result, "loc1")["reason"]


@pytest.mark.parametrize("before", [LOC_CHECK, LOC_PD, ""])
@pytest.mark.parametrize("rights", [
    "This item is not in the public domain. Permission required.",
    "This film may be in the public domain.",
    "Public domain status restricted to U.S. use.",
    "Public domain in the U.S.; all rights reserved for the soundtrack.",
    "It isn't public domain.",
    "Rights status not evaluated. Contact the rights holder.",
])
def test_b4_loc_negated_or_hedged_rights_never_write(tmp_path, before, rights):
    result, _, path = _run(tmp_path, [_asset("loc1", provider="loc", license=before, original_url=LOC_ITEM)],
                           {LOC_ITEM: (200, {"item": {"rights": [rights]}})})
    entry = _entry(result, "loc1")
    assert entry["status"] == "unresolved"
    assert "not a positive public-domain statement" in entry["reason"]
    written = _written_asset(path, "loc1")
    assert written["license"] == before
    assert "license_evidence" not in written


# ---------------------------------------------------------------------
# Mixkit and ESA pages
# ---------------------------------------------------------------------


def test_mixkit_restricted_page(tmp_path):
    asset = _asset("mk", provider="mixkit", license="", original_url=MIXKIT_RESTRICTED_URL)
    _, _, path = _run(tmp_path, [asset], {MIXKIT_RESTRICTED_URL: (200, MIXKIT_RESTRICTED_PAGE)})
    written = _written_asset(path, "mk")
    assert written["license"] == "Mixkit Restricted License"
    assert written["license_class"] == "not_cleared"
    evidence = written["license_evidence"]
    assert evidence["method"] == "page" and evidence["url"] == MIXKIT_RESTRICTED_URL
    assert 'data-license="videoRestricted"' in evidence["matched_text"]
    assert len(evidence["matched_text"]) <= 300


def test_mixkit_free_page_replaces_adapter_placeholder(tmp_path):
    asset = _asset("mk", provider="mixkit", license=MIXKIT_PLACEHOLDER, original_url=MIXKIT_FREE_URL)
    result, _, path = _run(tmp_path, [asset], {MIXKIT_FREE_URL: (200, MIXKIT_FREE_PAGE)})
    written = _written_asset(path, "mk")
    assert written["license"] == "Mixkit Stock Video Free License"
    assert written["license_class"] == classify_license("Mixkit Stock Video Free License")
    assert written["license_evidence"]["previous_licenses"] == [MIXKIT_PLACEHOLDER]
    assert _entry(result, "mk")["status"] == "resolved"


def test_mixkit_free_clip_resolves_to_cleared(tmp_path):
    assert classify_license(MIXKIT_PLACEHOLDER) == "not_cleared"
    asset = _asset("mk", provider="mixkit", license=MIXKIT_PLACEHOLDER, original_url=MIXKIT_FREE_URL)
    result, _, path = _run(tmp_path, [asset], {MIXKIT_FREE_URL: (200, MIXKIT_FREE_PAGE)})
    assert _entry(result, "mk")["license_class"] == "cleared"
    assert _written_asset(path, "mk")["license_class"] == "cleared"


def test_mixkit_restricted_wins_when_both_markers_present(tmp_path):
    page = MIXKIT_FREE_PAGE + MIXKIT_RESTRICTED_PAGE
    result, _, _ = _run(tmp_path, [_asset("mk", provider="mixkit", original_url=MIXKIT_FREE_URL)],
                        {MIXKIT_FREE_URL: (200, page)})
    assert _entry(result, "mk")["after"] == "Mixkit Restricted License"


@pytest.mark.parametrize("url, page, reason", [
    (MIXKIT_FREE_URL, _mixkit_page(MIXKIT_FREE_URL), "neither the Free nor the Restricted"),
    ("https://mixkit.co/free-stock-video/rain/", None, "not a Mixkit clip page"),
])
def test_mixkit_unresolved(tmp_path, url, page, reason):
    routes = {url: (200, page)} if page is not None else {}
    result, fetch, _ = _run(tmp_path, [_asset("mk", provider="mixkit", original_url=url)], routes)
    assert reason in _entry(result, "mk")["reason"]
    if page is None:
        assert fetch.calls == []


@pytest.mark.parametrize("kwargs, expected", [
    ({"data_license": "videoRestricted"}, "Mixkit Restricted License"),
    ({"notice": "Mixkit Restricted License"}, "Mixkit Restricted License"),
    ({"data_license": "videoFree"}, "Mixkit Stock Video Free License"),
    ({"notice": "Free"}, "Mixkit Stock Video Free License"),
])
def test_s8_each_mixkit_marker_resolves_alone(tmp_path, kwargs, expected):
    page = _mixkit_page(MIXKIT_FREE_URL, **kwargs)
    result, _, _ = _run(tmp_path, [_asset("mk", provider="mixkit", license="", original_url=MIXKIT_FREE_URL)],
                        {MIXKIT_FREE_URL: (200, page)})
    entry = _entry(result, "mk")
    assert entry["status"] == "resolved" and entry["after"] == expected


def test_s8_free_notice_of_another_clip_does_not_count(tmp_path):
    other = ('<script type="application/ld+json">{"@type":"VideoObject",'
             '"@id":"https://mixkit.co/free-stock-video/other-clip-1/#video","copyrightNotice":"Free"}</script>')
    page = _mixkit_page(MIXKIT_FREE_URL, extra=other)
    result, _, path = _run(tmp_path, [_asset("mk", provider="mixkit", license="", original_url=MIXKIT_FREE_URL)],
                           {MIXKIT_FREE_URL: (200, page)})
    assert _entry(result, "mk")["status"] == "unresolved"
    assert _written_asset(path, "mk")["license"] == ""


def test_esa_gaia_page_lists_both_alternatives_and_credit(tmp_path):
    asset = _asset("esa1", provider="esa", license="", original_url=ESA_GAIA_URL)
    _, _, path = _run(tmp_path, [asset], {ESA_GAIA_URL: (200, ESA_GAIA_PAGE)})
    written = _written_asset(path, "esa1")
    assert written["license"] == "CC BY-SA 3.0 IGO or ESA Standard Licence"
    assert written["license_class"] == "not_cleared"
    assert written["creator"] == "ESA/Gaia/DPAC"
    evidence = written["license_evidence"]
    assert evidence["method"] == "page"
    assert evidence["matched_text"].startswith("LICENCE: CC BY-SA 3.0 IGO or ESA Standard Licence")
    assert len(evidence["matched_text"]) <= 300


def test_esa_cc_by_alternative_wins(tmp_path):
    page = ESA_GAIA_PAGE.replace(
        '<a href="https://creativecommons.org/licenses/by-sa/3.0/igo/" target="_blank_">CC BY-SA 3.0 IGO</a>',
        '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>',
    )
    _, _, path = _run(tmp_path, [_asset("esa1", provider="esa", original_url=ESA_GAIA_URL)],
                      {ESA_GAIA_URL: (200, page)})
    written = _written_asset(path, "esa1")
    assert written["license"] == "CC BY 4.0"
    assert written["license_class"] == "attribution"


def test_esa_standard_licence_only_is_not_cleared(tmp_path):
    page = ('<ul class="modal__meta_licence"><li><b>CREDIT</b><br>ESA</li>'
            '<li><b>LICENCE</b><br><a href="/terms">ESA Standard Licence</a></li></ul>')
    _, _, path = _run(tmp_path, [_asset("esa1", provider="esa", original_url=ESA_GAIA_URL)],
                      {ESA_GAIA_URL: (200, page)})
    written = _written_asset(path, "esa1")
    assert written["license"] == "ESA Standard Licence"
    assert written["license_class"] == "not_cleared"
    assert written["creator"] == "ESA"


@pytest.mark.parametrize("page, reason", [
    ("<html><p>No licence block</p></html>", "no licence block"),
    ('<ul class="modal__meta_licence"><li><b>LICENCE</b><br>Contact us</li></ul>', "names no known licence"),
])
def test_esa_unresolved(tmp_path, page, reason):
    result, _, _ = _run(tmp_path, [_asset("esa1", provider="esa", original_url=ESA_GAIA_URL)],
                        {ESA_GAIA_URL: (200, page)})
    assert reason in _entry(result, "esa1")["reason"]


@pytest.mark.parametrize("page", [
    '<ul class="modal__meta_licence"><li><b>CREDIT</b><br>ESA; background image CC BY 4.0 by X</li></ul>',
    ESA_GAIA_PAGE.replace("<b>LICENCE</b>", "<strong>LICENCE</strong>").replace(
        "ESA/Gaia/DPAC; CC BY-SA 3.0 IGO", "ESA/Gaia/DPAC; star map CC BY 4.0"),
])
def test_s3_esa_without_licence_item_never_reads_credit(tmp_path, page):
    result, _, path = _run(tmp_path, [_asset("esa1", provider="esa", license="", original_url=ESA_GAIA_URL)],
                           {ESA_GAIA_URL: (200, page)})
    entry = _entry(result, "esa1")
    assert entry["status"] == "unresolved" and "no LICENCE item" in entry["reason"]
    assert _written_asset(path, "esa1")["license"] == ""


# ---------------------------------------------------------------------
# Site policies
# ---------------------------------------------------------------------


SITE_POLICY_CASES = [
    ("pexels", "https://www.pexels.com/video/rain-on-glass-1234/", pexels._PEXELS_LICENSE),
    ("pixabay_video", "https://pixabay.com/videos/rain-1234/", pixabay_video._LICENSE),
    ("pixabay", "https://pixabay.com/music/ambient-rain-1234/", pixabay_video._LICENSE),
    ("coverr", "https://coverr.co/videos/rain-abc", coverr._LICENSE),
    ("unsplash", "https://unsplash.com/photos/abc123", unsplash._UNSPLASH_LICENSE),
    ("videvo", "https://www.videvo.net/video/rain/123/", videvo._LICENSE_ATTR),
    ("dareful", "https://www.dareful.com/products/rain", dareful._LICENSE),
    ("nasa", "https://images.nasa.gov/details/abc", nasa._LICENSE),
    ("nara", "https://catalog.archives.gov/id/123", nara._LICENSE),
    ("noaa", "https://www.noaa.gov/multimedia/videos/abc", noaa._LICENSE),
    ("pond5_pd", "https://www.pond5.com/stock-footage/123", pond5_pd._LICENSE),
]


@pytest.mark.parametrize("provider, url, constant", SITE_POLICY_CASES)
def test_site_policy_sets_the_adapter_constant(tmp_path, provider, url, constant):
    result, fetch, path = _run(tmp_path, [_asset("a", provider=provider, license="", original_url=url)])
    assert fetch.calls == []
    written = _written_asset(path, "a")
    assert written["license"] == constant
    assert written["license_class"] == classify_license(constant)
    assert written["license_evidence"]["method"] == "site_policy"
    assert written["license_evidence"]["url"] == url
    assert _entry(result, "a")["status"] == "resolved"


@pytest.mark.parametrize("provider", ["", "pond5", "stock"])
def test_pond5_host_alone_never_routes_to_public_domain(tmp_path, provider):
    url = "https://www.pond5.com/stock-footage/item/123-paid-clip"
    assert lr.route_for({"provider": provider, "original_url": url}) == ""
    result, fetch, path = _run(tmp_path, [_asset("p5", provider=provider, license="", original_url=url)])
    entry = _entry(result, "p5")
    assert entry["status"] == "unresolved"
    assert "provider is explicitly 'pond5_pd'" in entry["reason"]
    assert fetch.calls == [] and result.data["written"] is False
    assert _written_asset(path, "p5")["license"] == ""
    assert lr.route_for({"provider": "pond5_pd", "original_url": url}) == "pond5_pd"


@pytest.mark.parametrize("url", [url for _, url, _ in SITE_POLICY_CASES] + [
    "https://www.videvo.net/stock-video-footage/premium/123/",
    "https://plus.unsplash.com/premium_photo-123",
    "https://catalog.archives.gov/id/123",
])
def test_b3_site_policy_hosts_never_route_without_provider(tmp_path, url):
    assert lr.route_for({"provider": "stock", "original_url": url}) == ""
    result, fetch, path = _run(tmp_path, [_asset("a", license="", original_url=url)])
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved" and "provider is explicitly" in entry["reason"]
    assert fetch.calls == []
    assert _written_asset(path, "a")["license"] == ""


def test_b3_plus_unsplash_is_excluded_even_with_provider(tmp_path):
    result, _, path = _run(tmp_path, [_asset("u", provider="unsplash", license="",
                                             original_url="https://plus.unsplash.com/premium_photo-123")])
    entry = _entry(result, "u")
    assert entry["status"] == "unresolved" and "plus.unsplash.com" in entry["reason"]
    assert _written_asset(path, "u")["license"] == ""


@pytest.mark.parametrize("url, route", [
    (CURID_URL, "wikimedia"),
    (IA_DETAILS_URL, "archive_org"),
    (FREESOUND_URL, "freesound"),
    (LOC_ITEM, "loc"),
    (MIXKIT_FREE_URL, "mixkit"),
    (ESA_GAIA_URL, "esa"),
])
def test_b3_page_and_api_sources_still_route_by_host(url, route):
    assert lr.route_for({"original_url": url}) == route


def test_site_policy_host_mismatch_is_unresolved(tmp_path):
    result, _, path = _run(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://example.com/rain.mp4")])
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved"
    assert "provider's own site" in entry["reason"]
    assert _written_asset(path, "a")["license"] == ""


def test_videvo_alternative_licence_already_recorded_is_kept(tmp_path):
    asset = _asset("v", provider="videvo", license=videvo._LICENSE_CC,
                   original_url="https://www.videvo.net/video/rain/123/")
    result, _, path = _run(tmp_path, [asset], only_unresolved=False)
    assert _entry(result, "v")["status"] == "unchanged"
    assert _written_asset(path, "v")["license"] == videvo._LICENSE_CC


@pytest.mark.parametrize("provider, url, before, only_unresolved", [
    ("pexels", "https://www.pexels.com/video/x-1/", "Pexels License - verify model release (person identifiable)", True),
    ("nara", "https://catalog.archives.gov/id/123", "Use restriction: undetermined - verify per item (donated material)", True),
    ("pexels", "https://www.pexels.com/video/x-1/", "CC BY-NC 4.0", False),
    ("nasa", "https://images.nasa.gov/details/abc", "NASA imagery; ESA logo rights-managed", False),
])
def test_b2_site_policy_cannot_override_item_caveat(tmp_path, provider, url, before, only_unresolved):
    result, _, path = _run(tmp_path, [_asset("a", provider=provider, license=before, original_url=url)],
                           only_unresolved=only_unresolved)
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved"
    assert "site policy cannot override an item-level caveat" in entry["reason"]
    written = _written_asset(path, "a")
    assert written["license"] == before and "license_evidence" not in written
    assert result.data["written"] is False


def test_b2_collection_heuristic_cannot_override_item_caveat(tmp_path):
    before = "Public Domain (Prelinger Archives) - verify talent release"
    result, _, path = _run(tmp_path, [_asset("ia", provider="archive_org", license=before, original_url=IA_DETAILS_URL)],
                           {IA_META_URL: (200, IA_PRELINGER)})
    entry = _entry(result, "ia")
    assert entry["status"] == "unresolved" and "cannot override an item-level caveat" in entry["reason"]
    assert _written_asset(path, "ia")["license"] == before


def test_b2_non_authoritative_results_may_replace_a_known_placeholder(tmp_path):
    placeholder = "Public Domain / CC (archive.org — verify per item)"
    assets = [
        _asset("ia", provider="archive_org", license=placeholder, original_url=IA_DETAILS_URL),
        _asset("px", provider="pexels", license=esa._LICENSE, original_url="https://www.pexels.com/video/x-1/"),
    ]
    result, _, path = _run(tmp_path, assets, {IA_META_URL: (200, IA_PRELINGER)})
    assert _entry(result, "ia")["after"] == "Public Domain (Prelinger Archives)"
    assert _entry(result, "px")["after"] == pexels._PEXELS_LICENSE


# ---------------------------------------------------------------------
# Page binding and fetch bounds
# ---------------------------------------------------------------------


@pytest.mark.parametrize("asset_fields, request_url, body", [
    ({"provider": "mixkit", "original_url": MIXKIT_FREE_URL}, MIXKIT_FREE_URL, MIXKIT_FREE_PAGE),
    ({"provider": "esa", "original_url": ESA_GAIA_URL}, ESA_GAIA_URL, ESA_GAIA_PAGE),
    ({"provider": "loc", "original_url": LOC_ITEM}, LOC_ITEM, {"item": {"rights": ["public domain"]}}),
    ({"provider": "wikimedia", "original_url": CURID_URL}, COMMONS_API, WIKI_PUDDLES),
])
def test_s2_redirect_to_another_host_is_unresolved(tmp_path, asset_fields, request_url, body):
    routes = {request_url: (200, body, "https://evil.example/landing")}
    result, _, path = _run(tmp_path, [_asset("a", license="", **asset_fields)], routes)
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved" and "redirected to another host" in entry["reason"]
    assert _written_asset(path, "a")["license"] == ""


@pytest.mark.parametrize("asset_fields, request_url, final_url, body, reason", [
    ({"provider": "mixkit", "original_url": MIXKIT_RESTRICTED_URL}, MIXKIT_RESTRICTED_URL,
     MIXKIT_FREE_URL, MIXKIT_FREE_PAGE, "redirected to a different page"),
    ({"provider": "mixkit", "original_url": MIXKIT_RESTRICTED_URL}, MIXKIT_RESTRICTED_URL,
     "https://mixkit.co/free-stock-video/rain/", MIXKIT_FREE_PAGE, "redirected to a different page"),
    ({"provider": "esa", "original_url": ESA_GAIA_URL}, ESA_GAIA_URL,
     "https://www.esa.int/Newsroom", ESA_GAIA_PAGE, "redirected away from /ESA_Multimedia/"),
    ({"provider": "loc", "original_url": LOC_ITEM}, LOC_ITEM,
     "https://www.loc.gov/search/?q=x", {"item": {"rights": ["public domain"]}}, "redirected away from an item page"),
])
def test_s2_same_host_redirect_off_the_item_is_unresolved(tmp_path, asset_fields, request_url, final_url, body, reason):
    result, _, path = _run(tmp_path, [_asset("a", license="", **asset_fields)], {request_url: (200, body, final_url)})
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert _written_asset(path, "a")["license"] == ""


def test_s2_mixkit_body_must_describe_the_requested_clip(tmp_path):
    # A Free clip page served under the Restricted clip's URL (soft redirect / wrong page).
    result, _, path = _run(tmp_path, [_asset("mk", provider="mixkit", license="", original_url=MIXKIT_RESTRICTED_URL)],
                           {MIXKIT_RESTRICTED_URL: (200, MIXKIT_FREE_PAGE)})
    entry = _entry(result, "mk")
    assert entry["status"] == "unresolved" and "does not describe this clip" in entry["reason"]
    assert _written_asset(path, "mk")["license"] == ""


def test_s2_final_url_on_the_same_page_is_accepted(tmp_path):
    routes = {MIXKIT_FREE_URL: (200, MIXKIT_FREE_PAGE, "https://www.mixkit.co/free-stock-video/window-on-a-rainy-day-2846")}
    result, _, _ = _run(tmp_path, [_asset("mk", provider="mixkit", license="", original_url=MIXKIT_FREE_URL)], routes)
    assert _entry(result, "mk")["status"] == "resolved"


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200,
                 url: str = "https://mixkit.co/final/", on_chunk=None):
        self.status_code = status
        self.headers = {"Content-Type": content_type}
        self.url = url
        self._body = body
        self._on_chunk = on_chunk

    @property
    def text(self):
        return self._body.decode("utf-8", "replace")

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            if self._on_chunk:
                self._on_chunk()
            yield self._body[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_requests(monkeypatch, response):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def test_s7_default_fetch_streams_and_returns_final_url(monkeypatch):
    calls = _patch_requests(monkeypatch, FakeResponse("Ата".encode("utf-8"), url="https://mixkit.co/final/"))
    assert lr._requests_fetch("https://mixkit.co/x/", None, {"User-Agent": "t"}, 5.0) == (
        200, "Ата", "https://mixkit.co/final/"
    )
    assert calls[0]["stream"] is True


@pytest.mark.parametrize("content_type", ["video/mp4", "application/octet-stream", ""])
def test_s7_default_fetch_rejects_other_content_types(monkeypatch, content_type):
    _patch_requests(monkeypatch, FakeResponse(b"\x00\x00", content_type=content_type))
    with pytest.raises(Exception, match="unsupported content type"):
        lr._requests_fetch("https://www.esa.int/ESA_Multimedia/x", None, {}, 5.0)


def test_s7_default_fetch_caps_the_body(monkeypatch):
    _patch_requests(monkeypatch, FakeResponse(b"a" * (2 * 1024 * 1024 + 1), content_type="text/plain"))
    with pytest.raises(Exception, match="exceeds 2097152 bytes"):
        lr._requests_fetch("https://www.loc.gov/item/1/", None, {}, 5.0)


def test_s7_default_fetch_enforces_its_budget_while_streaming(monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(lr, "_monotonic", lambda: clock["t"])

    def slow_drip():
        clock["t"] += 4.0

    _patch_requests(monkeypatch, FakeResponse(b"x" * 200_000, on_chunk=slow_drip))
    with pytest.raises(Exception, match="budget"):
        lr._requests_fetch("https://mixkit.co/x/", None, {}, 10.0)


def test_s7_per_asset_deadline_bounds_every_request(tmp_path):
    clock = {"t": 100.0}

    def slow_fetch(url, params, headers, timeout):
        slow_fetch.timeouts.append(timeout)
        clock["t"] += 31.0
        return 200, json.dumps(WIKI_PUDDLES)

    slow_fetch.timeouts = []
    path = _write_manifest(tmp_path, [_asset("clip", provider="wikimedia", license="", original_url=CURID_URL)])
    tool = LicenseResolver(fetch=slow_fetch, now=lambda: FIXED_NOW, monotonic=lambda: clock["t"])
    result = tool.execute({"asset_manifest_path": str(path), "timeout_seconds": 60, "deadline_seconds": 30})
    entry = _entry(result, "clip")
    assert slow_fetch.timeouts == [30.0]
    assert entry["status"] == "error" and "deadline" in entry["reason"]
    assert _written_asset(path, "clip")["license"] == ""


@pytest.mark.parametrize("provider, url, reason", [
    ("esa", "https://esamultimedia.esa.int/multimedia/videos/huge.mp4", "not an /ESA_Multimedia/ page"),
    ("esa", "https://www.esa.int/Newsroom/huge", "not an /ESA_Multimedia/ page"),
    ("loc", "https://www.loc.gov/search/?q=rain", "not an /item/ or /resource/ page"),
    ("loc", "https://tile.loc.gov/storage-services/huge.mp4", "not an /item/ or /resource/ page"),
])
def test_s7_esa_and_loc_fetch_only_item_pages(tmp_path, provider, url, reason):
    routes = {url: (200, "<html></html>"), url.split("?")[0]: (200, {"item": {"rights": ["public domain"]}})}
    result, fetch, _ = _run(tmp_path, [_asset("a", provider=provider, license="", original_url=url)], routes)
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert fetch.calls == []


# ---------------------------------------------------------------------
# Unresolved routes and errors
# ---------------------------------------------------------------------


@pytest.mark.parametrize("fields, reason", [
    ({"provider": "jaxa", "original_url": "https://jda.jaxa.jp/result.php?id=1"}, "JAXA"),
    ({"original_url": "https://jda.jaxa.jp/result.php?id=1"}, "JAXA"),
    ({"provider": "somestock", "original_url": "https://somestock.example/v/1"}, "no licence resolver"),
    ({"provider": "elevenlabs"}, "no original_url"),
    ({"provider": "wikimedia", "original_url": "https://example.com/?curid=5"}, "is not a wikimedia host"),
])
def test_unresolved_routes(tmp_path, fields, reason):
    result, fetch, _ = _run(tmp_path, [_asset("a", license="", **fields)])
    entry = _entry(result, "a")
    assert entry["status"] == "unresolved" and reason in entry["reason"]
    assert fetch.calls == []
    assert result.data["counts"]["unresolved"] == 1


def test_provider_field_wins_over_host():
    asset = {"provider": "archive_org", "original_url": "https://commons.wikimedia.org/?curid=1"}
    assert lr.route_for(asset) == "archive_org"
    assert lr.route_for({"original_url": "https://commons.wikimedia.org/?curid=1"}) == "wikimedia"


@pytest.mark.parametrize("response, reason", [
    ((503, "Service Unavailable"), "HTTP 503"),
    ((200, "<html>not json</html>"), "non-JSON"),
    (ConnectionError("boom"), "ConnectionError: boom"),
])
def test_fetch_failures_are_errors_and_write_nothing(tmp_path, response, reason):
    result, _, path = _run(tmp_path, [_asset("clip", provider="wikimedia", license="",
                                             original_url="https://commons.wikimedia.org/?curid=1")],
                           {COMMONS_API: response})
    assert result.success
    entry = _entry(result, "clip")
    assert entry["status"] == "error" and reason in entry["reason"]
    assert result.data["counts"]["errors"] == 1
    assert result.data["written"] is False and result.data["backup_path"] is None
    assert _backups(path) == []


# ---------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------


def test_only_unresolved_selection(tmp_path):
    assets = [
        _asset("cleared", provider="wikimedia", license="CC0", original_url="https://commons.wikimedia.org/?curid=1"),
        _asset("blank", provider="pexels", license="", original_url="https://www.pexels.com/video/x-1/"),
        _asset("verify", provider="archive_org", license="Public Domain / CC (archive.org — verify per item)",
               original_url=IA_DETAILS_URL),
        _asset("mixkit", provider="mixkit", license="Mixkit Restricted License", original_url=MIXKIT_RESTRICTED_URL),
        _asset("nc", provider="wikimedia", license="CC BY-NC 4.0", original_url="https://commons.wikimedia.org/?curid=2"),
    ]
    routes = {IA_META_URL: (200, IA_PRELINGER), MIXKIT_RESTRICTED_URL: (200, MIXKIT_RESTRICTED_PAGE)}
    result, fetch, _ = _run(tmp_path, assets, routes)
    assert sorted(e["id"] for e in result.data["assets"]) == ["blank", "mixkit", "verify"]
    assert result.data["counts"] == {"resolved": 2, "unresolved": 0, "unchanged": 1, "errors": 0, "skipped": 2}
    assert len(fetch.calls) == 2


def test_only_unresolved_false_rechecks_everything(tmp_path):
    assets = [_asset("cleared", provider="pexels", license=pexels._PEXELS_LICENSE,
                     original_url="https://www.pexels.com/video/x-1/")]
    result, _, _ = _run(tmp_path, assets, only_unresolved=False)
    assert result.data["counts"]["unchanged"] == 1 and result.data["counts"]["skipped"] == 0


def test_asset_ids_subset(tmp_path):
    assets = [
        _asset("a", provider="pexels", license="", original_url="https://www.pexels.com/video/x-1/"),
        _asset("b", provider="pexels", license="", original_url="https://www.pexels.com/video/x-2/"),
    ]
    result, _, path = _run(tmp_path, assets, asset_ids=["b"])
    assert [e["id"] for e in result.data["assets"]] == ["b"]
    assert result.data["counts"]["skipped"] == 1
    assert _written_asset(path, "a")["license"] == ""


def test_unknown_asset_ids_fail_before_any_fetch(tmp_path):
    result, fetch, _ = _run(tmp_path, [_asset("a", license="")], asset_ids=["a", "nope"])
    assert not result.success and "nope" in result.error
    assert fetch.calls == []


def test_inputs_are_forwarded(tmp_path):
    _, fetch, _ = _run(tmp_path, [_asset("clip", provider="wikimedia", original_url=CURID_URL)],
                       {COMMONS_API: (200, WIKI_PUDDLES)}, timeout_seconds=5, user_agent="TestAgent/1.0")
    assert fetch.calls[0]["timeout"] == 5.0
    assert fetch.calls[0]["headers"]["User-Agent"] == "TestAgent/1.0"


@pytest.mark.parametrize("inputs, error", [
    ({}, "requires project_dir or asset_manifest_path"),
    ({"project_dir": "/nonexistent/project"}, "asset_manifest not found"),
])
def test_missing_inputs(inputs, error):
    result = LicenseResolver(fetch=FakeFetch()).execute(inputs)
    assert not result.success and error in result.error


def test_project_dir_input(tmp_path):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")])
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"project_dir": str(path.parents[1])})
    assert result.success and result.data["manifest_path"] == str(path)


@pytest.mark.parametrize("fields, expected_class", [
    ({"provider": "seedance", "model": "seedance-2.5", "original_url": CURID_URL}, "generated"),
    ({"provider": "wikimedia", "prompt": "rain on glass, image-to-video", "original_url": CURID_URL}, "generated"),
    ({"provider": "nara", "subtype": "user_supplied", "original_url": "https://catalog.archives.gov/id/123"}, "user_supplied"),
    ({"provider": "pexels", "subtype": "library", "original_url": "https://www.pexels.com/video/x-1/"}, "user_supplied"),
])
def test_s5_generated_and_user_supplied_assets_are_skipped(tmp_path, fields, expected_class):
    routes = {COMMONS_API: (200, WIKI_PUDDLES)}
    result, fetch, path = _run(tmp_path, [_asset("g", license="", **fields)], routes)
    entry = _entry(result, "g")
    assert entry["status"] == "skipped"
    assert entry["license_class"] == expected_class
    assert f"classified {expected_class}" in entry["reason"]
    assert fetch.calls == []
    assert result.data["counts"]["skipped"] == 1
    assert _written_asset(path, "g")["license"] == ""


def test_s5_asset_ids_resolves_a_named_generated_asset(tmp_path):
    asset = _asset("g", provider="wikimedia", model="seedance-2.5", license="", original_url=CURID_URL)
    result, _, _ = _run(tmp_path, [asset], {COMMONS_API: (200, WIKI_PUDDLES)}, asset_ids=["g"])
    assert _entry(result, "g")["status"] == "resolved"


@pytest.mark.parametrize("fields, named", [
    ({"provider": "esa", "subtype": "library", "license": "Licensed to client by ESA, contract ESA-2026-044"}, False),
    ({"provider": "esa", "license": "Client-provided footage, ESA contract 44"}, False),
    ({"provider": "esa", "license": "Provided by the client under ESA contract 44"}, True),
    ({"provider": "esa", "subtype": "user_supplied", "license": "CC BY-SA 3.0 IGO via client"}, True),
])
def test_o3_user_declared_licence_is_never_rechecked(tmp_path, fields, named):
    asset = _asset("u", original_url=ESA_GAIA_URL, **fields)
    inputs = {"asset_ids": ["u"]} if named else {}
    result, fetch, path = _run(tmp_path, [asset], {ESA_GAIA_URL: (200, ESA_GAIA_PAGE)}, only_unresolved=False, **inputs)
    entry = _entry(result, "u")
    assert entry["status"] == "skipped" and entry["reason"] == "user-declared, not re-checked"
    assert fetch.calls == []
    written = _written_asset(path, "u")
    assert written["license"] == fields["license"] and "license_evidence" not in written


# ---------------------------------------------------------------------
# Write-back, backups and atomicity
# ---------------------------------------------------------------------


def test_write_back_false_leaves_manifest_untouched(tmp_path):
    result, _, path = _run(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")],
                           write_back=False)
    original = json.loads(path.read_text(encoding="utf-8"))
    assert original["assets"][0]["license"] == ""
    assert result.data["counts"]["resolved"] == 1
    assert result.data["written"] is False and result.data["backup_path"] is None
    assert _backups(path) == []


def test_nothing_to_change_writes_nothing(tmp_path):
    result, _, path = _run(tmp_path, [_asset("a", provider="wikimedia", license="CC0",
                                             original_url="https://commons.wikimedia.org/?curid=1")])
    assert result.data["assets"] == [] and result.data["written"] is False
    assert _backups(path) == []


def test_failed_replace_keeps_original_manifest_and_removes_temp_and_backups(tmp_path, monkeypatch):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")])
    original = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(lr.os, "replace", boom)
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})

    assert not result.success and "disk full" in result.error
    assert path.read_bytes() == original
    assert not [p for p in path.parent.iterdir() if p.suffix == ".tmp"]
    assert _backups(path) == []


def test_s4_first_backup_is_kept_and_every_change_gets_a_timestamped_backup(tmp_path):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")])
    original = path.read_bytes()
    first = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    assert first.data["written"] is True
    assert first.data["first_backup_path"] == str(path.with_name("asset_manifest.json.bak"))
    assert first.data["backup_path"] == str(path.with_name("asset_manifest.20260914T120000Z.bak"))
    assert first.artifacts == [str(path)]
    assert _backups(path) == ["asset_manifest.20260914T120000Z.bak", "asset_manifest.json.bak"]
    assert path.with_name("asset_manifest.json.bak").read_bytes() == original
    assert not [p for p in path.parent.iterdir() if p.suffix == ".tmp"]

    manifest = json.loads(path.read_bytes())
    manifest["assets"].append(_asset("b", provider="nasa", license="", original_url="https://images.nasa.gov/details/b"))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before_second = path.read_bytes()
    second = LicenseResolver(fetch=FakeFetch(), now=lambda: LATER).execute({"asset_manifest_path": str(path)})
    assert second.data["written"] is True
    assert path.with_name("asset_manifest.json.bak").read_bytes() == original  # first backup kept
    assert path.with_name("asset_manifest.20260915T130000Z.bak").read_bytes() == before_second
    assert len(_backups(path)) == 3


def test_s4_same_second_backups_do_not_collide(tmp_path):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")])
    tool = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW)
    tool.execute({"asset_manifest_path": str(path)})
    manifest = json.loads(path.read_bytes())
    manifest["assets"].append(_asset("b", provider="nasa", license="", original_url="https://images.nasa.gov/details/b"))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    second = tool.execute({"asset_manifest_path": str(path)})
    assert second.data["backup_path"].endswith("asset_manifest.20260914T120000Z-1.bak")


def test_s4_unchanged_rerun_keeps_evidence_and_does_not_rewrite(tmp_path):
    asset = _asset("mk", provider="mixkit", license=MIXKIT_PLACEHOLDER, original_url=MIXKIT_FREE_URL)
    path = _write_manifest(tmp_path, [asset])
    routes = {MIXKIT_FREE_URL: (200, MIXKIT_FREE_PAGE)}
    LicenseResolver(fetch=FakeFetch(routes), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    after_first = path.read_bytes()
    backups_after_first = _backups(path)

    again = LicenseResolver(fetch=FakeFetch(routes), now=lambda: LATER).execute({"asset_manifest_path": str(path)})
    assert _entry(again, "mk")["status"] == "unchanged"
    assert again.data["written"] is False and again.data["backup_path"] is None
    assert path.read_bytes() == after_first
    assert _backups(path) == backups_after_first
    assert _written_asset(path, "mk")["license_evidence"]["fetched_at"] == "2026-09-14T12:00:00+00:00"


def test_s4_previous_licenses_is_append_only(tmp_path):
    path = _write_manifest(tmp_path, [_asset("clip", provider="wikimedia", license="CC0", original_url=CURID_URL)])
    for body, now in ((WIKI_PUDDLES, FIXED_NOW), (_wiki_body(short="CC BY 4.0"), LATER)):
        LicenseResolver(fetch=FakeFetch({COMMONS_API: (200, body)}), now=lambda now=now: now).execute(
            {"asset_manifest_path": str(path), "only_unresolved": False}
        )
    written = _written_asset(path, "clip")
    assert written["license"] == "CC BY 4.0"
    assert written["license_evidence"]["previous_licenses"] == ["CC0", "CC BY-SA 4.0"]
    validate_artifact("asset_manifest", json.loads(path.read_text(encoding="utf-8")))


def test_s4_legacy_previous_license_string_is_migrated(tmp_path):
    legacy = {"url": "u", "method": "api", "matched_text": "t", "fetched_at": "2026-09-14T10:00:00+00:00",
              "resolver_version": "0.1.0", "previous_license": "CC0"}
    asset = _asset("clip", provider="wikimedia", license="CC BY 3.0", license_evidence=legacy, original_url=CURID_URL)
    result, _, path = _run(tmp_path, [asset], {COMMONS_API: (200, WIKI_PUDDLES)}, only_unresolved=False)
    assert _entry(result, "clip")["status"] == "resolved"
    evidence = _written_asset(path, "clip")["license_evidence"]
    assert evidence["previous_licenses"] == ["CC0", "CC BY 3.0"]
    assert "previous_license" not in evidence
    validate_artifact("asset_manifest", json.loads(path.read_text(encoding="utf-8")))


def test_s6_written_fields_are_validated_even_when_the_manifest_is_invalid(tmp_path, monkeypatch):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")],
                           unexpected_top=True)
    original = path.read_bytes()
    monkeypatch.setattr(lr, "_clip", lambda text: "x" * 400)  # breaks matched_text maxLength
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    assert result.success
    entry = _entry(result, "a")
    assert entry["status"] == "error" and "schema" in entry["reason"]
    assert result.data["counts"]["errors"] == 1
    assert path.read_bytes() == original and result.data["written"] is False


def test_s6_invalid_fields_on_a_valid_manifest_are_not_written(tmp_path, monkeypatch):
    path = _write_manifest(tmp_path, [
        _asset("a", provider="pexels", license="", original_url="https://www.pexels.com/video/x-1/"),
    ])
    original = path.read_bytes()
    monkeypatch.setattr(lr, "_clip", lambda text: "x" * 400)
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    assert _entry(result, "a")["status"] == "error"
    assert path.read_bytes() == original


def test_o1_write_back_preserves_the_file_mode(tmp_path):
    path = _write_manifest(tmp_path, [_asset("a", provider="pexels", license="",
                                             original_url="https://www.pexels.com/video/x-1/")])
    os.chmod(path, 0o640)
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    assert result.data["written"] is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_o2_manifest_edited_during_the_run_is_not_overwritten(tmp_path):
    path = _write_manifest(tmp_path, [_asset("clip", provider="wikimedia", license="", original_url=CURID_URL)])
    agent_edit = json.dumps({"version": "1.0", "assets": [_asset("clip", license="edited by agent")]})

    def editing_fetch():
        path.write_text(agent_edit, encoding="utf-8")
        return 200, WIKI_PUDDLES

    fetch = FakeFetch({COMMONS_API: editing_fetch})
    result = LicenseResolver(fetch=fetch, now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(path)})
    assert not result.success and "changed on disk" in result.error
    assert path.read_text(encoding="utf-8") == agent_edit
    assert _backups(path) == []
    assert not [p for p in path.parent.iterdir() if p.suffix == ".tmp"]


# ---------------------------------------------------------------------
# Disagreement: the source wins, the previous value is recorded
# ---------------------------------------------------------------------


def test_source_disagreement_takes_source_value_and_records_previous(tmp_path):
    asset = _asset("clip", provider="wikimedia", license="CC BY 3.0", creator="Someone Else", original_url=CURID_URL)
    path = _write_manifest(tmp_path, [asset])
    tool = LicenseResolver(fetch=FakeFetch({COMMONS_API: (200, WIKI_PUDDLES)}), now=lambda: FIXED_NOW)
    result = tool.execute({"asset_manifest_path": str(path), "only_unresolved": False})

    entry = _entry(result, "clip")
    assert entry["status"] == "resolved"
    assert entry["before"] == "CC BY 3.0" and entry["after"] == "CC BY-SA 4.0"
    assert entry["license_class"] == "not_cleared"
    assert len(result.data["notes"]) == 1
    note = result.data["notes"][0]
    assert "'CC BY 3.0'" in note and "'CC BY-SA 4.0'" in note and "LicenseShortName: CC BY-SA 4.0" in note
    assert entry["notes"] == [note]

    written = _written_asset(path, "clip")
    assert written["license"] == "CC BY-SA 4.0"
    assert written["license_evidence"]["previous_licenses"] == ["CC BY 3.0"]
    assert written["creator"] == "Someone Else"  # an existing creator is never overwritten

    again = tool.execute({"asset_manifest_path": str(path), "only_unresolved": False})
    assert _entry(again, "clip")["status"] == "unchanged"
    assert again.data["notes"] == [] and again.data["written"] is False
    assert _written_asset(path, "clip")["license_evidence"]["previous_licenses"] == ["CC BY 3.0"]


def test_agreement_ignoring_case_and_spacing_is_unchanged(tmp_path):
    asset = _asset("clip", provider="wikimedia", license="cc  by-sa 4.0", original_url=CURID_URL)
    result, _, path = _run(tmp_path, [asset], {COMMONS_API: (200, WIKI_PUDDLES)}, only_unresolved=False)
    entry = _entry(result, "clip")
    assert entry["status"] == "unchanged" and result.data["notes"] == []
    assert "previous_licenses" not in _written_asset(path, "clip")["license_evidence"]


# ---------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------


def test_written_manifest_validates_for_every_method(tmp_path, monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", "k-test")
    assets = [
        _asset("wiki", provider="wikimedia", license="", original_url=CURID_URL),
        _asset("ia", provider="archive_org", license="", original_url=IA_DETAILS_URL),
        _asset("fs", type="sfx", provider="freesound", license="", original_url="https://freesound.org/people/a/sounds/7/"),
        _asset("loc", provider="loc", license="", original_url=LOC_ITEM),
        _asset("mk", provider="mixkit", license="", original_url=MIXKIT_FREE_URL),
        _asset("esa", provider="esa", license="", original_url=ESA_GAIA_URL),
        _asset("px", provider="pexels", license="", original_url="https://www.pexels.com/video/x-1/"),
        _asset("jx", provider="jaxa", license="", original_url="https://jda.jaxa.jp/x"),
    ]
    routes = {
        COMMONS_API: (200, WIKI_PUDDLES),
        IA_META_URL: (200, IA_PRELINGER),
        "https://freesound.org/apiv2/sounds/7/": (200, {"license": "Attribution", "username": "a"}),
        LOC_ITEM: (200, {"item": {"rights": ["public domain"]}}),
        MIXKIT_FREE_URL: (200, MIXKIT_FREE_PAGE),
        ESA_GAIA_URL: (200, ESA_GAIA_PAGE),
    }
    result, _, path = _run(tmp_path, assets, routes)
    assert result.data["counts"] == {"resolved": 7, "unresolved": 1, "unchanged": 0, "errors": 0, "skipped": 0}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_artifact("asset_manifest", manifest)
    methods = {a["license_evidence"]["method"] for a in manifest["assets"] if "license_evidence" in a}
    assert methods == {"api", "page", "site_policy"}


@pytest.mark.parametrize("mutate", [
    lambda e: e.update(extra="x"),
    lambda e: e.update(method="guess"),
    lambda e: e.update(matched_text="x" * 301),
    lambda e: e.pop("fetched_at"),
    lambda e: e.update(previous_license="CC0"),
    lambda e: e.update(previous_licenses="CC0"),
])
def test_schema_rejects_malformed_evidence(mutate):
    evidence = {"url": "u", "method": "api", "matched_text": "t", "fetched_at": "2026-09-14T12:00:00+00:00",
                "resolver_version": "0.1.0"}
    mutate(evidence)
    manifest = {"version": "1.0", "assets": [_asset("a", license_evidence=evidence)]}
    with pytest.raises(jsonschema.ValidationError):
        validate_artifact("asset_manifest", manifest)


# ---------------------------------------------------------------------
# Docs, realistic fixture and registry
# ---------------------------------------------------------------------


def test_o4_compose_director_lists_site_policy_resolutions_separately():
    text = COMPOSE_DIRECTOR.read_text(encoding="utf-8")
    step = text.split("### 7b.", 1)[1].split("### 8.", 1)[0]
    assert "method: site_policy" in step
    assert "no page or API" in step


def test_rain_city_default_run_needs_no_fetches(tmp_path):
    if not RAIN_CITY.is_dir():
        pytest.skip("projects/rain-city-montage fixture not present")
    manifest = tmp_path / "asset_manifest.json"
    shutil.copy2(RAIN_CITY / "asset_manifest.json", manifest)
    original = manifest.read_bytes()
    result = LicenseResolver(fetch=FakeFetch(), now=lambda: FIXED_NOW).execute({"asset_manifest_path": str(manifest)})
    assert result.success
    assert result.data["counts"]["skipped"] == 11 and result.data["assets"] == []
    assert manifest.read_bytes() == original


def test_registry_discovers_resolver_with_contract_metadata():
    registry = ToolRegistry()
    assert registry.register_module(lr) == ["license_resolver"]
    info = registry.get("license_resolver").get_info()
    assert info["tier"] == "analyze"
    assert info["runtime"] == "api"
    assert info["resource_profile"]["network_required"] is True
    assert info["agent_skills"] == []
    assert registry.get("license_resolver").estimate_cost({}) == 0.0
