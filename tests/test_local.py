"""Tests for the offline LocalFolderSource."""

import os

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
