"""Tests for the search() facade: aggregation, license filtering, robustness."""

import pytest

import scoreseek
from scoreseek.base import License
from scoreseek.sources import LocalFolderSource


def test_default_source_registered():
    assert "thesession" in scoreseek.list_sources()


def test_license_filtering(tmp_path):
    (tmp_path / "a.mid").write_bytes(b"MThd\x00")
    scoreseek.register_source(
        LocalFolderSource(tmp_path, name="gray", license=License.GRAY)
    )
    scoreseek.register_source(
        LocalFolderSource(tmp_path, name="pd", license=License.PUBLIC_DOMAIN)
    )
    try:
        safe = scoreseek.search("a", sources=["gray", "pd"])  # default drops gray
        assert safe and all(h.license == License.PUBLIC_DOMAIN for h in safe)

        allhits = scoreseek.search("a", sources=["gray", "pd"], allow_copyrighted=True)
        assert any(h.license == License.GRAY for h in allhits)
    finally:
        del scoreseek.source_registry["gray"]
        del scoreseek.source_registry["pd"]


def test_unknown_source_raises():
    with pytest.raises(KeyError):
        scoreseek.search("x", sources=["nope"])


def test_flaky_source_is_skipped_with_warning():
    class Boom:
        name = "boom"
        license = License.CC0

        def search(self, *a, **k):
            raise RuntimeError("source down")

    scoreseek.register_source(Boom())
    try:
        import warnings

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = scoreseek.search("x", sources=["boom"])
        assert result == []
        assert any("boom" in str(w.message) for w in caught)
    finally:
        del scoreseek.source_registry["boom"]


def test_results_sorted_by_score(tmp_path):
    (tmp_path / "a.mid").write_bytes(b"MThd\x00")
    src = LocalFolderSource(tmp_path, name="s1", license=License.CC0)
    scoreseek.register_source(src)
    try:
        hits = scoreseek.search("a", sources=["s1"])
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)
    finally:
        del scoreseek.source_registry["s1"]
