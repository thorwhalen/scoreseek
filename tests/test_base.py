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


def test_builtin_sources_default_caps_and_pick_support():
    # downloading sources cap by default, local corpora don't; a source that
    # takes pick natively must also list its files
    from scoreseek import sources as S
    from scoreseek.base import DEFAULT_MAX_BYTES, _names

    downloading = {
        "OpenScoreLiederSource",
        "TheSessionSource",
        "WebMidiSource",
        "ChordonomiconSource",
    }
    local = {"LocalFolderSource", "PDMXSource", "LakhMidiSource", "KaggleChordsSource"}
    for name in downloading | local:
        assert getattr(S, name).max_bytes == (
            DEFAULT_MAX_BYTES if name in downloading else None
        ), name
    assert S.IMSLPSource().max_bytes == DEFAULT_MAX_BYTES
    for name in ("IMSLPSource", "PDMXSource"):
        cls = getattr(S, name)
        assert _names(cls.fetch, "pick") and cls.files is not S.Source.files


def test_kwargs_catch_all_does_not_disable_the_size_check(tmp_path):
    # a fetch(**kw) may ignore max_bytes; the generic check must still run
    from scoreseek import ScoreTooLargeError

    class Pass(_OneFile):
        def fetch(self, ref, *, fmt=None, **kw):
            return str(self.path)

    ref = Pass(tmp_path, 500).ref()
    with pytest.raises(ScoreTooLargeError):
        ref.fetch()  # default cap 100
    with pytest.raises(ScoreTooLargeError):
        ref.fetch(max_bytes=10)


def test_undecorated_wrapper_fetch_gets_no_pick_and_is_still_capped(tmp_path):
    from scoreseek import ScoreTooLargeError
    from scoreseek.picks import smallest

    class Wrapped(_OneFile):
        def __init__(self, tmp_path, size):
            super().__init__(tmp_path, size)
            inner = self.fetch
            self.fetch = lambda *a, **k: inner(*a, **k)  # no functools.wraps

    assert Wrapped(tmp_path, 10).ref().fetch(pick=smallest).endswith("score10.mid")
    with pytest.raises(ScoreTooLargeError):
        Wrapped(tmp_path, 500).ref().fetch()


def test_max_bytes_is_validated(tmp_path):
    ref = _OneFile(tmp_path, 10).ref()
    with pytest.raises(TypeError, match="max_bytes must be an int"):
        ref.fetch(max_bytes="5MB")
    with pytest.raises(ValueError, match=">= 0"):
        ref.fetch(max_bytes=-1)


def test_a_scorefile_is_accepted_as_pick(tmp_path):
    from scoreseek.base import ScoreFile

    ref = _OneFile(tmp_path, 10).ref()
    with pytest.raises(TypeError, match="one file per hit"):  # treated as filename
        ref.fetch(pick=ScoreFile("score10.mid", "midi"))
