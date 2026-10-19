"""Tests for the ScoreRef model and license-partitioning constants."""

import pytest

from scoreseek.base import COMMERCIAL_SAFE, RESTRICTED, License, ScoreRef


def test_license_partitions_are_disjoint_and_correct():
    assert License.PUBLIC_DOMAIN in COMMERCIAL_SAFE
    assert License.CC0 in COMMERCIAL_SAFE
    assert License.PERMISSIVE in COMMERCIAL_SAFE
    assert License.COPYRIGHTED in RESTRICTED
    assert License.GRAY in RESTRICTED
    assert not (COMMERCIAL_SAFE & RESTRICTED)


def test_scoreref_commercial_safe():
    assert ScoreRef("t", "s", license=License.CC0).is_commercial_safe
    assert not ScoreRef("t", "s", license=License.COPYRIGHTED).is_commercial_safe
    assert not ScoreRef("t", "s", license=License.UNKNOWN).is_commercial_safe


def test_scoreref_fetch_without_source_raises():
    with pytest.raises(NotImplementedError):
        ScoreRef("t", "s").fetch()


def test_files_on_a_non_listing_source_raises():
    import pytest

    from scoreseek.sources import TheSessionSource

    ref = TheSessionSource()._ref(title="x")
    with pytest.raises(NotImplementedError, match="does not list"):
        ref.files()
    with pytest.raises(NotImplementedError, match="cannot be listed"):
        ScoreRef(title="bare", source="none").files()


class _OneFile:
    """A minimal single-file source: fetch writes *size* bytes to a temp file."""

    name = "onefile"
    max_bytes = 100

    def __init__(self, tmp_path, size):
        self.path = tmp_path / f"score{size}.mid"
        self.path.write_bytes(b"x" * size)

    def fetch(self, ref, *, fmt=None):
        return str(self.path)

    def ref(self):
        r = ScoreRef(title="t", source=self.name)
        r._source_obj = self
        return r


def test_unknown_fetch_options_are_named(tmp_path):
    with pytest.raises(TypeError, match=r"'onefile' does not support .*'foo'"):
        _OneFile(tmp_path, 10).ref().fetch(foo=1)


def test_max_bytes_works_for_a_source_without_native_support(tmp_path):
    from scoreseek import ScoreTooLargeError

    small, big = _OneFile(tmp_path, 10).ref(), _OneFile(tmp_path, 500).ref()
    assert small.fetch().endswith("score10.mid")
    with pytest.raises(ScoreTooLargeError, match="over max_bytes=100"):
        big.fetch()  # the source's default cap (100) applies
    assert big.fetch(max_bytes=None).endswith("score500.mid")  # no cap
    assert big.fetch(max_bytes=1000).endswith("score500.mid")
    with pytest.raises(ScoreTooLargeError):
        small.fetch(max_bytes=5)


def test_pick_on_a_single_file_source(tmp_path):
    from scoreseek.picks import smallest

    ref = _OneFile(tmp_path, 10).ref()
    assert ref.fetch(pick=smallest).endswith("score10.mid")  # nothing to choose
    with pytest.raises(TypeError, match="one file per hit"):
        ref.fetch(pick="score.mid")


def test_every_builtin_source_accepts_the_uniform_options():
    # a loop over mixed search results passes max_bytes/pick without branching:
    # each source either takes them natively or is covered by ScoreRef.fetch
    from scoreseek.base import DEFAULT_MAX_BYTES, _accepts
    from scoreseek import sources as S

    downloading = {
        "IMSLPSource",
        "OpenScoreLiederSource",
        "TheSessionSource",
        "WebMidiSource",
        "ChordonomiconSource",
    }
    for name in S.__all__:
        cls = getattr(S, name)
        if not (isinstance(cls, type) and issubclass(cls, S.Source)) or cls is S.Source:
            continue
        expected = DEFAULT_MAX_BYTES if name in downloading else None
        if name != "IMSLPSource":  # IMSLP sets it per instance (configurable)
            assert cls.max_bytes == expected, name
        if _accepts(cls.fetch, "pick"):
            assert hasattr(cls, "files") and cls.files is not S.Source.files, name
