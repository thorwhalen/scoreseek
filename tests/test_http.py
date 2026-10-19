"""Tests for the shared HTTP helpers' download size cap (no network)."""

import io

import pytest

from scoreseek import ScoreTooLargeError, _http


class _Resp(io.BytesIO):
    def __init__(self, body, length=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(length)} if length is not None else {}


def _patch(monkeypatch, body, length=None):
    monkeypatch.setattr(_http, "_open", lambda url, **kw: _Resp(body, length))


def test_get_bytes_uncapped(monkeypatch):
    _patch(monkeypatch, b"x" * 10)
    assert _http.get_bytes("u") == b"x" * 10


def test_get_bytes_under_cap(monkeypatch):
    _patch(monkeypatch, b"x" * 10, length=10)
    assert _http.get_bytes("u", max_bytes=10) == b"x" * 10


def test_get_bytes_refuses_declared_length(monkeypatch):
    _patch(monkeypatch, b"", length=5 * _http.MB)
    with pytest.raises(ScoreTooLargeError, match="5.0 MB"):
        _http.get_bytes("u", max_bytes=_http.MB)


def test_get_bytes_stops_undeclared_body(monkeypatch):
    # no Content-Length: the cap must still hold while streaming
    _patch(monkeypatch, b"x" * (3 * _http.MB))
    with pytest.raises(ScoreTooLargeError, match="more than that"):
        _http.get_bytes("u", max_bytes=_http.MB + 1)
