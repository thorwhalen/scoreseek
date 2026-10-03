"""Tests for WebMidiSource: site HTML is canned, network is monkeypatched."""

import pytest

from scoreseek.base import License
from scoreseek.sources import MidiSite, WebMidiSource

_PAGES = {
    "https://bitmidi.com/search?q=star+wars": (
        '<a href="/star-wars-theme-from-star-wars-mid">x</a>'
        '<a href="/star-wars-theme-from-star-wars-mid">dup</a>'
    ),
    "https://bitmidi.com/star-wars-theme-from-star-wars-mid": (
        '{"downloadUrl":"/uploads/96653.mid"}'
    ),
    "https://www.midis101.com/search/star%20wars": (
        '<a href="/free-midi/58174-soundtrack-star-wars-sw-main">a</a>'
    ),
    "https://www.midiworld.com/search/?q=star+wars": (
        '<li>\nStar Wars - by John Willams (Movie Themes) - \n'
        '<a href="https://www.midiworld.com/download/3854" target="_blank">download</a>'
    ),
}
_MIDI = b"MThd\x00\x00\x00\x06fake"


@pytest.fixture
def src(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    s = WebMidiSource()
    calls = []

    def get_text(url, headers=None):
        return _PAGES[url]

    def get_bytes(url, headers=None):
        calls.append((url, headers))
        return _MIDI

    monkeypatch.setattr(s, "_get_text", get_text)
    monkeypatch.setattr(s, "_get_bytes", get_bytes)
    s.calls = calls
    return s


def test_search_all_sites_gray_and_deduplicated(src):
    hits = src.search("star wars")
    ids = [h.id for h in hits]
    assert sorted(ids) == sorted(
        [
            "bitmidi:star-wars-theme-from-star-wars-mid",
            "midis101:58174-soundtrack-star-wars-sw-main",
            "midiworld:3854",
        ]
    )
    assert all(h.license is License.GRAY and h.formats == ("midi",) for h in hits)
    titles = {h.id: h.title for h in hits}
    assert titles["midiworld:3854"].startswith("Star Wars - by John")
    assert titles["midis101:58174-soundtrack-star-wars-sw-main"] == "soundtrack star wars sw main"


def test_fetch_resolves_bitmidi_and_caches(src):
    hit = [h for h in src.search("star wars") if h.id.startswith("bitmidi")][0]
    path = hit.fetch()
    assert open(path, "rb").read() == _MIDI
    assert src.calls[0][0] == "https://bitmidi.com/uploads/96653.mid"
    hit.fetch()
    assert len(src.calls) == 1  # second fetch served from cache


def test_fetch_sends_referer_where_needed(src):
    hit = [h for h in src.search("star wars") if h.id.startswith("midis101")][0]
    hit.fetch()
    url, headers = src.calls[-1]
    assert url == "https://www.midis101.com/download/58174-soundtrack-star-wars-sw-main"
    assert headers["Referer"].endswith("/free-midi/58174-soundtrack-star-wars-sw-main")


def test_fetch_rejects_non_midi(src, monkeypatch):
    monkeypatch.setattr(src, "_get_bytes", lambda url, headers=None: b"<html>")
    hit = [h for h in src.search("star wars") if h.id.startswith("midiworld")][0]
    with pytest.raises(RuntimeError, match="did not return a MIDI"):
        hit.fetch()


def test_a_failing_site_does_not_sink_search(src, monkeypatch):
    def flaky(url, headers=None):
        if "bitmidi" in url:
            raise OSError("down")
        return _PAGES[url]

    monkeypatch.setattr(src, "_get_text", flaky)
    assert {h.metadata["site"] for h in src.search("star wars")} == {"midis101", "midiworld"}


def test_custom_site():
    site = MidiSite(
        name="mine",
        search_url="https://x.test/s?q={q}",
        hit_pattern=r'href="/m/(?P<id>\d+)">(?P<title>[^<]+)<',
        download_url="https://x.test/d/{id}",
        page_url="https://x.test/m/{id}",
    )
    assert site.parse_hits('<a href="/m/7">Song A</a>') == [("7", "Song A")]
    assert WebMidiSource(sites=[site]).sites == {"mine": site}
