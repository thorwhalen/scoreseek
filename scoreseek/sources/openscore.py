"""OpenScore Lieder source: ~1,356 CC0 art songs from the OpenScore corpus.

A GitHub-hosted, CC0 corpus (`github.com/OpenScore/Lieder
<https://github.com/OpenScore/Lieder>`_) of nineteenth-century art songs. There
is no search API: this source fetches the corpus manifest (``data/scores.yaml``,
plus ``data/composers.yaml`` for display names) once, caches it, and searches it
**in memory** by title/composer. :meth:`~OpenScoreLiederSource.fetch` downloads a single score
file (compressed MusicXML ``.mxl`` by default) from ``raw.githubusercontent.com``.
Stdlib-only: the manifests are StrictYAML (flat ``id -> {key: scalar}`` maps), read
by the small :func:`_parse_flat_yaml` rather than a YAML dependency, so scoreseek
stays zero-dependency.

Note: only ``.mxl``/``.mscx``/``.mscz`` are committed to the repo (no MIDI) --
let ``audiate`` derive MIDI from the MusicXML downstream.
"""

from __future__ import annotations

import re
import urllib.parse
from functools import cached_property
from typing import List, Optional

from scoreseek import _http
from scoreseek._match import match_score
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_RAW = "https://raw.githubusercontent.com/OpenScore/Lieder/main"
_SCORES_YAML = f"{_RAW}/data/scores.yaml"
_COMPOSERS_YAML = f"{_RAW}/data/composers.yaml"
#: Human page for a score (the corpus is mirrored on MuseScore).
_SCORE_PAGE = "https://musescore.com/openscore-lieder-corpus/scores/{id}"

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
        """[{id, path, name, link, ...}] from scores.yaml (title from the path)."""
        rows = []
        for score_id, fields in _parse_flat_yaml(self._get_text(_SCORES_YAML)).items():
            path = fields.get("path", "")
            rows.append(
                {
                    **fields,
                    "id": score_id,
                    "name": fields.get("name") or _title_from_path(path),
                    "link": fields.get("link") or _SCORE_PAGE.format(id=score_id),
                }
            )
        return rows

    @cached_property
    def _composers(self) -> dict:
        """{path_folder -> clean 'First Last' name} from composers.yaml (best-effort)."""
        try:
            text = self._get_text(_COMPOSERS_YAML)
        except Exception:
            return {}
        return {
            row["path"]: row.get("name", "")
            for row in _parse_flat_yaml(text).values()
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
                        "language": row.get("language", ""),
                        "lyricist": row.get("lyricist", ""),
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


#: ``key: value`` at the top level (an id) or indented two spaces (a field).
_TOP_RE = re.compile(r"^([^\s#][^:]*):\s*$")
_FIELD_RE = re.compile(r"^  ([A-Za-z_][\w-]*):\s*(.*)$")


def _parse_flat_yaml(text: str) -> dict:
    """Parse a StrictYAML ``id -> {key: scalar}`` map (OpenScore's data/*.yaml).

    Only the two levels the manifests use are read; deeper structures (e.g. a
    score's ``audio:`` list) are skipped, and quoted scalars are unquoted.
    """
    out, current = {}, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if m := _TOP_RE.match(line):
            current = out.setdefault(m.group(1).strip(), {})
        elif current is not None and (m := _FIELD_RE.match(line)):
            value = m.group(2).strip()
            if value:  # an empty value opens a nested block: skip it
                current[m.group(1)] = _unquote(value)
    return out


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def _title_from_path(path: str) -> str:
    """``"Abrams,_Harriett/_/The_Orphan's_Prayer"`` -> ``"The Orphan's Prayer"``;
    a set member's ``"2_The_Ghost_Road"`` / ``"7c_Havanaise"`` drops its ordinal."""
    leaf = path.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"^\d+[a-z]?_", "", leaf).replace("_", " ").strip()
