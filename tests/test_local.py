"""Tests for the offline LocalFolderSource."""

import os

import pytest

from scoreseek.base import License
from scoreseek.sources import LocalFolderSource


def _make_scores(root):
    (root / "Cooleys Reel.mid").write_bytes(b"MThd\x00")
    (root / "Prelude in C.musicxml").write_text("<score/>")
    sub = root / "jigs"
    sub.mkdir()
    (sub / "Morrisons Jig.abc").write_text("X:1")
    (root / "notes.txt").write_text("not a score")


def test_indexes_only_score_files(tmp_path):
    _make_scores(tmp_path)
    src = LocalFolderSource(tmp_path, license=License.PUBLIC_DOMAIN)
    hits = src.search(limit=100)
    assert len(hits) == 3  # mid + musicxml + abc; the .txt is ignored


def test_search_matches_title(tmp_path):
    _make_scores(tmp_path)
    src = LocalFolderSource(tmp_path)
    hits = src.search("cooley")
    assert len(hits) == 1
    assert hits[0].title.lower().startswith("cooley")
    assert hits[0].formats == ("midi",)
    assert hits[0].source == "local"


def test_search_matches_subfolder_path(tmp_path):
    _make_scores(tmp_path)
    src = LocalFolderSource(tmp_path)
    assert any("Morrison" in h.title for h in src.search("jig"))


def test_fetch_returns_existing_path(tmp_path):
    _make_scores(tmp_path)
    src = LocalFolderSource(tmp_path)
    ref = src.search("prelude")[0]
    path = ref.fetch()
    assert os.path.exists(path)
    assert path.endswith(".musicxml")


def test_license_is_applied_to_hits(tmp_path):
    _make_scores(tmp_path)
    src = LocalFolderSource(tmp_path, license=License.PUBLIC_DOMAIN)
    assert src.search("prelude")[0].license == License.PUBLIC_DOMAIN


def test_missing_root_is_empty(tmp_path):
    src = LocalFolderSource(tmp_path / "does_not_exist")
    assert src.search(limit=100) == []


def test_fetch_max_bytes_checks_the_local_file(tmp_path):
    from scoreseek import ScoreTooLargeError

    (tmp_path / "big.mid").write_bytes(b"MThd" + b"x" * 100)
    ref = LocalFolderSource(tmp_path).search("big")[0]
    assert ref.fetch().endswith("big.mid")  # local: no default cap
    with pytest.raises(ScoreTooLargeError):
        ref.fetch(max_bytes=10)


def test_exported_tab_and_print_formats_are_scores(tmp_path):
    """One format table (scoreseek#7): songleaf's Guitar Pro/MuseScore/PDF too."""
    for name in ("Song.gp5", "Song.mscz", "Song.pdf"):
        (tmp_path / name).write_bytes(b"")
    formats = {h.formats[0] for h in LocalFolderSource(tmp_path).search(limit=10)}
    assert formats == {"guitarpro", "musescore", "pdf"}


def test_scan_folder_takes_a_format_table(tmp_path):
    from scoreseek.sources.local import SCORE_FORMATS, scan_folder

    _make_scores(tmp_path)
    (tmp_path / "Nobody - Paper Boats.txt").write_text("C\nla la la")
    with_text = {**SCORE_FORMATS, ".txt": "text"}
    entries = {e["id"]: e for e in scan_folder(tmp_path, formats=with_text)}
    assert len(entries) == 5  # the 3 scores + both .txt files
    boats = entries["Nobody - Paper Boats.txt"]
    assert (boats["title"], boats["artist"], boats["format"]) == (
        "Paper Boats",
        "Nobody",
        "text",
    )
    assert entries[os.path.join("jigs", "Morrisons Jig.abc")]["ext"] == ".abc"
    assert list(scan_folder(tmp_path / "missing")) == []
