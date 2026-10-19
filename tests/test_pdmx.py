"""Tests for PDMXSource (a local-corpus source -> no network at all)."""

import pytest

from scoreseek.base import License
from scoreseek.sources import PDMXSource

_HEADER = "title,song_name,composer_name,artist_name,license,license_conflict,mxl,mid,genres,n_tracks,rating"
_ROWS = [
    # clean public-domain row (has both mxl + mid)
    "Helvic Head,Helvic Head,NA,NA,publicdomain,False,./mxl/1/11/QmABC.mxl,./mid/1/11/QmABC.mid,NA,2,4.66",
    # cc-zero row
    "Sunset Waltz,Sunset Waltz,From Harts Gallopades,NA,cc-zero,False,./mxl/2/22/QmCC0.mxl,NA,classical,1,NA",
    # license-conflict row (dropped in clean lane, GRAY otherwise)
    "Doubtful,Doubtful,Somebody,NA,publicdomain,True,./mxl/3/33/QmDEF.mxl,NA,pop,3,NA",
    # unrecognized license string -> GRAY (finding #10), clean (no conflict)
    "WeirdLic,WeirdLic,NA,NA,mystery-terms,False,./mxl/4/44/QmWWW.mxl,NA,NA,1,NA",
]


def _make_corpus(tmp_path, *, with_mxl=True):
    (tmp_path / "PDMX.csv").write_text(
        _HEADER + "\n" + "\n".join(_ROWS) + "\n", encoding="utf-8"
    )
    if with_mxl:
        f = tmp_path / "mxl" / "1" / "11" / "QmABC.mxl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"PK\x03\x04fake")
    return tmp_path


def test_search_clean_only_drops_conflict(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    hits = src.search("")  # bare listing
    titles = {h.title for h in hits}
    assert "Doubtful" not in titles  # license_conflict -> dropped
    assert {"Helvic Head", "Sunset Waltz"} <= titles


def test_license_mapping(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    by_title = {h.title: h for h in src.search("")}
    assert by_title["Helvic Head"].license == License.PUBLIC_DOMAIN
    assert by_title["Sunset Waltz"].license == License.CC0
    assert by_title["Helvic Head"].formats == ("musicxml", "midi")
    assert by_title["Sunset Waltz"].formats == ("musicxml",)  # mid is NA
    assert by_title["Helvic Head"].id == "QmABC"


def test_conflict_row_tagged_gray_when_not_clean(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path), clean_only=False)
    by_title = {h.title: h for h in src.search("")}
    assert by_title["Doubtful"].license == License.GRAY


def test_fetch_returns_local_path(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    ref = next(h for h in src.search("Helvic") if h.title == "Helvic Head")
    path = src.fetch(ref)
    assert path.endswith("QmABC.mxl")
    assert open(path, "rb").read().startswith(b"PK\x03\x04")


def test_unknown_license_defaults_gray(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    by_title = {h.title: h for h in src.search("")}
    assert by_title["WeirdLic"].license == License.GRAY  # not PUBLIC_DOMAIN


def test_fetch_missing_corpus_raises_with_zenodo_hint(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    ref = next(h for h in src.search("Helvic") if h.title == "Helvic Head")
    with pytest.raises(FileNotFoundError) as e:
        src.fetch(ref, fmt="midi")  # mid file was never created
    assert "mid.tar.gz" in str(e.value)


def test_fetch_pdf_does_not_silently_return_mxl(tmp_path):
    # fmt="pdf" must raise, not silently hand back the MusicXML file (finding #3)
    src = PDMXSource(_make_corpus(tmp_path))
    ref = next(h for h in src.search("Helvic") if h.title == "Helvic Head")
    with pytest.raises(ValueError):
        src.fetch(ref, fmt="pdf")


def test_search_missing_index_raises(tmp_path):
    src = PDMXSource(tmp_path)  # no PDMX.csv here
    with pytest.raises(FileNotFoundError) as e:
        src.search("anything")
    assert "PDMX.csv" in str(e.value)


def _helvic(tmp_path):
    src = PDMXSource(_make_corpus(tmp_path))
    return src, next(h for h in src.search("Helvic") if h.title == "Helvic Head")


def test_files_lists_mxl_then_mid_with_disk_sizes(tmp_path):
    _, ref = _helvic(tmp_path)
    files = ref.files()
    assert [(f.filename, f.kind) for f in files] == [
        ("QmABC.mxl", "musicxml"),
        ("QmABC.mid", "midi"),
    ]
    assert files[0].size == len(b"PK\x03\x04fake")
    assert files[1].size is None  # mid corpus not extracted


def test_fetch_pick_by_filename_and_strategy(tmp_path):
    src, ref = _helvic(tmp_path)
    assert ref.fetch(pick="QmABC.mxl").endswith("QmABC.mxl")
    with pytest.raises(FileNotFoundError, match="mid.tar.gz"):
        ref.fetch(pick="QmABC.mid")
    with pytest.raises(FileNotFoundError, match="mid.tar.gz"):
        ref.fetch(pick=lambda files: files[1])
    with pytest.raises(RuntimeError, match="is a musicxml file, not midi"):
        ref.fetch(fmt="midi", pick="QmABC.mxl")


def test_fetch_max_bytes(tmp_path):
    from scoreseek import ScoreTooLargeError

    _, ref = _helvic(tmp_path)
    assert ref.fetch().endswith("QmABC.mxl")  # local corpus: no default cap
    with pytest.raises(ScoreTooLargeError, match="Smallest: QmABC.mxl"):
        ref.fetch(fmt="musicxml", max_bytes=2)
