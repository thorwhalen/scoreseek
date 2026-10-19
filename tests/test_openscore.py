"""Tests for OpenScoreLiederSource (network mocked -> offline & deterministic)."""

import os

from scoreseek.base import License
from scoreseek.sources import OpenScoreLiederSource

# Real manifest shape (StrictYAML), from the live data/scores.yaml: the title
# comes from the path leaf, and nested blocks (audio:) must be skipped.
_SCORES_YAML = """# Parse this file with StrictYAML - https://github.com/crdoconnor/strictyaml
6583477:
  path: Abbott,_Jane_Bingham/_/Just_for_Today
  imslp: '#412044'
  set_id: 5106766
  lyricist_url: https://imslp.org/wiki/Category:Wilberforce,_Samuel
  language: EN
6583907:
  path: Abrams,_Harriett/_/Crazy_Jane
  imslp: '#396671'
  set_id: 5106769
  lyricist: M. Lewis
  language: EN
  audio:
  - audio_id: vonOtterForsberg2001
    audio: https://www.youtube.com/watch?v=x
6583966:
  path: Abrams,_Harriett/_/The_Orphans_Prayer
  imslp: '#436708'
  set_id: 5106769
"""
_COMPOSERS_YAML = """# Parse this file with StrictYAML
11925257:
  path: Abrams,_Harriett
  name: Harriett Abrams
  gender: Female
  born: 1758
"""


def _mock_text(url):
    return _COMPOSERS_YAML if "composers.yaml" in url else _SCORES_YAML


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
    assert ref.metadata["imslp"] == "#396671"  # quoted scalar unquoted
    assert ref.url == "https://musescore.com/openscore-lieder-corpus/scores/6583907"


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


def test_manifest_urls_are_the_yaml_ones(monkeypatch):
    # data/scores.tsv was removed upstream (404); the YAML manifests replaced it
    src = OpenScoreLiederSource()
    seen = []
    monkeypatch.setattr(
        src, "_get_text", lambda url: seen.append(url) or _mock_text(url)
    )
    src.search("Crazy Jane")
    assert all(u.endswith((".yaml",)) for u in seen)
    assert any(u.endswith("data/scores.yaml") for u in seen)


def test_title_from_path_drops_set_ordinal():
    from scoreseek.sources.openscore import _title_from_path

    assert (
        _title_from_path("Bantock,_Granville/Set/2_The_Ghost_Road") == "The Ghost Road"
    )
    assert _title_from_path("Abrams,_Harriett/_/Crazy_Jane") == "Crazy Jane"


def test_parse_flat_yaml_unquotes_and_skips_nested():
    from scoreseek.sources.openscore import _parse_flat_yaml

    parsed = _parse_flat_yaml(_SCORES_YAML)
    assert list(parsed) == ["6583477", "6583907", "6583966"]
    assert parsed["6583907"]["imslp"] == "#396671"
    assert "audio" not in parsed["6583907"]
    assert "audio_id" not in parsed["6583907"]
