"""A local-folder score source: index and search a directory of score files.

Zero network, zero external services -- point it at a folder of ``.mid`` /
MusicXML / ABC / ``kern`` files and search by filename. Fetching returns the
file path (ready for ``audiate.render``). This is the simplest possible source
and the reference implementation of the :class:`~scoreseek.sources.base.Source`
contract.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

#: File extensions treated as scores, mapped to a format label.
_SCORE_EXTS = {
    ".mid": "midi",
    ".midi": "midi",
    ".xml": "musicxml",
    ".musicxml": "musicxml",
    ".mxl": "musicxml",
    ".abc": "abc",
    ".krn": "kern",
    ".kern": "kern",
    ".mei": "mei",
}


class LocalFolderSource(Source):
    """Search a local folder of score files by filename.

    Args:
        root: Directory to index (searched recursively).
        name: Registry name (default ``'local'``).
        license: License tag to attach to every hit (default ``UNKNOWN`` -- set
            it if you know the folder's provenance, e.g. ``License.PUBLIC_DOMAIN``
            for a PD corpus).
    """

    def __init__(
        self,
        root,
        *,
        name: str = "local",
        license: License = License.UNKNOWN,
    ):
        self.name = name
        self.license = license
        self.root = Path(os.path.expanduser(str(root)))
        self._index: List[dict] = []
        self.reindex()

    def reindex(self) -> int:
        """(Re)build the file index. Returns the number of scores found."""
        self._index = []
        if not self.root.exists():
            return 0
        for dirpath, _dirs, files in os.walk(self.root):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                fmt = _SCORE_EXTS.get(ext)
                if fmt is None:
                    continue
                full = os.path.join(dirpath, fname)
                self._index.append(
                    {
                        "title": os.path.splitext(fname)[0],
                        "path": full,
                        "format": fmt,
                        "rel": os.path.relpath(full, self.root),
                    }
                )
        return len(self._index)

    def search(
        self,
        query: str = "",
        *,
        title: str = "",
        composer: str = "",
        limit: int = 10,
    ) -> List[ScoreRef]:
        """Substring-match the query against each file's title and relative path."""
        needles = [t.lower() for t in (query, title, composer) if t]
        hits = []
        for entry in self._index:
            haystack = (entry["title"] + " " + entry["rel"]).lower()
            if all(n in haystack for n in needles):
                hits.append(
                    self._ref(
                        title=entry["title"],
                        id=entry["rel"],
                        formats=(entry["format"],),
                        score=1.0 if needles else 0.5,
                        url=entry["path"],
                        metadata={"path": entry["path"]},
                    )
                )
        return hits[:limit]

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Return the score file's path (fetching a local file is a no-op)."""
        path = ref.metadata.get("path")
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Score file for {ref.title!r} not found: {path!r}")
        return path
