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

    def fake_bytes(url, **kw):
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
        src, "_get_bytes", lambda u, **kw: captured.__setitem__("url", u) or b"MThd\x00"
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
    monkeypatch.setattr(src, "_get_bytes", lambda u, **kw: b"%PDF-")
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


# --- file listing, size cap and file choice (issue #2) ---

_MB = 2**20


def _sized_work(files, *, headings=None):
    """files: list of (filename, size_text, index, description).

    Builds wikitext (with optional ``headings[i]`` placed before file i) and a
    rendered page whose links carry IMSLP's size text, e.g. "9.29MB" or the
    large-file form with the number wrapped in tags.
    """
    headings = headings or {}
    blocks = ""
    for i, (fn, _, _, desc) in enumerate(files):
        blocks += headings.get(i, "")
        blocks += (
            f"{{{{#fte:imslpfile\n|File Name 1={fn}\n|File Description 1={desc}\n"
            f"|Copyright=Public Domain\n}}}}\n"
        )
    wikitext = "*****AUDIO*****\n*****FILES*****\n" + blocks
    revisions = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "Nocturnes, Op.9 (Chopin, Frédéric)",
                    "revisions": [{"slots": {"main": {"*": wikitext}}}],
                }
            }
        }
    }
    anchors = "".join(
        f'<a href="/wiki/File:{fn}" title="x">#{idx}</a> - {size}, 3 pp. <span>x</span>'
        for fn, size, idx, _ in files
    )
    return revisions, {"parse": {"text": {"*": anchors}}}


_BIG_FIRST = [
    (
        "PMLP1-huge-score.pdf",
        '<span style="color:red"><b>779.10</b></span>MB',
        "1",
        "Complete Score",
    ),
    ("PMLP1-full-score.pdf", "12.50MB", "2", "Complete Score"),
    ("PMLP1-flute-part.pdf", "0.80MB", "3", "Flute"),
]


def _src(monkeypatch, files, **kw):
    rev, parse = _sized_work(files, headings=kw.pop("headings", None))
    src = IMSLPSource(**kw)
    monkeypatch.setattr(src, "_get_json", _mock_for(rev, parse))
    captured = {}

    def fake_bytes(url, *, max_bytes=None):
        captured["url"], captured["max_bytes"] = url, max_bytes
        return b"%PDF-"

    monkeypatch.setattr(src, "_get_bytes", fake_bytes)
    return src, captured


def test_files_lists_kinds_sizes_and_fills_formats(monkeypatch):
    src, _ = _src(monkeypatch, _BIG_FIRST + [("PMLP1-w.mid", "0.01MB", "4", "MIDI")])
    ref = src.search("Chopin")[0]
    files = ref.files()
    assert [f.filename for f in files][:2] == [
        "PMLP1-huge-score.pdf",
        "PMLP1-full-score.pdf",
    ]
    assert files[0].size == int(779.10 * _MB)  # tag-wrapped large-file size
    assert files[1].size == int(12.5 * _MB)
    assert [f.kind for f in files] == ["pdf", "pdf", "pdf", "midi"]
    assert files[0].metadata["index"] == "1"
    assert ref.formats == ("midi", "pdf")


def test_search_hits_carry_formats(monkeypatch):
    src, _ = _src(monkeypatch, _BIG_FIRST)
    hit = src.search("Chopin")[0]
    assert hit.formats == ("pdf",)  # resolved from the batched wikitext request


def test_search_can_skip_format_resolution(monkeypatch):
    src, _ = _src(monkeypatch, _BIG_FIRST, resolve_formats=False)
    assert src.search("Chopin")[0].formats == ()


def test_fetch_skips_files_over_default_cap(monkeypatch):
    src, captured = _src(monkeypatch, _BIG_FIRST)
    path = src.search("Chopin")[0].fetch()
    try:
        assert "IMSLP2-" in captured["url"]  # the 779 MB file is skipped
        assert captured["max_bytes"] == 50 * _MB  # the download is capped too
    finally:
        os.remove(path)


def test_fetch_all_too_large_names_smallest(monkeypatch):
    from scoreseek import ScoreTooLargeError

    src, _ = _src(monkeypatch, _BIG_FIRST)
    ref = src.search("Chopin")[0]
    with pytest.raises(ScoreTooLargeError) as e:
        ref.fetch(max_bytes=100_000)
    msg = str(e.value)
    assert "PMLP1-flute-part.pdf (819.2 KB)" in msg
    assert msg.index("flute-part") < msg.index("full-score")  # smallest first
    assert "pick=" in msg and "max_bytes" in msg


