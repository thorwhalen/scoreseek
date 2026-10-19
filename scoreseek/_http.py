"""Small shared HTTP + cache helpers (stdlib-only, keeps scoreseek zero-dep).

Sources that talk to an HTTP API route networking through here, so the
``User-Agent`` (some APIs -- e.g. MusicBrainz -- ``403`` a blank UA), JSON
decoding, byte downloads, and the on-disk cache location all live in one place.
Each source keeps a thin ``_get_json`` / ``_get_text`` / ``_get_bytes`` wrapper
so tests can monkeypatch at the source instance (see ``tests/test_thesession.py``).
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Optional

from scoreseek.base import ScoreTooLargeError

#: Kept in sync with pyproject; embedded in the default User-Agent.
__version__ = "0.0.1"

#: Bytes per MB as file hosts report it (IMSLP's "779.10MB" means 779.10 MiB).
MB = 2**20

#: Default User-Agent for outbound requests. A descriptive UA is required by
#: some hosts (MusicBrainz returns 403 for a blank/generic UA).
USER_AGENT = f"scoreseek/{__version__} (+https://github.com/thorwhalen/scoreseek)"


def _open(url: str, *, timeout: float, headers: Optional[dict] = None):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    return urllib.request.urlopen(req, timeout=timeout)


def get_bytes(
    url: str,
    *,
    timeout: float = 15.0,
    headers: Optional[dict] = None,
    max_bytes: Optional[int] = None,
) -> bytes:
    """GET *url* and return the raw response bytes.

    With ``max_bytes``, refuse a response whose ``Content-Length`` exceeds it, and
    stop reading (raising :class:`~scoreseek.base.ScoreTooLargeError`) once the
    body passes it -- so a cap holds even when the server reports no length.
    """
    with _open(url, timeout=timeout, headers=headers) as resp:
        if max_bytes is None:
            return resp.read()
        declared = resp.headers.get("Content-Length")
        if declared and declared.isdigit() and int(declared) > max_bytes:
            raise _too_large(url, int(declared), max_bytes)
        chunks, total = [], 0
        while chunk := resp.read(MB):
            total += len(chunk)
            if total > max_bytes:
                raise _too_large(url, None, max_bytes)
            chunks.append(chunk)
        return b"".join(chunks)


def _too_large(url: str, size: Optional[int], max_bytes: int) -> ScoreTooLargeError:
    got = human_size(size) if size is not None else "more than that"
    return ScoreTooLargeError(
        f"{url} is {got}, over max_bytes={max_bytes} ({human_size(max_bytes)}). "
        "Pass a larger max_bytes, or max_bytes=None for no cap."
    )


def human_size(n: Optional[int]) -> str:
    """``54841`` -> ``"53.6 KB"``, ``None`` -> ``"unknown size"`` (binary units)."""
    if n is None:
        return "unknown size"
    for unit, scale in (("GB", 2**30), ("MB", MB), ("KB", 2**10)):
        if n >= scale:
            return f"{n / scale:.1f} {unit}"
    return f"{n} B"


def get_text(
    url: str,
    *,
    timeout: float = 15.0,
    headers: Optional[dict] = None,
    encoding: str = "utf-8",
) -> str:
    """GET *url* and return the decoded response text."""
    return get_bytes(url, timeout=timeout, headers=headers).decode(encoding)


def get_json(
    url: str, *, timeout: float = 15.0, headers: Optional[dict] = None
) -> dict:
    """GET *url* and parse the JSON response."""
    h = {"Accept": "application/json"}
    if headers:
        h.update(headers)
    return json.loads(get_text(url, timeout=timeout, headers=h))


def cache_dir(*parts: str) -> Path:
    """Return (creating) scoreseek's cache dir, optionally under *parts* subdirs.

    Honors ``$XDG_CACHE_HOME`` (default ``~/.cache``), so it stays portable.
    """
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "scoreseek"
    d = base.joinpath(*parts) if parts else base
    d.mkdir(parents=True, exist_ok=True)
    return d
