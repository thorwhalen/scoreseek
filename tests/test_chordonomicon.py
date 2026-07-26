"""Tests for ChordonomiconSource (datasets-server API mocked -> offline)."""

import os

import pytest

from scoreseek.base import License
from scoreseek.sources import ChordonomiconSource

_SEARCH = {
    "rows": [
        {
            # row_idx is deliberately NOT id-1, to prove the URL uses the
            # authoritative server row_idx (finding #6), not int(id)-1.
            "row_idx": 298505,
            "row": {
                "id": 2,
                "chords": "<intro_1> E D A/Cs E D A/Cs <verse_1> E D A/Cs",
                "genres": "'nu metal' 'pop rock'",
                "main_genre": "metal",
                "rock_genre": "pop rock",
                "decade": 2000.0,
                "release_date": "2003-01-01",
                "artist_id": "artist_2",
                "spotify_song_id": "2ffJZ2r8HxI5DHcmf3BO6c",
                "spotify_artist_id": "694QW15WkebjcrWgQHzRYF",
            },
        }
    ],
    "num_rows_total": 17651,
}
_FILTER = {
    "rows": [{"row": {"id": 2, "chords": "<intro_1> E D A/Cs E D A/Cs"}}],
    "num_rows_total": 1,
}


def _mock_json(url):
    return _FILTER if "/filter?" in url else _SEARCH


def test_search_maps_rows(monkeypatch):
    src = ChordonomiconSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    hits = src.search("metal")
    assert len(hits) == 1
    ref = hits[0]
    assert ref.id == "2"
    assert "metal" in ref.title
    assert ref.composer == ""  # no artist name in this dataset
    assert ref.formats == ("chords",)
    assert ref.license == License.NONCOMMERCIAL
    assert ref.metadata["main_genre"] == "metal"
    assert ref.metadata["spotify_song_id"] == "2ffJZ2r8HxI5DHcmf3BO6c"


def test_viewer_url_uses_authoritative_row_idx(monkeypatch):
    src = ChordonomiconSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    ref = src.search("metal")[0]
    assert "row=298505" in ref.url  # server row_idx, not id-1 (=1)


def test_empty_query_returns_nothing():
    assert ChordonomiconSource().search("") == []


def test_fetch_writes_chords_txt_and_filters_by_id(monkeypatch):
    src = ChordonomiconSource()
    seen = {}

    def mock(url):
        if "/filter?" in url:
            seen["filter_url"] = url
            return _FILTER
        return _SEARCH

    monkeypatch.setattr(src, "_get_json", mock)
    ref = src._ref(title="Chordonomicon #2", id="2")
    path = src.fetch(ref)
    try:
        assert path.endswith(".txt")
        assert open(path, encoding="utf-8").read().startswith("<intro_1>")
        # the fetch must query by the exact id via a double-quoted where-clause
        assert "where=" in seen["filter_url"]
        assert "%22id%22%20%3D%202" in seen["filter_url"]  # quote('"id" = 2')
    finally:
        os.remove(path)


def test_retry_on_transient_error(monkeypatch):
    src = ChordonomiconSource(retries=2, retry_wait=0)
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the dataset index is loading")
        return _SEARCH

    monkeypatch.setattr(src, "_get_json", flaky)
    hits = src.search("metal")
    assert len(hits) == 1 and calls["n"] == 2


def test_retries_zero_still_attempts_once(monkeypatch):
    # retries=0 must not `raise None` (TypeError) -- it should attempt once (finding #11)
    src = ChordonomiconSource(retries=0, retry_wait=0)
    monkeypatch.setattr(src, "_get_json", lambda url: _SEARCH)
    assert len(src.search("metal")) == 1

    def boom(url):
        raise RuntimeError("still down")

    src2 = ChordonomiconSource(retries=0, retry_wait=0)
    monkeypatch.setattr(src2, "_get_json", boom)
    with pytest.raises(RuntimeError):  # the real error, not TypeError(raise None)
        src2.search("metal")
