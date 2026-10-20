"""A local-folder score source: index and search a directory of score files.

Zero network, zero external services -- point it at a folder of ``.mid`` /
MusicXML / ABC / ``kern`` files and search by filename. Fetching returns the
file path (ready for ``audiate.render``). This is the simplest possible source
and the reference implementation of the :class:`~scoreseek.sources.base.Source`
contract.

It also owns the folder scan itself (:func:`scan_folder`, :data:`SCORE_FORMATS`):
``songleaf``'s ``LocalFolderSource`` builds on them and adds its chart parsing
(thorwhalen/scoreseek#7).
"""

from __future__ import annotations

import os
from pathlib import Path
from collections.abc import Iterator, Mapping
from typing import List, Optional

from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

#: File extensions treated as scores, mapped to a format label. The one table for
#: score files on disk: notation formats, plus tab and print formats (Guitar Pro,
#: MuseScore, PDF) a site lets a user export.
SCORE_FORMATS = {
    ".mid": "midi",
    ".midi": "midi",
    ".xml": "musicxml",
    ".musicxml": "musicxml",
    ".mxl": "musicxml",
    ".abc": "abc",
    ".krn": "kern",
    ".kern": "kern",
    ".mei": "mei",
    ".mscz": "musescore",
    ".gp": "guitarpro",
    ".gpx": "guitarpro",
    ".gp3": "guitarpro",
    ".gp4": "guitarpro",
    ".gp5": "guitarpro",
    ".pdf": "pdf",
}


def title_and_artist(stem: str) -> tuple[str, str]:
    """``"Artist - Title"`` splits; anything else is a bare title.

    >>> title_and_artist("Nobody - Paper Boats")
    ('Paper Boats', 'Nobody')
    >>> title_and_artist("Paper Boats")
    ('Paper Boats', '')
    """
    artist, sep, title = stem.partition(" - ")
    return (title, artist) if sep else (stem, "")


def scan_folder(
    root, *, formats: Mapping[str, str] = SCORE_FORMATS
) -> Iterator[dict]:
    """Yield one entry per file under ``root`` whose extension is in ``formats``.

    Each entry has ``id`` (the path relative to ``root``), ``path``, ``stem``,
    ``ext`` (lowercased), ``format`` (``formats[ext]``), and ``title`` and
    ``artist`` (from :func:`title_and_artist`). A missing ``root`` yields nothing.

    >>> import tempfile, pathlib
    >>> root = pathlib.Path(tempfile.mkdtemp())
    >>> _ = (root / "Nobody - Paper Boats.mid").write_bytes(b"")
    >>> _ = (root / "notes.txt").write_text("not a score")
    >>> [(e["id"], e["format"], e["title"], e["artist"]) for e in scan_folder(root)]
    [('Nobody - Paper Boats.mid', 'midi', 'Paper Boats', 'Nobody')]
    """
    root = os.path.expanduser(str(root))
    for dirpath, _dirs, files in os.walk(root):
        for fname in sorted(files):
            stem, ext = os.path.splitext(fname)
            fmt = formats.get(ext.lower())
            if fmt is None:
                continue
            path = os.path.join(dirpath, fname)
            title, artist = title_and_artist(stem)
            yield {
                "id": os.path.relpath(path, root),
                "path": path,
                "stem": stem,
                "ext": ext.lower(),
                "format": fmt,
                "title": title,
                "artist": artist,
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
        self._index = [
            {
                "title": entry["stem"],
                "path": entry["path"],
                "format": entry["format"],
                "rel": entry["id"],
            }
            for entry in scan_folder(self.root)
        ]
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