def test_fetch_no_cap(monkeypatch):
    src, captured = _src(monkeypatch, _BIG_FIRST)
    path = src.search("Chopin")[0].fetch(max_bytes=None)
    try:
        assert "IMSLP1-" in captured["url"]
        assert captured["max_bytes"] is None
    finally:
        os.remove(path)


def test_fetch_pick_by_filename_and_strategy(monkeypatch):
    from scoreseek.sources.imslp import smallest

    src, captured = _src(monkeypatch, _BIG_FIRST)
    ref = src.search("Chopin")[0]
    os.remove(ref.fetch(pick="PMLP1-flute-part.pdf"))
    assert "IMSLP3-" in captured["url"]
    os.remove(ref.fetch(pick=smallest))
    assert "IMSLP3-" in captured["url"]
    os.remove(ref.fetch(pick=lambda files: files[0]))  # first file that fits
    assert "IMSLP2-" in captured["url"]


def test_fetch_pick_filename_errors(monkeypatch):
    from scoreseek import ScoreTooLargeError

    src, _ = _src(monkeypatch, _BIG_FIRST)
    ref = src.search("Chopin")[0]
    with pytest.raises(RuntimeError, match="no file named"):
        ref.fetch(pick="nope.pdf")
    with pytest.raises(ScoreTooLargeError, match="779.1 MB"):
        ref.fetch(pick="PMLP1-huge-score.pdf")


def test_default_pick_prefers_full_score_over_part(monkeypatch):
    files = [
        ("PMLP1-flute.pdf", "0.80MB", "3", "Flute"),
        ("PMLP1-score.pdf", "5.00MB", "2", "Complete Score"),
    ]
    src, captured = _src(
        monkeypatch, files, headings={0: "===Parts===\n", 1: "===Full Scores===\n"}
    )
    ref = src.search("Chopin")[0]
    assert ref.files()[0].section == "Parts"
    os.remove(ref.fetch())
    assert "IMSLP2-" in captured["url"]


def test_default_pick_prefers_known_size(monkeypatch):
    files = [
        ("PMLP1-a.pdf", "?", "1", "Complete Score"),  # no size shown
        ("PMLP1-b.pdf", "1.00MB", "2", "Complete Score"),
    ]
    src, captured = _src(monkeypatch, files)
    ref = src.search("Chopin")[0]
    assert ref.files()[0].size is None
    os.remove(ref.fetch())
    assert "IMSLP2-" in captured["url"]


def test_size_does_not_leak_from_next_link():
    from scoreseek.sources.imslp import _parse_size

    size, bound = _parse_size(' <span class="preview">x</span> - 7.96MB, 87 pp.')
    assert size == int(7.96 * _MB)
    assert bound == int(7.965 * _MB)  # displayed to 0.01 MB: half a step of slack
    assert _parse_size(' <i>?</i> <a href="/wiki/File:other.pdf">#9</a> - 3.00MB') == (
        None,
        None,
    )


def test_multi_file_block_and_heading_path():
    from scoreseek.sources.imslp import _parse_files

    wikitext = (
        "*****FILES*****\n===Arrangements===\n====For Piano====\n"
        "{{#fte:imslpfile\n|File Name 1=PMLP1-mvt1.pdf\n|File Description 1=Mvt 1\n"
        "|File Name 2=PMLP1-mvt2.mid\n|File Description 2=Mvt 2\n"
        "|Copyright=Public Domain\n}}\n===Parts===\n"
        "{{#fte:imslpfile\n|File Name 1=PMLP1-vn.pdf\n|Copyright=Public Domain\n}}\n"
    )
    files = _parse_files(wikitext)
    assert [(f["filename"], f["kind"], f["description"]) for f in files] == [
        ("PMLP1-mvt1.pdf", "pdf", "Mvt 1"),
        ("PMLP1-mvt2.mid", "midi", "Mvt 2"),
        ("PMLP1-vn.pdf", "pdf", ""),
    ]
    assert files[0]["section"] == "Arrangements / For Piano"
    assert files[2]["section"] == "Parts"  # a level-3 heading clears level 4


