"""Tests for LakhMidiSource over a tiny fake tarball and folder (no network)."""

import io
import tarfile

import pytest

from scoreseek.base import License
from scoreseek.sources import LakhMidiSource

_FILES = {
    "clean_midi/Meco/Star Wars Theme.mid": b"MThd-1",
    "clean_midi/Meco/Star Wars Theme.2.mid": b"MThd-2",
    "clean_midi/ABBA/Waterloo.mid": b"MThd-3",
}


def _tarball(tmp_path):
    path = tmp_path / "clean_midi.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, data in _FILES.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_search_tarball_by_artist_and_title(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    src = LakhMidiSource(_tarball(tmp_path))
    hits = src.search("star wars")
    assert [(h.composer, h.title) for h in hits] == [("Meco", "Star Wars Theme")] * 2
    assert all(h.license is License.GRAY for h in hits)
    assert src.search(composer="abba")[0].title == "Waterloo"


def test_fetch_extracts_one_member(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    src = LakhMidiSource(_tarball(tmp_path))
    hit = [h for h in src.search("waterloo")][0]
    assert open(hit.fetch(), "rb").read() == b"MThd-3"


def test_folder_layout(tmp_path):
    for name, data in _FILES.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    src = LakhMidiSource(tmp_path / "clean_midi")
    hit = src.search("waterloo")[0]
    assert hit.fetch().endswith("ABBA/Waterloo.mid")


def test_missing_path_says_where_to_download(tmp_path):
    with pytest.raises(FileNotFoundError, match="clean_midi.tar.gz"):
        LakhMidiSource(tmp_path / "nope.tar.gz")
