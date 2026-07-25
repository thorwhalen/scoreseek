"""Tests for TheSessionSource (network mocked -> offline & deterministic)."""

import os

from scoreseek.base import License
from scoreseek.sources import TheSessionSource

_SEARCH_JSON = {
    "tunes": [
        {
            "id": 1,
            "name": "Cooley's",
            "url": "https://thesession.org/tunes/1",
            "type": "reel",
        }
    ]
}

_TUNE_JSON = {
    "name": "Cooley's",
    "type": "reel",
    "settings": [{"key": "Edor", "abc": "|:D2|EBBA B2 EB|~B3 A B2 EB|"}],
}


def test_search_builds_refs(monkeypatch):
    src = TheSessionSource()
    monkeypatch.setattr(src, "_get_json", lambda url: _SEARCH_JSON)
    hits = src.search("cooley")
    assert len(hits) == 1
    ref = hits[0]
    assert ref.title == "Cooley's"
    assert ref.id == "1"
    assert ref.license == License.PERMISSIVE
    assert ref.formats == ("abc",)
    assert ref.source == "thesession"


def test_search_empty_query_returns_nothing():
    assert TheSessionSource().search("") == []


def test_fetch_writes_valid_abc(monkeypatch):
    src = TheSessionSource()
    monkeypatch.setattr(src, "_get_json", lambda url: _TUNE_JSON)
    ref = src._ref(title="Cooley's", id="1")
    path = src.fetch(ref)
    try:
        assert path.endswith(".abc")
        text = open(path, encoding="utf-8").read()
        assert "T:Cooley's" in text
        assert "K:Edor" in text
        assert "M:4/4" in text  # reel -> 4/4 header injected
        assert "EBBA" in text  # the ABC body
    finally:
        os.remove(path)
