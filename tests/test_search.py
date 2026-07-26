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


def test_results_sorted_by_score():
    class Multi:
        name = "multi"
        license = License.CC0

        def search(self, query="", *, title="", composer="", limit=10):
            from scoreseek.base import ScoreRef

            return [
                ScoreRef(title="lo", source="multi", license=License.CC0, score=0.3),
                ScoreRef(title="hi", source="multi", license=License.CC0, score=0.9),
            ]

    scoreseek.register_source(Multi())
    try:
        hits = scoreseek.search("x", sources=["multi"])
        # non-vacuous: two distinct scores -> a dropped/reversed sort would fail
        assert [h.score for h in hits] == [0.9, 0.3]
    finally:
        del scoreseek.source_registry["multi"]


class _StaticSource:
    """A tiny source returning one hit with a fixed license (for filter tests)."""

    def __init__(self, name, license):
        self.name = name
        self.license = license

    def search(self, query="", *, title="", composer="", limit=10):
        from scoreseek.base import ScoreRef

        return [
            ScoreRef(
                title=query or "x", source=self.name, license=self.license, score=1.0
            )
        ]


def test_noncommercial_gated_by_default():
    scoreseek.register_source(_StaticSource("nc", License.NONCOMMERCIAL))
    try:
        # default is copyright-safe: CC-BY-NC is a *restricted* lane -> dropped
        assert scoreseek.search("x", sources=["nc"]) == []
        allowed = scoreseek.search("x", sources=["nc"], allow_copyrighted=True)
        assert allowed and allowed[0].license == License.NONCOMMERCIAL
    finally:
        del scoreseek.source_registry["nc"]


def test_unknown_license_visible_by_default():
    # UNKNOWN (e.g. your own local folder) is not hidden by the safe default
    scoreseek.register_source(_StaticSource("unk", License.UNKNOWN))
    try:
        assert scoreseek.search("x", sources=["unk"])
    finally:
        del scoreseek.source_registry["unk"]


def test_normalize_canonicalizes_query_and_stamps_mbid():
    class FakeNormalizer:
        def normalize(self, query, *, artist=None, limit=3):
            return {
                "title": "Autumn Leaves",
                "composer": "Joseph Kosma",
                "mbid": "MBID-1",
            }

    captured = {}

    class Recorder:
        name = "rec"
        license = License.PUBLIC_DOMAIN

        def search(self, query="", *, title="", composer="", limit=10):
            from scoreseek.base import ScoreRef

            captured.update(query=query, title=title, composer=composer)
            return [
                ScoreRef(title=title, source="rec", license=self.license, score=1.0)
            ]

    scoreseek.register_source(Recorder())
    try:
        hits = scoreseek.search(
            "autumn leaves kosma", sources=["rec"], normalizer=FakeNormalizer()
        )
        assert captured["title"] == "Autumn Leaves"
        assert captured["composer"] == "Joseph Kosma"
        assert hits[0].metadata.get("mbid") == "MBID-1"
    finally:
        del scoreseek.source_registry["rec"]
