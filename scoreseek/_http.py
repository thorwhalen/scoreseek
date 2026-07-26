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

#: Kept in sync with pyproject; embedded in the default User-Agent.
__version__ = "0.0.1"

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
    url: str, *, timeout: float = 15.0, headers: Optional[dict] = None
) -> bytes:
    """GET *url* and return the raw response bytes."""
    with _open(url, timeout=timeout, headers=headers) as resp:
        return resp.read()


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
