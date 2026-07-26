"""Chordonomicon source (GRAY lane): ~680K song chord progressions.

The `Chordonomicon <https://huggingface.co/datasets/ailsntua/Chordonomicon>`_
HuggingFace dataset, read live over the free datasets-server JSON API
(stdlib ``urllib`` -- no ``huggingface_hub``/``datasets``/``pandas``).

Important shape facts (verified): the dataset has **no human title or artist
name** -- only an opaque ``artist_id`` plus Spotify IDs -- so it cannot answer
``title=``/``composer=`` searches. It is a **genre / chord-text** search source:
scoreseek's ``query`` is routed into the full-text ``/search`` endpoint (which
indexes the chord text and genre columns). :meth:`~ChordonomiconSource.fetch`
retrieves one row by id and writes its ``chords`` string to a ``.txt`` file.

License is **CC-BY-NC-4.0** -> :attr:`License.NONCOMMERCIAL` (gated behind
``allow_copyrighted=True``). Attribution/citation required (arXiv:2410.22046).

The ``chords`` notation is custom (``Cs``=C#, ``Ab`` flats, ``min`` minor,
``A/Cs`` slash, ``Gno3d`` power chords, ``<verse_1>`` section markers) -- kept
verbatim; downstream (audiate/accompy) normalizes it.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import List, Optional

from scoreseek import _http
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_DS = "ailsntua/Chordonomicon"
_BASE = "https://datasets-server.huggingface.co"
_FIXED = {"dataset": _DS, "config": "default", "split": "train"}


class ChordonomiconSource(Source):
    """Search Chordonomicon chord progressions by chord-text / genre (NC license)."""

    name = "chordonomicon"
    license = License.NONCOMMERCIAL

    def __init__(
        self, *, timeout: float = 20.0, retries: int = 3, retry_wait: float = 5.0
    ):
        self.timeout = timeout
        self.retries = retries
        self.retry_wait = retry_wait

    def _get_json(self, url: str) -> dict:
        return _http.get_json(url, timeout=self.timeout)

    def _get_json_retrying(self, url: str) -> dict:
        """GET with retry: the duckdb search index spins down when idle (HTTP 500)."""
        attempts = max(self.retries, 1)  # always try at least once
        last = None
        for attempt in range(attempts):
            try:
                return self._get_json(url)
            except Exception as e:  # HTTPError 500 "index is loading", transient
                last = e
                if attempt < attempts - 1:
                    time.sleep(self.retry_wait)
        raise last

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Full-text search over chord text + genres. ``title``/``composer`` are
        folded into the free-text query (there is no real title/artist column)."""
        q = " ".join(t for t in (query, title, composer) if t).strip()
        if not q:
            return []
        params = dict(_FIXED, query=q, offset=0, length=min(max(limit, 1), 100))
        url = f"{_BASE}/search?" + urllib.parse.urlencode(params)
        data = self._get_json_retrying(url)
        hits = []
        for rec in (data.get("rows") or [])[:limit]:
            row = rec.get("row") or {}
            rid = str(row.get("id", ""))
            main_genre = row.get("main_genre")
            title_str = f"Chordonomicon #{rid}"
            if main_genre:
                title_str += f" ({main_genre})"
            # Prefer the authoritative row position the server returns; only fall
            # back to id-1 (assumes contiguous 1-based ids) when it is absent.
            row_idx = rec.get("row_idx")
            viewer_row = row_idx if row_idx is not None else _row_index(rid)
            hits.append(
                self._ref(
                    title=title_str,
                    id=rid,
                    composer="",  # opaque artist_id only; stashed in metadata
                    formats=("chords",),
                    url=f"https://huggingface.co/datasets/{_DS}/viewer/default/train?row={viewer_row}",
                    score=0.5,  # endpoint returns no per-row relevance
                    metadata={
                        "genres": row.get("genres"),
                        "main_genre": main_genre,
                        "rock_genre": row.get("rock_genre"),
                        "decade": row.get("decade"),
                        "release_date": row.get("release_date"),
                        "artist_id": row.get("artist_id"),
                        "spotify_song_id": row.get("spotify_song_id"),
                        "spotify_artist_id": row.get("spotify_artist_id"),
                    },
                )
            )
        return hits

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Fetch the row by id and write its chord progression to a ``.txt`` file."""
        where = urllib.parse.quote(f'"id" = {int(ref.id)}')
        params = dict(_FIXED, offset=0, length=1)
        url = f"{_BASE}/filter?" + urllib.parse.urlencode(params) + f"&where={where}"
        data = self._get_json_retrying(url)
        rows = data.get("rows") or []
        if not rows:
            raise KeyError(f"Chordonomicon: no row with id={ref.id!r}")
        chords = rows[0].get("row", {}).get("chords", "")
        out = _http.cache_dir("chordonomicon") / f"chordonomicon_{ref.id}.txt"
        out.write_text(chords, encoding="utf-8")
        return str(out)


def _row_index(rid: str) -> str:
    """Viewer row index is 0-based; ids are 1-based sequential."""
    try:
        return str(int(rid) - 1)
    except (TypeError, ValueError):
        return "0"
