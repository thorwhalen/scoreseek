"""OpenScore Lieder source: ~1,356 CC0 art songs from the OpenScore corpus.

A GitHub-hosted, CC0 corpus (`github.com/OpenScore/Lieder
<https://github.com/OpenScore/Lieder>`_) of nineteenth-century art songs. There
is no search API: this source fetches one small TSV manifest
(``data/scores.tsv``) once, caches it, and searches it **in memory** by
title/composer. :meth:`~OpenScoreLiederSource.fetch` downloads a single score
file (compressed MusicXML ``.mxl`` by default) from ``raw.githubusercontent.com``.
Stdlib-only (``csv`` + ``urllib``), so scoreseek stays zero-dependency.

Note: only ``.mxl``/``.mscx``/``.mscz`` are committed to the repo (no MIDI) --
let ``audiate`` derive MIDI from the MusicXML downstream.
"""

from __future__ import annotations

import csv
import io
import urllib.parse
from functools import cached_property
from typing import List, Optional

from scoreseek import _http
from scoreseek._match import match_score
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_RAW = "https://raw.githubusercontent.com/OpenScore/Lieder/main"
_SCORES_TSV = f"{_RAW}/data/scores.tsv"
_COMPOSERS_TSV = f"{_RAW}/data/composers.tsv"

#: Requested format label -> committed file extension. Default is compressed MusicXML.
_EXT = {
    "musicxml": "mxl",
    "mxl": "mxl",
    "mscx": "mscx",
    "mscz": "mscz",
    "musescore": "mscz",
}


class OpenScoreLiederSource(Source):
    """Search the OpenScore Lieder CC0 corpus (compressed MusicXML)."""

    name = "openscore_lieder"
    license = License.CC0

    def __init__(self, *, timeout: float = 15.0):
        self.timeout = timeout

    # -- networking (thin wrappers so tests can monkeypatch at the instance) --
    def _get_text(self, url: str) -> str:
        return _http.get_text(url, timeout=self.timeout)

    def _get_bytes(self, url: str) -> bytes:
        return _http.get_bytes(url, timeout=self.timeout)

    # -- cached manifests (fetched once per source instance) --
    @cached_property
    def _rows(self) -> list:
        text = self._get_text(_SCORES_TSV)
        return list(csv.DictReader(io.StringIO(text), delimiter="\t"))

    @cached_property
    def _composers(self) -> dict:
        """{path_folder -> clean 'First Last' name} from composers.tsv (best-effort)."""
        try:
            text = self._get_text(_COMPOSERS_TSV)
        except Exception:
            return {}
        return {
            row["path"]: row.get("name", "")
            for row in csv.DictReader(io.StringIO(text), delimiter="\t")
            if row.get("path")
        }

    def _composer_of(self, path: str) -> str:
        folder = path.split("/")[0]
        return self._composers.get(folder) or folder.replace("_", " ")

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Substring-search the cached manifest by title/composer/free-text query."""
        hits = []
        for row in self._rows:
            name = row.get("name", "")
            path = row.get("path", "")
            comp = self._composer_of(path)
            score = match_score(
                query, title, composer, cand_title=name, cand_composer=comp
            )
            if score is None:
                continue
            hits.append(
                self._ref(
                    title=name,
                    id=row.get("id", ""),
                    composer=comp,
                    formats=("musicxml", "musescore"),
                    url=row.get("link", ""),
                    score=score,
                    metadata={
                        "path": path,
                        "imslp": row.get("imslp", ""),
                        "set_id": row.get("set_id", ""),
                        "link": row.get("link", ""),
                    },
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Download the score file (``.mxl`` by default) and return a cached path."""
        ext = _EXT.get((fmt or "").lower(), "mxl")
        path = ref.metadata.get("path")
        if not path:
            raise RuntimeError(
                f"OpenScore ref {ref.title!r} has no 'path' metadata to fetch"
            )
        enc = "/".join(urllib.parse.quote(seg) for seg in path.split("/"))
        url = f"{_RAW}/scores/{enc}/lc{ref.id}.{ext}"
        data = self._get_bytes(url)
        out = _http.cache_dir("openscore_lieder") / f"lc{ref.id}.{ext}"
        out.write_bytes(data)
        return str(out)
