"""Tests for KaggleChordsSource (sung delegation faked via dependency injection).

No real ``sung``/``haggle``/Kaggle import: the source takes a ``dataset_loader``,
so we inject a tiny pandas DataFrame shaped exactly like the real corpus.
"""

import os

import pytest

pd = pytest.importorskip("pandas")

from scoreseek.base import License
from scoreseek.sources import KaggleChordsSource

_DF = pd.DataFrame(
    [
        {
            "Unnamed: 0": 38313,
            "artist_name": "Leonard Cohen",
            "song_name": "Hallelujah",
            "chords&lyrics": "Intro:\nG Em7/B\nI heard there was a secret chord",
            "popularity": 70,
            "genres": "['folk']",
            "lang": "en",
            "name_e_chords": "leonard-cohen",
        },
        {
            "Unnamed: 0": 0,
            "artist_name": "Justin Bieber",
            "song_name": "10,000 Hours",
            "chords&lyrics": "Verse 1:\nG G/B C\nDo you love the rain",
            "popularity": 100,
            "genres": "['pop']",
            "lang": "en",
            "name_e_chords": "justin-bieber",
        },
        {
            # a SECOND "Hallelujah" by a different artist -> makes the composer
            # AND-filter load-bearing (finding #17)
            "Unnamed: 0": 55555,
            "artist_name": "Panic! at the Disco",
            "song_name": "Hallelujah",
            "chords&lyrics": "Verse:\nEb Bb\nHallelujah",
            "popularity": 60,
            "genres": "['pop']",
            "lang": "en",
            "name_e_chords": "panic-at-the-disco",
        },
        {
            # a row with a MISSING popularity (NaN) -> score must be 0.0 (finding #5)
            "Unnamed: 0": 999,
            "artist_name": "Anon",
            "song_name": "Untitled Instrumental",
            "chords&lyrics": "C Am F G",
            "popularity": float("nan"),
            "genres": "[]",
            "lang": "en",
            "name_e_chords": "anon",
        },
    ]
)


def _src():
    return KaggleChordsSource(dataset_loader=lambda: _DF)


def test_search_composer_filter_is_load_bearing():
    hits = _src().search(title="Hallelujah", composer="Leonard Cohen")
    # must return ONLY the Cohen row, not the Panic! at the Disco "Hallelujah"
    assert [h.id for h in hits] == ["38313"]
    ref = hits[0]
    assert ref.composer == "Leonard Cohen"
    assert ref.license == License.GRAY
    assert ref.formats == ("text",)
    assert abs(ref.score - 0.70) < 1e-6  # popularity 70 -> 0.70


def test_title_without_composer_returns_both_hallelujahs():
    ids = {h.id for h in _src().search("hallelujah")}
    assert ids == {"38313", "55555"}


def test_missing_popularity_scores_zero_not_nan():
    hits = _src().search(title="Untitled Instrumental")
    assert len(hits) == 1
    assert hits[0].score == 0.0  # NaN would have propagated and corrupted sorting


def test_fetch_writes_native_text():
    src = _src()
    ref = src.search(title="Hallelujah", composer="Leonard Cohen")[0]
    path = src.fetch(ref)
    try:
        assert "secret chord" in open(path, encoding="utf-8").read()
    finally:
        os.remove(path)


def test_fetch_unknown_id_raises():
    src = _src()
    ref = src._ref(title="x", id="424242")
    with pytest.raises(KeyError):
        src.fetch(ref)