def test_audio_section_midi_is_listed(monkeypatch):
    # IMSLP keeps MIDI renditions with the recordings (review finding #1)
    wikitext = (
        "*****AUDIO*****\n===Synthesized/MIDI===\n"
        "{{#fte:imslpaudio\n|File Name 1=PMLP1-perf.mp3\n|Copyright=Public Domain\n}}\n"
        "{{#fte:imslpaudio\n|File Name 1=WIMA-w.mid\n|File Description 1=Synthesized\n"
        "|Copyright=Public Domain\n}}\n"
        "*****FILES*****\n{{#fte:imslpfile\n|File Name 1=PMLP1-score.pdf\n"
        "|Copyright=Public Domain\n}}\n"
    )
    from scoreseek.sources.imslp import _parse_files

    files = _parse_files(wikitext)
    assert [(f["filename"], f["kind"]) for f in files] == [
        ("PMLP1-score.pdf", "pdf"),
        ("WIMA-w.mid", "midi"),  # the .mp3 is not a score file
    ]
    assert files[1]["section"] == "Synthesized/MIDI"


def test_default_pick_prefers_safe_license_over_renderable(monkeypatch):
    # a non-commercial MIDI must not displace a PD score in a default fetch
    from scoreseek.base import ScoreFile
    from scoreseek.picks import prefer_safe_renderable

    nc_midi = ScoreFile("w.mid", "midi", 10, License.NONCOMMERCIAL)
    pd_pdf = ScoreFile("w.pdf", "pdf", 10, License.PUBLIC_DOMAIN)
    pd_midi = ScoreFile("v.mid", "midi", 10, License.PUBLIC_DOMAIN)
    assert prefer_safe_renderable([nc_midi, pd_pdf]) is pd_pdf
    assert prefer_safe_renderable([nc_midi, pd_pdf, pd_midi]) is pd_midi


def test_is_part_ignores_partitur_and_partita():
    from scoreseek.base import ScoreFile
    from scoreseek.picks import is_part

    assert is_part(ScoreFile("a", "pdf", section="Parts"))
    assert is_part(ScoreFile("a", "pdf", description="Flute part"))
    assert not is_part(ScoreFile("a", "pdf", description="Partitur"))
    assert not is_part(ScoreFile("a", "pdf", section="Partita No.2"))
    assert not is_part(ScoreFile("a", "pdf", section="Part-songs"))


def test_listed_size_bound_is_used_for_the_cap(monkeypatch):
    # displayed 0.05MB may really be up to 0.055MB: a cap of exactly the displayed
    # size must not let it through only to trip the download (review finding #3)
    files = [("PMLP1-a.pdf", "0.05MB", "1", "Complete Score")]
    src, _ = _src(monkeypatch, files)
    from scoreseek import ScoreTooLargeError

    ref = src.search("Chopin")[0]
    with pytest.raises(ScoreTooLargeError):
        ref.fetch(max_bytes=int(0.05 * _MB))
    with pytest.raises(ScoreTooLargeError):
        ref.fetch(pick="PMLP1-a.pdf", max_bytes=int(0.05 * _MB))


def test_strategy_falls_back_when_download_trips_cap(monkeypatch):
    from scoreseek import ScoreTooLargeError

    files = [
        ("PMLP1-a.pdf", "?", "1", "Complete Score"),  # unknown size
        ("PMLP1-b.pdf", "?", "2", "Complete Score"),
    ]
    src, _ = _src(monkeypatch, files)
    tried = []

    def fake_bytes(url, *, max_bytes=None):
        tried.append(url)
        if "IMSLP1-" in url:
            raise ScoreTooLargeError("too big")
        return b"%PDF-"

    monkeypatch.setattr(src, "_get_bytes", fake_bytes)
    path = src.search("Chopin")[0].fetch()
    try:
        assert ["IMSLP1-" in tried[0], "IMSLP2-" in tried[1]] == [True, True]
    finally:
        os.remove(path)


def test_filename_pick_respects_fmt_and_none_pick_is_default(monkeypatch):
    src, captured = _src(monkeypatch, _BIG_FIRST)
    ref = src.search("Chopin")[0]
    with pytest.raises(RuntimeError, match="is a pdf file, not midi"):
        ref.fetch(fmt="midi", pick="PMLP1-full-score.pdf")
    os.remove(ref.fetch(pick=None))  # None means the source default
    assert "IMSLP2-" in captured["url"]


