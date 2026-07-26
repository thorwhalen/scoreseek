"""Tests for OpenScoreLiederSource (network mocked -> offline & deterministic)."""

import os

from scoreseek.base import License
from scoreseek.sources import OpenScoreLiederSource

# Real manifest shape (header + 3 rows), from the live data/scores.tsv.
_SCORES_TSV = (
    "id\tpath\tname\tlink\timslp\tset_id\tlyricist_url\n"
    "6583477\tAbbott,_Jane_Bingham/_/Just_for_Today\tJust for Today"
    "\thttps://musescore.com/openscore-lieder-corpus/scores/6583477\t#412044\t5106766\thttps://imslp.org/x\n"
    "6583907\tAbrams,_Harriett/_/Crazy_Jane\tCrazy Jane"
    "\thttps://musescore.com/openscore-lieder-corpus/scores/6583907\t#396671\t5106769\t\n"
    "6583966\tAbrams,_Harriett/_/The_Orphans_Prayer\tThe Orphans Prayer"
    "\thttps://musescore.com/openscore-lieder-corpus/scores/6583966\t#436708\t5106769\t\n"
)
_COMPOSERS_TSV = (
    "id\tpath\tname\tgender\tborn\tdied\tdesc\tsets\tscores\tlink\twikidata\twikipedia\timslp\timage\n"
    "11925257\tAbrams,_Harriett\tHarriett Abrams\tFemale\t1758\t1821\tcomposer\t1\t2\tx\tQ1\tx\tx\tx\n"
)


def _mock_text(url):
    return _COMPOSERS_TSV if "composers.tsv" in url else _SCORES_TSV


def test_search_builds_refs(monkeypatch):
    src = OpenScoreLiederSource()
    monkeypatch.setattr(src, "_get_text", _mock_text)
    hits = src.search("Crazy Jane")
    assert len(hits) == 1
    ref = hits[0]
    assert ref.title == "Crazy Jane"
    assert ref.id == "6583907"
    assert ref.composer == "Harriett Abrams"  # joined from composers.tsv
    assert ref.license == License.CC0
    assert ref.formats == ("musicxml", "musescore")
    assert ref.source == "openscore_lieder"
    assert ref.metadata["path"] == "Abrams,_Harriett/_/Crazy_Jane"


def test_search_by_composer(monkeypatch):
    src = OpenScoreLiederSource()
    monkeypatch.setattr(src, "_get_text", _mock_text)
    hits = src.search(composer="Abrams")
    assert {h.title for h in hits} == {"Crazy Jane", "The Orphans Prayer"}


def test_search_empty_returns_all_low_ranked(monkeypatch):
    src = OpenScoreLiederSource()
    monkeypatch.setattr(src, "_get_text", _mock_text)
    hits = src.search("")
    assert len(hits) == 3  # bare listing


def test_fetch_writes_mxl_with_encoded_path(monkeypatch):
    src = OpenScoreLiederSource()
    monkeypatch.setattr(src, "_get_text", _mock_text)
    captured = {}

    def fake_bytes(url):
        captured["url"] = url
        return b"PK\x03\x04fake-mxl-zip"

    monkeypatch.setattr(src, "_get_bytes", fake_bytes)
    ref = src.search("Crazy Jane")[0]
    path = src.fetch(ref)
    try:
        assert path.endswith("lc6583907.mxl")
        assert open(path, "rb").read().startswith(b"PK\x03\x04")
        # comma in the composer folder must be percent-encoded
        assert "Abrams%2C_Harriett" in captured["url"]
        assert captured["url"].endswith("lc6583907.mxl")
    finally:
        os.remove(path)
