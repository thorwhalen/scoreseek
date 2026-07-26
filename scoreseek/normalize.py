"""Query normalization via MusicBrainz -- an identity *pre-layer*, not a source.

:class:`MusicBrainzNormalizer` resolves a fuzzy ``"title [+ artist]"`` into a
canonical work (or recording) with a stable MBID, canonical title, composer, and
aliases, using the MusicBrainz ``ws/2`` JSON API. ``scoreseek.search(normalize=True)``
calls it *before* fanning out to the real score sources, so a messy query
("moonlight sonata beethovan") becomes a clean, canonical one.

It is deliberately **not** a :class:`~scoreseek.sources.base.Source` (it retrieves
no scores) and is **opt-in**: every call is a network round-trip throttled to
~1 request/second, and MusicBrainz requires a descriptive non-empty
``User-Agent`` (a blank UA returns HTTP 403).

Stdlib-only (``urllib`` + ``json`` + ``time``).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

_BASE = "https://musicbrainz.org/ws/2"

#: Lucene special characters to escape inside a quoted MusicBrainz query value.
_LUCENE = r'+-&|!(){}[]^"~*?:\/'


def _quote_lucene(value: str) -> str:
    """Wrap a value in double quotes with internal quotes/backslashes escaped."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MusicBrainzNormalizer:
    """Canonicalize a query via MusicBrainz ``ws/2`` (throttled, UA-identified).

    Args:
        contact: A contact URL or email for the mandatory User-Agent (MusicBrainz
            policy). Defaults to the scoreseek repo URL.
        app_name / app_version: Compose the User-Agent with ``contact``.
        min_interval: Minimum seconds between requests (client-side throttle).
        threshold: Minimum MusicBrainz relevance (0-100) to accept a work hit.
        timeout / max_retries: HTTP behavior.
    """

    def __init__(
        self,
        *,
        contact: str = "https://github.com/thorwhalen/scoreseek",
        app_name: str = "scoreseek",
        app_version: str = "0.0.1",
        min_interval: float = 1.0,
        threshold: int = 85,
        timeout: float = 10.0,
        max_retries: int = 3,
    ):
        self.user_agent = f"{app_name}/{app_version} ( {contact} )"
        self.min_interval = min_interval
        self.threshold = threshold
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0

    def _get_json(self, url: str) -> dict:
        """Throttled GET with the mandatory descriptive User-Agent."""
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
        )
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self._last_request = time.monotonic()
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:  # 503 rate-limited -> back off
                self._last_request = time.monotonic()
                if e.code == 503 and attempt < self.max_retries - 1:
                    time.sleep(float(e.headers.get("Retry-After", 1)))
                    continue
                raise
        return {}

    def _search(self, entity: str, lucene: str, limit: int) -> list:
        url = f"{_BASE}/{entity}?" + urllib.parse.urlencode(
            {"query": lucene, "fmt": "json", "limit": limit}
        )
        data = self._get_json(url)
        return data.get(f"{entity}s") or []

    def normalize(
        self, query: str, *, artist: Optional[str] = None, limit: int = 3
    ) -> Optional[dict]:
        """Return canonical ``{mbid, title, artist, composer, aliases, ...}`` or None.

        Tries a **work** search first (works carry composer relations inline);
        falls back to a **recording** search for a performance-credited artist.
        Returns ``None`` if nothing clears :attr:`threshold` -- callers then use
        the raw, un-normalized query.
        """
        if not query:
            return None
        lucene = _quote_lucene(query)
        if artist:
            lucene += f" AND artist:{_quote_lucene(artist)}"

        works = self._search("work", lucene, limit)
        if works and works[0].get("score", 0) >= self.threshold:
            return _work_result(works[0], fallback_artist=artist)

        recordings = self._search("recording", lucene, limit)
        if recordings and recordings[0].get("score", 0) >= self.threshold:
            return _recording_result(recordings[0])
        return None


def _work_result(work: dict, *, fallback_artist: Optional[str]) -> dict:
    composer = None
    for rel in work.get("relations", []) or []:
        if rel.get("type") == "composer":
            composer = (rel.get("artist") or {}).get("name")
            break
    return {
        "entity": "work",
        "source": "musicbrainz",
        "mbid": work.get("id"),
        "title": work.get("title"),
        "composer": composer,
        "artist": composer or fallback_artist,
        "aliases": [
            a.get("name") for a in work.get("aliases", []) or [] if a.get("name")
        ],
        "iswcs": work.get("iswcs") or [],
        "disambiguation": work.get("disambiguation", ""),
        "score": round(work.get("score", 0) / 100.0, 3),
    }


def _recording_result(rec: dict) -> dict:
    parts = []
    for ac in rec.get("artist-credit", []) or []:
        parts.append(ac.get("name", ""))
        parts.append(ac.get("joinphrase", ""))
    artist = "".join(parts).strip() or None
    return {
        "entity": "recording",
        "source": "musicbrainz",
        "mbid": rec.get("id"),
        "title": rec.get("title"),
        "composer": None,
        "artist": artist,
        "aliases": [],
        "first_release_date": rec.get("first-release-date"),
        "score": round(rec.get("score", 0) / 100.0, 3),
    }
