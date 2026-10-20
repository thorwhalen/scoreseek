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


# --- the light path: an index of the same corpus (scoreseek#3) ---------------


class _FakeHit:
    def __init__(self, id, title, artist, score, meta=None):
        self.id, self.title, self.artist, self.score = id, title, artist, score
        self.meta = meta or {}


class _FakeIndex:
    def __init__(self):
        self.calls = []

    def search(self, *, title="", artist="", limit=10):
        self.calls.append((title, artist, limit))
        return [_FakeHit("38313", "Hallelujah", "Leonard Cohen", 0.9, {"lang": "en"})]


def test_an_index_is_searched_instead_of_the_dataframe():
    def no_dataframe():
        raise AssertionError("the DataFrame must not load when an index is given")

    index = _FakeIndex()
    src = KaggleChordsSource(dataset_loader=no_dataframe, index=index)
    (ref,) = src.search(title="Hallelujah", composer="Cohen", limit=3)
    assert index.calls == [("Hallelujah", "Cohen", 3)]
    assert (ref.id, ref.title, ref.composer, ref.score) == (
        "38313",
        "Hallelujah",
        "Leonard Cohen",
        0.9,
    )
    assert ref.metadata == {"artist_name": "Leonard Cohen", "lang": "en"}


def test_a_dataframe_loader_means_no_default_index():
    src = KaggleChordsSource(dataset_loader=lambda: _DF)
    assert src._index is None and src._index_path is None


def test_songleaf_index_round_trip(tmp_path, monkeypatch):
    kaggle_index = pytest.importorskip("songleaf.kaggle_index")
    pytest.importorskip("songleaf.export")
    path = str(tmp_path / "index.sqlite")
    kaggle_index.build_kaggle_index(path=path, loader=lambda: _DF)
    src = KaggleChordsSource(index=kaggle_index.KaggleChordsIndex(path))
    hits = src.search(title="Hallelujah", composer="Leonard Cohen")
    assert [h.id for h in hits] == ["38313"]
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    with open(src.fetch(hits[0]), encoding="utf-8") as f:
        assert "secret chord" in f.read()


# --- the corpus index in sung (thorwhalen/sung#17) ----------------------------


@pytest.fixture
def sung_index(tmp_path):
    chords_index = pytest.importorskip("sung.chords_index")
    path = str(tmp_path / chords_index.INDEX_NAME)
    chords_index.build_chords_index(path, rows=chords_index.dataframe_rows(_DF))
    return path


def _as_tuples(refs):
    return [(r.id, r.title, r.composer, r.score) for r in refs]


@pytest.mark.parametrize(
    "query",
    [
        dict(title="hallelujah"),
        dict(composer="cohen"),
        dict(title="hallelujah", composer="cohen"),
        dict(title="zzz"),
        dict(title="", composer=""),
    ],
)
def test_the_sung_index_searches_like_the_dataframe(sung_index, query):
    def no_dataframe():
        raise AssertionError("the DataFrame must not load when an index is read")

    light = KaggleChordsSource(index_path=sung_index)
    light._loader = no_dataframe
    heavy = KaggleChordsSource(dataset_loader=lambda: _DF)
    assert _as_tuples(light.search(**query)) == _as_tuples(heavy.search(**query))


def test_the_sung_index_fetches_the_row_verbatim(sung_index, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    light = KaggleChordsSource(index_path=sung_index)
    heavy = KaggleChordsSource(dataset_loader=lambda: _DF)
    (ref,) = light.search(title="hallelujah", composer="cohen", limit=1)
    with open(light.fetch(ref), encoding="utf-8") as a:
        light_text = a.read()
    with open(heavy.fetch(ref), encoding="utf-8") as b:
        assert light_text == b.read()
    with pytest.raises(KeyError):
        light.fetch(light._ref(title="x", id="424242"))


def test_the_default_index_is_found_through_sung(sung_index, monkeypatch):
    from scoreseek.sources import kaggle_chords

    monkeypatch.setenv("SUNG_CHORDS_INDEX", sung_index)
    assert kaggle_chords.chords_index_path() == sung_index
    assert KaggleChordsSource()._index_path == sung_index
    assert KaggleChordsSource.available()
