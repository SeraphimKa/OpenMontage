"""Commercial-use licence gate across stock sources and freesound_music.

Covers the classifier on every licence string the adapters emit, the
central gate in corpus_builder / direct_clip_search, per-item filtering
inside the adapters that can resolve licence, and the scrape-only
adapters that must return nothing under the gate. No network.
"""
from __future__ import annotations

import ast
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.video.stock_sources as stock_sources
from lib.corpus import Corpus
from tools.audio.freesound_music import FreesoundMusic
from tools.video.corpus_builder import CorpusBuilder
from tools.video.direct_clip_search import DirectClipSearch
from tools.video.stock_sources import (
    Candidate,
    SearchFilters,
    classify_license,
    is_commercially_cleared,
)
from tools.video.stock_sources import (
    archive_org,
    coverr,
    dareful,
    esa,
    jaxa,
    loc,
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
from tools.video.stock_sources.base import has_restrictive_license_marker
from tools.video.stock_sources.archive_org import ArchiveOrgSource
from tools.video.stock_sources.loc import LibraryOfCongressSource
from tools.video.stock_sources.wikimedia import _page_to_candidate

_STOCK_DIR = Path(stock_sources.__file__).parent


# ---------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------

# Every licence string an adapter emits today, with its expected class.
_ADAPTER_LICENSES = {
    # cleared
    pexels._PEXELS_LICENSE: "cleared",
    pixabay_video._LICENSE: "cleared",
    coverr._LICENSE: "cleared",
    unsplash._UNSPLASH_LICENSE: "cleared",
    nasa._LICENSE: "cleared",
    nara._LICENSE: "cleared",
    noaa._LICENSE: "cleared",
    pond5_pd._LICENSE: "cleared",
    loc._LICENSE_PD: "cleared",
    archive_org._license_from_collection("prelinger"): "cleared",
    archive_org._license_from_collection("home_movies"): "cleared",
    # attribution
    videvo._LICENSE_ATTR: "attribution",
    videvo._LICENSE_CC: "attribution",
    dareful._LICENSE: "attribution",
    # not cleared
    loc._LICENSE_CHECK: "not_cleared",
    archive_org._license_from_collection("opensource_movies"): "not_cleared",
    mixkit._LICENSE: "not_cleared",
    esa._LICENSE: "not_cleared",
    jaxa._LICENSE: "not_cleared",
    wikimedia._COMMONS_LICENSE: "not_cleared",
}

# Per-item strings passed through from upstream APIs (Archive.org
# licenseurl, Commons LicenseShortName/UsageTerms, Freesound license).
_UPSTREAM_LICENSES = {
    "http://creativecommons.org/publicdomain/zero/1.0/": "cleared",
    "https://creativecommons.org/publicdomain/mark/1.0/": "cleared",
    "Public domain": "cleared",
    "CC0": "cleared",
    "No known copyright restrictions": "cleared",
    "Mixkit Stock Video Free License": "cleared",   # license_resolver, Free clip page
    "Mixkit Restricted License": "not_cleared",
    "http://creativecommons.org/licenses/by/3.0/": "attribution",
    "https://creativecommons.org/licenses/by/4.0/": "attribution",
    "CC BY 4.0": "attribution",
    "CC-BY-2.0": "attribution",
    "Attribution": "attribution",
    "Creative Commons Attribution 4.0": "attribution",
    "http://creativecommons.org/licenses/by-nc/3.0/": "not_cleared",
    "http://creativecommons.org/licenses/by-sa/4.0/": "not_cleared",
    "http://creativecommons.org/licenses/by-nd/4.0/": "not_cleared",
    "http://creativecommons.org/licenses/by-nc-sa/2.0/": "not_cleared",
    "http://creativecommons.org/licenses/sampling+/1.0/": "not_cleared",
    "CC BY-SA 4.0": "not_cleared",
    "CC BY-NC 2.0": "not_cleared",
    "Creative Commons Attribution-Share Alike 4.0": "not_cleared",
    "Attribution NonCommercial": "not_cleared",
    "Attribution-NoDerivs": "not_cleared",
    "GFDL": "not_cleared",
    "Personal use only": "not_cleared",
    "Free for personal use": "not_cleared",
    "Editorial use only": "not_cleared",
    "Getty Images Rights-Managed": "not_cleared",
    "rights managed": "not_cleared",
    "Creative Commons": "not_cleared",
    "": "not_cleared",
    "   ": "not_cleared",
}


@pytest.mark.parametrize(
    ("text", "expected"),
    list({**_ADAPTER_LICENSES, **_UPSTREAM_LICENSES}.items()),
)
def test_classify_license(text, expected):
    assert classify_license(text) == expected
    assert is_commercially_cleared(text) is (expected != "not_cleared")


def test_classify_license_none_fails_closed():
    assert classify_license(None) == "not_cleared"


@pytest.mark.parametrize(
    "text",
    [
        "Personal use only",
        "Free for personal use",
        "Editorial use only",
        "editorial-use",
        "Getty Images Rights-Managed",
        "rights managed",
    ],
)
def test_use_restrictions_are_explicit_restrictive_markers(text):
    # classify_license already fails closed on these as unrecognised;
    # the marker is what stops media_license_report treating them as
    # generated/library assets with no recognisable licence.
    assert has_restrictive_license_marker(text) is True
    assert classify_license(text) == "not_cleared"


def test_commercial_and_personal_use_is_not_a_restriction():
    assert has_restrictive_license_marker(coverr._LICENSE) is False
    assert classify_license(coverr._LICENSE) == "cleared"


def _module_license_constants() -> dict[str, str]:
    """Every module-level ``_*LICENSE*`` string constant in the package."""
    found: dict[str, str] = {}
    for path in sorted(_STOCK_DIR.glob("*.py")):
        if path.stem == "base":
            continue  # classifier result names, not adapter strings
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and "LICENSE" in target.id:
                    value = ast.literal_eval(node.value)
                    if isinstance(value, str):
                        found[f"{path.stem}.{target.id}"] = value
    return found


def test_every_adapter_license_constant_is_covered():
    # A new or reworded licence constant must be added to the table
    # above, so its classification is a decision rather than an accident.
    constants = _module_license_constants()
    assert constants, "no licence constants found; did the scan break?"
    missing = {k: v for k, v in constants.items() if v not in _ADAPTER_LICENSES}
    assert missing == {}


# ---------------------------------------------------------------------
# Central gate: corpus_builder and direct_clip_search
# ---------------------------------------------------------------------

_MIXED = [
    ("pd", "Public domain (U.S. federal government work)", "cleared"),
    ("ccby", "Creative Commons Attribution 4.0 (CC BY 4.0, attribution required)", "attribution"),
    ("nc", "http://creativecommons.org/licenses/by-nc/3.0/", "not_cleared"),
    ("sa", "CC BY-SA 4.0", "not_cleared"),
    ("unknown", "", "not_cleared"),
]


class _MixedSource:
    name = "mixed"

    def __init__(self):
        self.filters_seen: list[SearchFilters] = []

    def is_available(self):
        return True

    def search(self, query, filters):
        self.filters_seen.append(filters)
        return [
            Candidate(
                source=self.name,
                source_id=sid,
                source_url=f"https://example.test/{sid}",
                download_url=f"https://example.test/{sid}.mp4",
                kind="video",
                license=lic,
            )
            for sid, lic, _cls in _MIXED
        ]

    def download(self, candidate, out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"0" * 2048)
        return out_path


def _patch_sources(monkeypatch, *sources):
    monkeypatch.setattr(stock_sources, "all_sources", lambda: list(sources))
    monkeypatch.setattr(stock_sources, "available_sources", lambda: list(sources))
    monkeypatch.setattr(
        stock_sources,
        "source_summary",
        lambda: {
            "configured": len(sources),
            "total": len(sources),
            "available_source_names": [s.name for s in sources],
            "unavailable_source_names": [],
        },
    )


class _GateExcludedSource:
    """Stands in for mixkit/esa/jaxa: flagged, returns nothing."""

    name = "gated"
    licence_gate_excluded = True

    def is_available(self):
        return True

    def search(self, query, filters):
        return []

    def download(self, candidate, out_path):
        raise AssertionError("nothing to download")


def _run_corpus_builder(monkeypatch, tmp_path, *, source=None, extra_sources=(), **extra):
    source = source or _MixedSource()
    _patch_sources(monkeypatch, source, *extra_sources)
    processed: list[str] = []

    def fake_process(self, **kwargs):
        processed.append(kwargs["cand"].clip_id)
        return SimpleNamespace(clip_id=kwargs["cand"].clip_id)

    monkeypatch.setattr(CorpusBuilder, "_process_candidate", fake_process)
    monkeypatch.setattr(Corpus, "has", lambda self, clip_id: False)
    result = CorpusBuilder().execute({
        "corpus_dir": str(tmp_path / "corpus"),
        "queries": [{"query": "harbor"}],
        **extra,
    })
    return result, processed, source


def test_corpus_builder_drops_not_cleared_by_default(monkeypatch, tmp_path):
    result, processed, source = _run_corpus_builder(monkeypatch, tmp_path)

    assert result.success, result.error
    assert processed == ["mixed_pd", "mixed_ccby"]
    assert result.data["license_rejected"] == 3
    assert result.data["candidates_seen"] == 5
    assert result.data["commercial_only"] is True
    assert all(f.commercial_only for f in source.filters_seen)


def test_corpus_builder_passes_everything_when_gate_off(monkeypatch, tmp_path):
    result, processed, source = _run_corpus_builder(
        monkeypatch, tmp_path, commercial_only=False
    )

    assert result.success, result.error
    assert processed == [f"mixed_{sid}" for sid, _lic, _cls in _MIXED]
    assert result.data["license_rejected"] == 0
    assert not any(f.commercial_only for f in source.filters_seen)


def test_corpus_builder_schema_declares_gate():
    prop = CorpusBuilder.input_schema["properties"]["commercial_only"]
    assert prop["type"] == "boolean" and prop["default"] is True


def _run_direct_search(monkeypatch, tmp_path, *, extra_sources=(), **extra):
    source = _MixedSource()
    _patch_sources(monkeypatch, source, *extra_sources)
    result = DirectClipSearch().execute({
        "output_dir": str(tmp_path / "out"),
        "queries": [{"query": "harbor"}],
        "clips_per_query": 10,
        "extract_thumbnails": False,
        **extra,
    })
    return result, source


def test_direct_clip_search_drops_not_cleared_by_default(monkeypatch, tmp_path):
    result, source = _run_direct_search(monkeypatch, tmp_path)

    assert result.success, result.error
    clips = result.data["clips"]
    assert [c["clip_id"] for c in clips] == ["mixed_pd", "mixed_ccby"]
    assert [c["license_class"] for c in clips] == ["cleared", "attribution"]
    assert result.data["license_rejected"] == 3
    assert result.data["commercial_only"] is True
    assert all(f.commercial_only for f in source.filters_seen)
    assert not (tmp_path / "out" / "clips" / "mixed_nc.mp4").exists()


def test_direct_clip_search_passes_everything_when_gate_off(monkeypatch, tmp_path):
    result, _source = _run_direct_search(monkeypatch, tmp_path, commercial_only=False)

    assert result.success, result.error
    clips = result.data["clips"]
    assert [c["license_class"] for c in clips] == [cls for _sid, _lic, cls in _MIXED]
    assert result.data["license_rejected"] == 0


def test_direct_clip_search_reused_clip_carries_license_class(monkeypatch, tmp_path):
    clip = tmp_path / "out" / "clips" / "mixed_pd.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"0" * 2048)

    result, _source = _run_direct_search(monkeypatch, tmp_path)

    reused = [c for c in result.data["clips"] if c["skipped_existing"]]
    assert [(c["clip_id"], c["license_class"]) for c in reused] == [("mixed_pd", "cleared")]


def test_direct_clip_search_schema_declares_gate():
    prop = DirectClipSearch.input_schema["properties"]["commercial_only"]
    assert prop["type"] == "boolean" and prop["default"] is True


# ---------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------


def _forbid_network(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("network call made under the commercial gate")

    requests_stub = types.ModuleType("requests")
    requests_stub.get = boom
    monkeypatch.setitem(sys.modules, "requests", requests_stub)
    bs4_stub = types.ModuleType("bs4")
    bs4_stub.BeautifulSoup = object
    monkeypatch.setitem(sys.modules, "bs4", bs4_stub)


@pytest.mark.parametrize("source_name", ["mixkit", "esa", "jaxa"])
def test_unverifiable_sources_return_nothing_under_gate(source_name, monkeypatch):
    _forbid_network(monkeypatch)
    source = stock_sources.get_source(source_name)

    assert source.search("rocket launch", SearchFilters(kind="any")) == []


@pytest.mark.parametrize("source_name", ["mixkit", "esa", "jaxa"])
def test_unverifiable_sources_still_search_with_gate_off(source_name, monkeypatch):
    _forbid_network(monkeypatch)
    source = stock_sources.get_source(source_name)

    with pytest.raises(AssertionError, match="network call"):
        source.search("rocket launch", SearchFilters(kind="any", commercial_only=False))


def _commons_page(license_short_name: str) -> dict:
    return {
        "pageid": 42,
        "title": "File:Harbor.webm",
        "imageinfo": [{
            "mime": "video/webm",
            "url": "https://upload.example.test/Harbor.webm",
            "width": 1280,
            "height": 720,
            "duration": 10,
            "extmetadata": {"LicenseShortName": {"value": license_short_name}},
        }],
    }


@pytest.mark.parametrize(
    ("license_name", "kept"),
    [
        ("CC BY-SA 4.0", False),
        ("CC BY-NC 2.0", False),
        ("", False),                # falls back to "verify per-file"
        ("CC BY 4.0", True),
        ("Public domain", True),
        ("CC0", True),
    ],
)
def test_wikimedia_skips_not_cleared_files(license_name, kept):
    page = _commons_page(license_name)

    gated = _page_to_candidate(page, SearchFilters(kind="video"))
    assert (gated is not None) is kept
    ungated = _page_to_candidate(page, SearchFilters(kind="video", commercial_only=False))
    assert ungated is not None


def _loc_item(rights: str) -> dict:
    return {
        "id": "https://www.loc.gov/item/123/",
        "title": "Harbor film",
        "rights": [rights],
        "resources": [{"files": [[{
            "url": "https://tile.loc.gov/harbor.mp4",
            "mimetype": "video/mp4",
        }]]}],
    }


def test_loc_skips_items_needing_per_item_verification():
    src = LibraryOfCongressSource()
    varies = _loc_item("Rights assessment is your responsibility.")
    pd = _loc_item("No known restrictions on publication.")

    assert src._extract_candidates(varies, "video", SearchFilters()) == []
    assert len(src._extract_candidates(pd, "video", SearchFilters())) == 1
    assert len(
        src._extract_candidates(varies, "video", SearchFilters(commercial_only=False))
    ) == 1


@pytest.mark.parametrize(
    ("doc_license", "kept"),
    [
        ({"collection": "prelinger"}, True),
        ({"collection": "home_movies"}, True),
        ({"collection": "opensource_movies"}, False),
        ({"collection": "opensource_movies",
          "licenseurl": "http://creativecommons.org/publicdomain/zero/1.0/"}, True),
        ({"collection": "opensource_movies",
          "licenseurl": "http://creativecommons.org/licenses/by/3.0/"}, True),
        ({"collection": "opensource_movies",
          "licenseurl": "http://creativecommons.org/licenses/by-nc/3.0/"}, False),
    ],
)
def test_archive_org_gates_before_metadata_fetch(doc_license, kept, monkeypatch):
    calls: list[str] = []

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"files": [{
                "name": "harbor.mp4", "format": "h.264", "length": "12",
                "width": "640", "height": "480", "size": "1000000",
            }]}

    def fake_get(url, *_args, **_kwargs):
        calls.append(url)
        return _Resp()

    requests_stub = types.ModuleType("requests")
    requests_stub.get = fake_get
    monkeypatch.setitem(sys.modules, "requests", requests_stub)

    doc = {"identifier": "Harbor1950", **doc_license}
    cand = ArchiveOrgSource()._hydrate_candidate(doc, SearchFilters())

    assert (cand is not None) is kept
    assert bool(calls) is kept
    if kept:
        assert is_commercially_cleared(cand.license)

    ungated = ArchiveOrgSource()._hydrate_candidate(doc, SearchFilters(commercial_only=False))
    assert ungated is not None


# ---------------------------------------------------------------------
# freesound_music
# ---------------------------------------------------------------------


def test_freesound_filter_adds_license_clause_by_default():
    params = FreesoundMusic()._search_params(
        {"query": "ambient", "min_duration": 30, "max_duration": 120}, "key"
    )

    assert params["filter"] == (
        'duration:[30 TO 120] license:("Creative Commons 0" OR "Attribution")'
    )
    assert "license" in params["fields"].split(",")
    assert "username" in params["fields"].split(",")


def test_freesound_filter_omits_license_clause_when_gate_off():
    params = FreesoundMusic()._search_params(
        {"query": "ambient", "min_duration": 5, "max_duration": 60, "commercial_only": False},
        "key",
    )

    assert params["filter"] == "duration:[5 TO 60]"


_SOUNDS = [
    {"id": 1, "name": "nc drone", "username": "alice",
     "license": "http://creativecommons.org/licenses/by-nc/4.0/"},
    {"id": 2, "name": "cc0 pad", "username": "bob",
     "license": "http://creativecommons.org/publicdomain/zero/1.0/"},
]


def _run_freesound(monkeypatch, tmp_path, **extra):
    monkeypatch.setenv("FREESOUND_API_KEY", "test-key")
    tool = FreesoundMusic()
    monkeypatch.setattr(tool, "_search", lambda inputs, api_key: list(_SOUNDS))
    monkeypatch.setattr(
        tool, "_download", lambda sound, inputs, api_key: tmp_path / f"{sound['id']}.mp3"
    )
    return tool.execute({"query": "ambient", **extra})


def test_freesound_surfaces_real_license_and_rechecks_gate(monkeypatch, tmp_path):
    result = _run_freesound(monkeypatch, tmp_path)

    assert result.success, result.error
    assert result.data["sound_id"] == 2
    assert result.data["license"] == "http://creativecommons.org/publicdomain/zero/1.0/"
    assert result.data["license_class"] == "cleared"
    assert result.data["username"] == "bob"
    assert result.data["commercial_only"] is True


def test_freesound_gate_off_takes_top_result(monkeypatch, tmp_path):
    result = _run_freesound(monkeypatch, tmp_path, commercial_only=False)

    assert result.success, result.error
    assert result.data["sound_id"] == 1
    assert result.data["license_class"] == "not_cleared"
    assert result.data["username"] == "alice"


# ---------------------------------------------------------------------
# Gate edge paths (review O5)
# ---------------------------------------------------------------------


def test_direct_clip_search_never_reuses_not_cleared_clip_on_disk(monkeypatch, tmp_path):
    clips_dir = tmp_path / "out" / "clips"
    clips_dir.mkdir(parents=True)
    for sid in ("nc", "sa", "unknown"):
        (clips_dir / f"mixed_{sid}.mp4").write_bytes(b"0" * 2048)

    result, _source = _run_direct_search(monkeypatch, tmp_path, skip_existing=True)

    assert result.success, result.error
    assert [c["clip_id"] for c in result.data["clips"]] == ["mixed_pd", "mixed_ccby"]
    assert result.data["clips_reused"] == 0
    assert result.data["license_rejected"] == 3


class _AllRejectedSource(_MixedSource):
    def search(self, query, filters):
        self.filters_seen.append(filters)
        return [
            Candidate(
                source=self.name,
                source_id=f"nc{i}",
                source_url=f"https://example.test/nc{i}",
                download_url=f"https://example.test/nc{i}.mp4",
                kind="video",
                license="CC BY-NC 4.0",
            )
            for i in range(4)
        ]


def test_corpus_builder_all_rejected_is_an_empty_successful_run(monkeypatch, tmp_path):
    result, processed, _source = _run_corpus_builder(
        monkeypatch, tmp_path, source=_AllRejectedSource()
    )

    assert result.success is True, result.error
    assert result.data["clips_added"] == 0
    assert result.data["license_rejected"] == 4
    assert result.data["clips_failed"] == 0
    assert processed == []


# ---------------------------------------------------------------------
# Sources excluded outright by the gate (review O1)
# ---------------------------------------------------------------------


def test_scrape_only_adapters_are_flagged_as_gate_excluded():
    for name in ("mixkit", "esa", "jaxa"):
        assert stock_sources.get_source(name).licence_gate_excluded is True
    for name in ("pexels", "archive_org", "wikimedia", "videvo"):
        assert getattr(stock_sources.get_source(name), "licence_gate_excluded", False) is False


def test_source_catalog_reports_gate_exclusion():
    catalog = {entry["name"]: entry["licence_gate_excluded"] for entry in stock_sources.source_catalog()}

    assert catalog["mixkit"] is True
    assert catalog["esa"] is True
    assert catalog["jaxa"] is True
    assert catalog["pexels"] is False


def test_corpus_builder_reports_sources_excluded_by_gate(monkeypatch, tmp_path):
    result, _processed, _source = _run_corpus_builder(
        monkeypatch, tmp_path, extra_sources=(_GateExcludedSource(),)
    )
    assert result.success, result.error
    assert result.data["excluded_by_licence_gate"] == ["gated"]

    off, _processed, _source = _run_corpus_builder(
        monkeypatch, tmp_path, extra_sources=(_GateExcludedSource(),), commercial_only=False
    )
    assert off.data["excluded_by_licence_gate"] == []


def test_direct_clip_search_reports_sources_excluded_by_gate(monkeypatch, tmp_path):
    result, _source = _run_direct_search(
        monkeypatch, tmp_path, extra_sources=(_GateExcludedSource(),)
    )
    assert result.success, result.error
    assert result.data["excluded_by_licence_gate"] == ["gated"]

    off, _source = _run_direct_search(
        monkeypatch, tmp_path / "off", extra_sources=(_GateExcludedSource(),), commercial_only=False
    )
    assert off.data["excluded_by_licence_gate"] == []


# ---------------------------------------------------------------------
# Retrieval gate: clip_search (review O2)
# ---------------------------------------------------------------------


def _build_corpus(tmp_path) -> Path:
    """A real on-disk corpus with one row per `_MIXED` licence."""
    import numpy as np

    from lib.corpus import ClipRecord

    corp = Corpus(tmp_path / "corpus")
    rng = np.random.default_rng(7)
    for sid, lic, _cls in _MIXED:
        vec = rng.normal(size=512).astype(np.float32)
        vec /= np.linalg.norm(vec)
        corp.add(
            ClipRecord(
                clip_id=f"mixed_{sid}",
                source="mixed",
                source_id=sid,
                source_url="",
                local_path=f"clips/mixed_{sid}.mp4",
                license=lic,
            ),
            vec,
            vec,
        )
    corp.save()
    return corp.corpus_dir


_CLEARED_IDS = {"mixed_pd", "mixed_ccby"}
_ALL_IDS = {f"mixed_{sid}" for sid, _lic, _cls in _MIXED}


@pytest.fixture
def clip_corpus(tmp_path, monkeypatch):
    import numpy as np

    # rank_for_slot imports embed_texts lazily; stub the module so no
    # CLIP model loads.
    embedder = types.ModuleType("lib.clip_embedder")
    unit = np.ones((1, 512), dtype=np.float32) / np.sqrt(512)
    embedder.embed_texts = lambda texts: unit
    monkeypatch.setitem(sys.modules, "lib.clip_embedder", embedder)
    return _build_corpus(tmp_path)


def _clip_search(corpus_dir, **inputs):
    from tools.video.clip_search import ClipSearch

    result = ClipSearch().execute({"corpus_dir": str(corpus_dir), **inputs})
    assert result.success, result.error
    return result.data


def test_clip_search_rank_for_slot_gates_licence(clip_corpus):
    data = _clip_search(clip_corpus, operation="rank_for_slot", query_text="harbor", k=10)
    assert {r["record"]["clip_id"] for r in data["results"]} == _CLEARED_IDS
    assert data["license_rejected"] == 3
    assert data["commercial_only"] is True

    off = _clip_search(
        clip_corpus, operation="rank_for_slot", query_text="harbor", k=10, commercial_only=False
    )
    assert {r["record"]["clip_id"] for r in off["results"]} == _ALL_IDS
    assert off["license_rejected"] == 0


def test_clip_search_rank_for_slot_does_not_double_count_user_excludes(clip_corpus):
    data = _clip_search(
        clip_corpus,
        operation="rank_for_slot",
        query_text="harbor",
        k=10,
        exclude_ids=["mixed_nc", "mixed_pd"],
    )
    assert {r["record"]["clip_id"] for r in data["results"]} == {"mixed_ccby"}
    assert data["license_rejected"] == 2


def test_clip_search_find_similar_set_gates_licence(clip_corpus):
    data = _clip_search(clip_corpus, operation="find_similar_set", seed_clip_id="mixed_nc", n=10)
    assert {r["record"]["clip_id"] for r in data["results"]} == _CLEARED_IDS
    assert data["license_rejected"] == 2  # the not-cleared seed is not counted

    off = _clip_search(
        clip_corpus, operation="find_similar_set", seed_clip_id="mixed_nc", n=10,
        commercial_only=False,
    )
    assert {r["record"]["clip_id"] for r in off["results"]} == _ALL_IDS - {"mixed_nc"}


def test_clip_search_diversify_gates_licence(clip_corpus):
    # A not-cleared id first: Corpus.diversify always keeps the first.
    candidates = ["mixed_nc", "mixed_pd", "mixed_sa", "mixed_ccby", "mixed_unknown"]

    data = _clip_search(clip_corpus, operation="diversify", candidate_ids=candidates, n=5)
    assert set(data["kept_ids"]) == _CLEARED_IDS
    assert data["license_rejected"] == 3

    off = _clip_search(
        clip_corpus, operation="diversify", candidate_ids=candidates, n=5, commercial_only=False
    )
    assert set(off["kept_ids"]) == _ALL_IDS
    assert off["license_rejected"] == 0


def test_clip_search_get_returns_row_with_license_class(clip_corpus):
    data = _clip_search(clip_corpus, operation="get", clip_id="mixed_nc")
    assert data["found"] is True
    assert data["record"]["clip_id"] == "mixed_nc"
    assert data["license_class"] == "not_cleared"

    missing = _clip_search(clip_corpus, operation="get", clip_id="nope")
    assert missing["found"] is False


def test_clip_search_stats_counts_license_classes(clip_corpus, tmp_path):
    data = _clip_search(clip_corpus, operation="stats")
    assert data["per_license_class"] == {"cleared": 1, "attribution": 1, "not_cleared": 3}

    empty = _clip_search(tmp_path / "empty", operation="stats")
    assert empty["per_license_class"] == {}


def test_clip_search_find_similar_set_unknown_seed_reports_no_rejections(clip_corpus):
    data = _clip_search(clip_corpus, operation="find_similar_set", seed_clip_id="ghost", n=10)
    assert data["results"] == []
    assert data["license_rejected"] == 0


def test_clip_search_diversify_counts_distinct_rejected_ids(clip_corpus):
    data = _clip_search(
        clip_corpus,
        operation="diversify",
        candidate_ids=["mixed_nc", "mixed_nc", "mixed_pd", "ghost"],
        n=5,
    )
    assert data["kept_ids"] == ["mixed_pd"]
    assert data["license_rejected"] == 1


def test_clip_search_post_filters_blank_line_index_desync(clip_corpus):
    # A blank line after row 0 shifts Corpus._id_to_row (lib/corpus.py
    # Corpus.load), so Corpus.diversify maps "mixed_ccby" to the
    # not-cleared "mixed_nc" row. The post-filter must drop it.
    index = clip_corpus / "index.jsonl"
    lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
    index.write_text(lines[0] + "\n" + "".join(lines[1:]), encoding="utf-8")

    data = _clip_search(
        clip_corpus, operation="diversify", candidate_ids=["mixed_pd", "mixed_ccby"], n=5
    )
    assert "mixed_nc" not in data["kept_ids"]
    assert set(data["kept_ids"]) <= _CLEARED_IDS


# ---------------------------------------------------------------------
# commercial_only: null keeps the gate on (review 02 O2)
# ---------------------------------------------------------------------


def test_clip_search_none_flag_keeps_gate_on(clip_corpus):
    data = _clip_search(
        clip_corpus, operation="rank_for_slot", query_text="harbor", k=10, commercial_only=None
    )
    assert data["commercial_only"] is True
    assert {r["record"]["clip_id"] for r in data["results"]} == _CLEARED_IDS


def test_corpus_builder_none_flag_keeps_gate_on(monkeypatch, tmp_path):
    result, processed, source = _run_corpus_builder(monkeypatch, tmp_path, commercial_only=None)
    assert result.data["commercial_only"] is True
    assert result.data["license_rejected"] == 3
    assert processed == ["mixed_pd", "mixed_ccby"]
    assert all(f.commercial_only for f in source.filters_seen)


def test_direct_clip_search_none_flag_keeps_gate_on(monkeypatch, tmp_path):
    result, source = _run_direct_search(monkeypatch, tmp_path, commercial_only=None)
    assert result.data["commercial_only"] is True
    assert result.data["license_rejected"] == 3
    assert all(f.commercial_only for f in source.filters_seen)


def test_freesound_none_flag_keeps_gate_on(monkeypatch, tmp_path):
    params = FreesoundMusic()._search_params(
        {"query": "ambient", "min_duration": 30, "max_duration": 120, "commercial_only": None},
        "key",
    )
    assert 'license:("Creative Commons 0" OR "Attribution")' in params["filter"]

    result = _run_freesound(monkeypatch, tmp_path, commercial_only=None)
    assert result.data["commercial_only"] is True
    assert result.data["sound_id"] == 2


def test_clip_search_schema_declares_gate():
    from tools.video.clip_search import ClipSearch

    prop = ClipSearch.input_schema["properties"]["commercial_only"]
    assert prop["type"] == "boolean" and prop["default"] is True
