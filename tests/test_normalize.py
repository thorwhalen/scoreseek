"""Tests for MusicBrainzNormalizer (ws/2 API mocked -> offline, no throttle)."""

from scoreseek.normalize import MusicBrainzNormalizer

_WORK = {
    "created": "2026-07-25T14:30:00Z",
    "count": 9,
    "works": [
        {
            "id": "048b4b7a-07c4-3e33-959f-268199324401",
            "type": "Song",
            "score": 100,
            "title": "Autumn Leaves",
            "iswcs": ["T-070.002.297-4"],
            "disambiguation": "jazz standard, English lyrics",
            "aliases": [{"name": "Autumn Leaves (Les Feuilles Mortes)"}],
            "relations": [
                {"type": "lyricist", "artist": {"name": "Johnny Mercer"}},
                {"type": "composer", "artist": {"name": "Joseph Kosma"}},
            ],
        }
    ],
}
_RECORDING = {
    "count": 233,
    "recordings": [
        {
            "id": "7e361932-46c1-4275-9819-dddaa85dafa4",
            "score": 100,
            "title": "Autumn Leaves",
            "first-release-date": "1981",
            "artist-credit": [{"name": "Bill Evans", "artist": {"id": "8247"}}],
        }
    ],
}
_EMPTY_WORKS = {"count": 0, "works": []}


def _norm():
    n = MusicBrainzNormalizer(threshold=85)
    return n


def test_normalize_work_extracts_composer(monkeypatch):
    n = _norm()
    monkeypatch.setattr(n, "_get_json", lambda url: _WORK)
    result = n.normalize("Autumn Leaves", artist="Kosma")
    assert result["entity"] == "work"
    assert result["mbid"] == "048b4b7a-07c4-3e33-959f-268199324401"
    assert result["title"] == "Autumn Leaves"
    assert result["composer"] == "Joseph Kosma"
    assert result["score"] == 1.0
    assert "Autumn Leaves (Les Feuilles Mortes)" in result["aliases"]


def test_normalize_falls_back_to_recording(monkeypatch):
    n = _norm()
    monkeypatch.setattr(
        n, "_get_json", lambda url: _EMPTY_WORKS if "/work?" in url else _RECORDING
    )
    result = n.normalize("Autumn Leaves", artist="Bill Evans")
    assert result["entity"] == "recording"
    assert result["artist"] == "Bill Evans"
    assert result["mbid"].startswith("7e361932")


def test_normalize_below_threshold_returns_none(monkeypatch):
    n = _norm()
    low = {"works": [{"id": "x", "title": "y", "score": 40}]}
    monkeypatch.setattr(
        n, "_get_json", lambda url: low if "/work?" in url else {"recordings": []}
    )
    assert n.normalize("obscure thing") is None


def test_empty_query_returns_none():
    assert _norm().normalize("") is None
