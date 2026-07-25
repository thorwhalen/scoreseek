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
