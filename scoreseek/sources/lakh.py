"""Lakh MIDI source: search a local copy of the Lakh MIDI Dataset's clean subset.

The Lakh MIDI Dataset (Raffel, 2016) ``clean_midi`` subset is ~17K MIDI files
named ``<Artist>/<Title>.mid``: popular music no public-domain corpus covers.
The dataset is distributed under CC-BY 4.0, but the files themselves are
transcriptions of copyrighted songs scraped from the web, so hits are tagged
:attr:`License.GRAY` (opt in with ``allow_copyrighted=True``).

Point the source at either the downloaded ``clean_midi.tar.gz`` (searched
without extracting; :meth:`fetch` extracts one member into the cache) or an
extracted ``clean_midi/`` folder. One-time download (234 MB)::

    http://hog.ee.columbia.edu/craffel/lmd/clean_midi.tar.gz
"""

from __future__ import annotations

import tarfile
from functools import cached_property
from pathlib import Path
from typing import List, Optional

from scoreseek import _http
from scoreseek._match import match_score
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

LAKH_CLEAN_URL = "http://hog.ee.columbia.edu/craffel/lmd/clean_midi.tar.gz"


def _artist_title(rel: str):
    """``clean_midi/Artist/Title.2.mid`` -> ``("Artist", "Title")``."""
    parts = Path(rel).parts
    artist = parts[-2] if len(parts) >= 2 else ""
    stem = Path(parts[-1]).stem
    head, _, tail = stem.rpartition(".")
    title = head if tail.isdigit() and head else stem  # drop ".2" version suffixes
    return artist, title


def _is_safe(rel: str) -> bool:
    """A relative path that stays inside its root (no absolute, no ``..``)."""
    p = Path(rel)
    return (
        not p.is_absolute() and ".." not in p.parts and not rel.startswith(("/", "\\"))
    )


class LakhMidiSource(Source):
    """Search the Lakh ``clean_midi`` subset (a tarball or an extracted folder).

    Args:
        path: ``clean_midi.tar.gz`` or the extracted ``clean_midi`` directory.
        name: Registry name (default ``'lakh'``).
    """

    name = "lakh"
    license = License.GRAY
    uniform_license = True

    def __init__(self, path, *, name: str = "lakh"):
        self.name = name
        self.path = Path(path).expanduser()
        if not self.path.exists():
            raise FileNotFoundError(
                f"Lakh MIDI not found at {self.path}. Download the clean subset "
                f"once (234 MB) from {LAKH_CLEAN_URL}."
            )

    @cached_property
    def _members(self) -> List[str]:
        """Relative paths of every ``.mid`` file (read once, then cached)."""
        if self.path.is_dir():
            names = (str(p.relative_to(self.path)) for p in self.path.rglob("*"))
        else:
            with tarfile.open(self.path, "r:*") as tar:
                names = tar.getnames()
        return sorted(m for m in names if m.lower().endswith(".mid") and _is_safe(m))

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Match the query against ``Artist`` and ``Title`` from the file names."""
        hits = []
        for rel in self._members:
            artist, cand_title = _artist_title(rel)
            score = match_score(
                query, title, composer, cand_title=cand_title, cand_composer=artist
            )
            if score is None:
                continue
            hits.append(
                self._ref(
                    title=cand_title,
                    id=rel,
                    composer=artist,
                    formats=("midi",),
                    url=LAKH_CLEAN_URL,
                    score=score,
                    metadata={"member": rel},
                )
            )
        hits.sort(key=lambda r: -r.score)
        return hits[:limit]

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Return a local path to the MIDI (extracting from the tarball if needed)."""
        if fmt not in (None, "midi", "mid"):
            raise ValueError(f"lakh only serves MIDI, not {fmt!r}")
        rel = ref.metadata.get("member") or ref.id
        if not _is_safe(rel):
            raise ValueError(f"refusing unsafe member path {rel!r}")
        if self.path.is_dir():
            return str(self.path / rel)
        out = _http.cache_dir("lakh") / rel
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            with tarfile.open(self.path, "r:*") as tar:
                member = tar.extractfile(rel)
                if member is None:
                    raise FileNotFoundError(f"{rel!r} not in {self.path}")
                out.write_bytes(member.read())
        return str(out)
