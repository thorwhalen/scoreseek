"""The Session (thesession.org) source: Irish/folk traditional tunes in ABC.

A real, zero-config, no-auth source with a clean JSON API and openly-licensed
content (CC-BY-SA 4.0). Search returns tune references; fetching writes a valid
ABC file (ready for ``audiate.render``). Networking uses only the standard
library, so scoreseek's core needs no third-party HTTP dependency.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from typing import List, Optional

from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_BASE = "https://thesession.org"
_UA = "scoreseek/0.0.1 (+https://github.com/thorwhalen/scoreseek)"

#: tune "type" -> a sensible default meter (for cleaner ABC rendering).
_METER = {
    "reel": "4/4",
    "jig": "6/8",
    "slip jig": "9/8",
    "hornpipe": "4/4",
    "polka": "2/4",
    "slide": "12/8",
    "waltz": "3/4",
    "march": "4/4",
    "strathspey": "4/4",
    "barndance": "4/4",
    "mazurka": "3/4",
    "three-two": "3/2",
}


class TheSessionSource(Source):
    """Search thesession.org's traditional-tune database (ABC, CC-BY-SA 4.0)."""

    name = "thesession"
    license = License.PERMISSIVE  # CC-BY-SA 4.0

    def __init__(self, *, timeout: float = 15.0):
        self.timeout = timeout

    def _get_json(self, url: str) -> dict:
        req = urllib.request.Request(
            url, headers={"User-Agent": _UA, "Accept": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def search(
        self,
        query: str = "",
        *,
        title: str = "",
        composer: str = "",
        limit: int = 10,
    ) -> List[ScoreRef]:
        """Search tunes by name. Returns ABC-format :class:`ScoreRef` hits."""
        q = query or title or composer
        if not q:
            return []
        url = f"{_BASE}/tunes/search?" + urllib.parse.urlencode(
            {"q": q, "format": "json", "perpage": limit}
        )
        data = self._get_json(url)
        hits = []
        for tune in data.get("tunes", [])[:limit]:
            hits.append(
                self._ref(
                    title=tune.get("name", ""),
                    id=str(tune.get("id", "")),
                    composer="traditional",
                    formats=("abc",),
                    url=tune.get("url", ""),
                    score=1.0,
                    metadata={"type": tune.get("type", "")},
                )
            )
        return hits

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Fetch the tune's first ABC setting and write it to a temp ``.abc`` file."""
        data = self._get_json(f"{_BASE}/tunes/{ref.id}?format=json")
        settings = data.get("settings") or []
        if not settings:
            raise RuntimeError(f"No ABC settings for tune {ref.id!r} on thesession.org")
        setting = settings[0]
        name = data.get("name", ref.title)
        tune_type = data.get("type", ref.metadata.get("type", ""))
        header = f"X:1\nT:{name}\n"
        if tune_type in _METER:
            header += f"M:{_METER[tune_type]}\n"
        if setting.get("key"):
            header += f"K:{setting['key']}\n"
        abc = header + (setting.get("abc", "") or "").replace("\r\n", "\n")

        fd, path = tempfile.mkstemp(suffix=".abc")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(abc)
        return path
