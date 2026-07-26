"""Tests for IMSLPSource (all three network steps mocked -> offline)."""

import os

import pytest

from scoreseek.base import License
from scoreseek.sources import IMSLPSource
from scoreseek.sources.imslp import _copyright_to_license

_SEARCH = {
    "query": {
        "search": [
            {
                "ns": 0,
                "title": "Nocturnes, Op.9 (Chopin, Frédéric)",
                "snippet": "|File Name 1=PMLP02312-...pdf",
                "size": 9637,
            },
            {
                "ns": 0,
                "title": "Nocturne in C-sharp minor, B.49 (Chopin, Frédéric)",
                "snippet": "|File Name 1=PMLP03848-...mp3",
                "size": 9519,
            },
        ]
    },
    "query-continue": {"search": {"sroffset": 5}},
}

_FILENAME = "PMLP02312-Chopin_Nocturnes_Op_9_Kistner_995_First_Edition_1832.pdf"
_WIKITEXT = (
    "intro...\n*****AUDIO*****\n{{#fte:imslpaudio\n|File Name 1=recording.mp3\n"
    "|Copyright=Public Domain\n}}\n*****FILES*****\n====Complete====\n"
    "{{#fte:imslpfile\n|File Name 1="
    + _FILENAME
    + "\n|File Description 1=Complete Score\n"
    "|Publisher Information={{P|Kistner|Fr. Kistner|Leipzig|{{HMB|1833|7}}|1832||995}}\n"
    "|Copyright=Public Domain\n}}\n"
)
_REVISIONS = {
    "query": {
        "pages": {
            "177110": {
                "pageid": 177110,
                "revisions": [{"slots": {"main": {"*": _WIKITEXT}}}],
            }
        }
    }
}
_PARSE = {
    "parse": {
        "text": {"*": f'<a href="/wiki/File:{_FILENAME}" title="File:x">#86550</a>'}
    }
}


def _mock_json(url):
    if "list=search" in url:
        return _SEARCH
    if "prop=revisions" in url:
        return _REVISIONS
    if "action=parse" in url:
        return _PARSE
    raise AssertionError(f"unexpected url {url}")


# --- a builder for multi-file manifests (for fmt + license tests) ---


def _work(files):
    """files: list of (filename, copyright, index) -> (revisions, parse) mocks."""
    blocks = "".join(
        f"{{{{#fte:imslpfile\n|File Name 1={fn}\n|Copyright={cp}\n}}}}\n"
        for fn, cp, _ in files
    )
    wikitext = "*****AUDIO*****\n*****FILES*****\n" + blocks
    revisions = {
        "query": {
            "pages": {
                "1": {"pageid": 1, "revisions": [{"slots": {"main": {"*": wikitext}}}]}
            }
        }
    }
    anchors = "".join(
        f'<a href="/wiki/File:{fn}" title="x">#{idx}</a>' for fn, _, idx in files
    )
    return revisions, {"parse": {"text": {"*": anchors}}}


def _mock_for(revisions, parse):
    def m(url):
        if "list=search" in url:
            return _SEARCH
        if "prop=revisions" in url:
            return revisions
        if "action=parse" in url:
            return parse
        raise AssertionError(f"unexpected url {url}")

    return m


def test_search_parses_title_and_composer(monkeypatch):
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    hits = src.search("Chopin Nocturne")
    assert len(hits) == 2
    ref = hits[0]
    assert ref.title == "Nocturnes, Op.9"
    assert ref.composer == "Frédéric Chopin"
    assert ref.id == "Nocturnes, Op.9 (Chopin, Frédéric)"
    assert ref.license == License.PUBLIC_DOMAIN
    assert ref.source == "imslp"


def test_search_composer_filter(monkeypatch):
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    assert src.search("Nocturne", composer="Chopin")  # both hits keep Chopin
    assert src.search("Nocturne", composer="Mozart") == []


def test_fetch_downloads_from_mirror(monkeypatch):
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    captured = {}

    def fake_bytes(url):
        captured["url"] = url
        return b"%PDF-1.4 fake"

    monkeypatch.setattr(src, "_get_bytes", fake_bytes)
    ref = src.search("Chopin Nocturne")[0]
    path = src.fetch(ref)
    try:
        assert open(path, "rb").read().startswith(b"%PDF-")
        # Pin the FULL mirror URL incl. the md5 hash bucket (/9/91/) -- a bucket
        # miscompute would silently 404 in production (finding #9).
        assert captured["url"] == (
            f"https://vmirror.imslp.org/files/imglnks/usimg/9/91/IMSLP86550-{_FILENAME}"
        )
    finally:
        os.remove(path)


def test_fetch_midi_accepts_dot_mid_extension(monkeypatch):
    # fmt="midi" must accept a file named ".mid" (finding #2)
    rev, parse = _work(
        [
            ("PMLP1-w.pdf", "Public Domain", "111"),
            ("PMLP1-w.mid", "Public Domain", "222"),
        ]
    )
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_for(rev, parse))
    captured = {}
    monkeypatch.setattr(
        src, "_get_bytes", lambda u: captured.__setitem__("url", u) or b"MThd\x00"
    )
    ref = src.search("Chopin")[0]
    path = src.fetch(ref, fmt="midi")
    try:
        assert "IMSLP222-" in captured["url"]
        assert path.endswith("PMLP1-w.mid")
    finally:
        os.remove(path)


def test_fetch_refines_license_at_fetch(monkeypatch):
    # search hits are provisionally PUBLIC_DOMAIN; fetch refines to the file's
    # true license so NC files don't masquerade as PD (finding #4)
    rev, parse = _work(
        [("PMLP1-w.pdf", "Creative Commons Attribution-NonCommercial 4.0", "333")]
    )
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_for(rev, parse))
    monkeypatch.setattr(src, "_get_bytes", lambda u: b"%PDF-")
    ref = src.search("Chopin")[0]
    assert ref.license == License.PUBLIC_DOMAIN  # provisional
    path = src.fetch(ref)
    try:
        assert ref.license == License.NONCOMMERCIAL  # refined
    finally:
        os.remove(path)


def test_fetch_missing_format_raises(monkeypatch):
    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", _mock_json)
    ref = src.search("Chopin Nocturne")[0]
    # only a PDF is available; asking for MIDI must fail informatively
    with pytest.raises(RuntimeError) as e:
        src.fetch(ref, fmt="midi")
    assert "no midi file" in str(e.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Public Domain", License.PUBLIC_DOMAIN),
        ("Creative Commons Attribution-NonCommercial 4.0", License.NONCOMMERCIAL),
        (
            "Creative Commons Attribution-NonCommercial-ShareAlike 4.0",
            License.NONCOMMERCIAL,
        ),
        ("Creative Commons Attribution 4.0", License.PERMISSIVE),
        ("Creative Commons Attribution-ShareAlike 4.0", License.PERMISSIVE),
        ("", License.GRAY),
        ("some unspecified terms", License.GRAY),
    ],
)
def test_copyright_to_license(value, expected):
    # guards the branch ORDER (noncommercial checked before creative-commons)
    assert _copyright_to_license(value) == expected