def test_strategy_must_return_a_candidate(monkeypatch):
    src, _ = _src(monkeypatch, _BIG_FIRST)
    ref = src.search("Chopin")[0]
    with pytest.raises(RuntimeError, match="not one of the candidate"):
        ref.fetch(pick=lambda files: "PMLP1-full-score.pdf")
    huge = ref.files()[0]
    with pytest.raises(RuntimeError, match="not one of the candidate"):
        ref.fetch(pick=lambda files: huge)  # over the cap: not a candidate


def test_search_follows_redirects_and_dedupes(monkeypatch):
    # review finding #2: redirect hits (empty snippet) pointed at their target
    search = {
        "query": {
            "search": [
                {"title": "Greensleeves (Vaughan Williams, Ralph)", "snippet": ""},
                {"title": "Old Title (Vaughan Williams, Ralph)", "snippet": ""},
                {"title": "Sir John in Love (Vaughan Williams, Ralph)", "snippet": "x"},
            ]
        }
    }
    target = "Sir John in Love (Vaughan Williams, Ralph)"
    wikitext = (
        "*****FILES*****\n{{#fte:imslpfile\n|File Name 1=PMLP9-s.pdf\n"
        "|Copyright=Public Domain\n}}\n"
    )
    revisions = {
        "query": {
            "redirects": [
                {"from": "Greensleeves (Vaughan Williams, Ralph)", "to": target},
                {"from": "Old Title (Vaughan Williams, Ralph)", "to": target},
            ],
            "pages": {
                "9": {
                    "title": target,
                    "revisions": [{"slots": {"main": {"*": wikitext}}}],
                }
            },
        }
    }
    parse = {
        "parse": {"text": {"*": '<a href="/wiki/File:PMLP9-s.pdf">#9</a> - 1.00MB'}}
    }
    seen = []

    def mock(url):
        seen.append(url)
        if "list=search" in url:
            return search
        if "prop=revisions" in url:
            assert "redirects=1" in url
            return revisions
        return parse

    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", mock)
    hits = src.search("Greensleeves")
    assert len(hits) == 1  # three hits, one page
    hit = hits[0]
    assert hit.id == "Greensleeves (Vaughan Williams, Ralph)"
    assert hit.metadata["page"] == target
    assert hit.metadata["redirected_from"] == hit.id
    assert hit.formats == ("pdf",)
    assert [f.filename for f in hit.files()] == ["PMLP9-s.pdf"]


@pytest.mark.parametrize(
    "cont",
    [
        {"query-continue": {"revisions": {"rvcontinue": "2|x"}}},  # IMSLP (MW 1.18)
        {"continue": {"rvcontinue": "2|x", "continue": "||"}},  # newer MediaWiki
    ],
)
def test_wikitexts_follows_continuation(monkeypatch, cont):
    first = {
        **cont,
        "query": {"pages": {"1": {"title": "A", "revisions": [{"*": "ta"}]}}},
    }
    second = {"query": {"pages": {"2": {"title": "B", "revisions": [{"*": "tb"}]}}}}
    calls = []

    def mock(url):
        calls.append(url)
        return second if "rvcontinue" in url else first

    src = IMSLPSource()
    monkeypatch.setattr(src, "_get_json", mock)
    assert src._wikitexts(["A", "B"]) == {"A": ("A", "ta"), "B": ("B", "tb")}
    assert len(calls) == 2


def test_fallback_terminates_for_value_equal_pick(monkeypatch):
    # a strategy may return an equal (not identical) ScoreFile, e.g. one kept from
    # an earlier ref.files(); the fallback must still drop it (2nd review, #1)
    from scoreseek import ScoreTooLargeError

    files = [("PMLP1-a.pdf", "?", "1", "Complete Score")]
    src, _ = _src(monkeypatch, files)
    ref = src.search("Chopin")[0]
    kept = ref.files()[0]
    calls = []

    def fake_bytes(url, *, max_bytes=None):
        calls.append(url)
        raise ScoreTooLargeError("too big")

    monkeypatch.setattr(src, "_get_bytes", fake_bytes)
    with pytest.raises(ScoreTooLargeError, match="PMLP1-a.pdf"):
        ref.fetch(pick=lambda fs: kept)
    assert len(calls) == 1
