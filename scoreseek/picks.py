"""Choosing one file among a hit's :class:`~scoreseek.base.ScoreFile` entries.

A source whose hits bundle several files (IMSLP: full scores, parts,
arrangements, MIDI renditions) lists them with ``files()`` and lets ``fetch``
choose. The choice is split in two so each half is swappable:

* :func:`candidates` -- the hard filters: requested format and size cap. It
  raises an informative error when nothing survives.
* a **pick strategy**, ``(List[ScoreFile]) -> ScoreFile`` -- the preference among
  the survivors: :func:`prefer_safe_renderable` (the default) or :func:`smallest`,
  or any callable of that shape.

Sizes are often displayed rounded (IMSLP shows ``"7.96MB"``), so a source may
record an upper bound as ``metadata["size_max"]``; the cap is checked against it,
so a file that "fits" by its listing never trips the download's own cap.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from scoreseek._http import human_size
from scoreseek.base import COMMERCIAL_SAFE, ScoreFile, ScoreTooLargeError

#: A pick strategy: choose one file among candidates.
Pick = Callable[[List[ScoreFile]], ScoreFile]

#: Requested ``fmt`` label -> the ``ScoreFile.kind`` it accepts.
FMT_KINDS = {
    "midi": "midi",
    "mid": "midi",
    "musicxml": "musicxml",
    "mxl": "musicxml",
    "xml": "musicxml",
    "pdf": "pdf",
    "zip": "zip",
}
#: Kinds a synthesizer can render to audio directly.
RENDERABLE_KINDS = frozenset({"midi", "musicxml"})

#: "Parts", "Part 2", "Flute part" -- but not "Partitur" (full score), "Partita",
#: or "Part-songs".
_PART_RE = re.compile(r"\bparts?\b(?!-)", re.IGNORECASE)


def kind_for_fmt(fmt: str) -> str:
    """The ``ScoreFile.kind`` a requested ``fmt`` means (``"mid"`` -> ``"midi"``)."""
    label = fmt.lower().lstrip(".")
    return FMT_KINDS.get(label, label)


def is_part(f: ScoreFile) -> bool:
    """True if *f* looks like a single instrument's part rather than a score."""
    return bool(_PART_RE.search(f"{f.section} {f.description}"))


def size_bound(f: ScoreFile) -> Optional[int]:
    """The largest *f* can be: ``metadata["size_max"]`` if given, else ``size``."""
    return f.metadata.get("size_max", f.size)


def over_cap(f: ScoreFile, max_bytes: Optional[int]) -> bool:
    """True if *f* is known (or may be) larger than ``max_bytes`` (``None``: no cap)."""
    bound = size_bound(f)
    return max_bytes is not None and bound is not None and bound > max_bytes


# -- strategies: (List[ScoreFile]) -> ScoreFile --


def prefer_safe_renderable(files: List[ScoreFile]) -> ScoreFile:
    """Default pick, in priority order:

    1. a commercial-safe license (PD/CC0/permissive) -- so a default, copyright-safe
       search never silently hands back a non-commercial file;
    2. a renderable kind (MIDI/MusicXML) over PDF/zip;
    3. a score over a single part;
    4. a known size over an unknown one (only a known size is checked up front);
    5. the listing's own order (a source lists its main editions first).
    """

    def rank(item):
        i, f = item
        return (
            f.license not in COMMERCIAL_SAFE,
            f.kind not in RENDERABLE_KINDS,
            is_part(f),
            f.size is None,
            i,
        )

    return min(enumerate(files), key=rank)[1]


def smallest(files: List[ScoreFile]) -> ScoreFile:
    """A pick strategy: the smallest file (unknown sizes last)."""
    return min(files, key=lambda f: (f.size is None, f.size or 0))


# -- the hard filters, and applying a pick --


def candidates(
    files: List[ScoreFile],
    *,
    fmt: Optional[str] = None,
    max_bytes: Optional[int] = None,
    where: str = "",
) -> List[ScoreFile]:
    """The files matching ``fmt`` that fit ``max_bytes``, in listing order.

    Raises:
        RuntimeError: No file matches ``fmt``.
        ScoreTooLargeError: Files match but all exceed the cap; the message names
            the smallest ones and how to get past the cap.
    """
    if fmt:
        kind = kind_for_fmt(fmt)
        matching = [f for f in files if f.kind == kind]
        if not matching:
            raise RuntimeError(
                f"{where}has no {fmt} file "
                f"(available: {sorted({f.kind for f in files})})"
            )
    else:
        matching = list(files)
    fitting = [f for f in matching if not over_cap(f, max_bytes)]
    if not fitting:
        raise too_large_error(matching, fmt=fmt, max_bytes=max_bytes, where=where)
    return fitting


def too_large_error(
    files: List[ScoreFile], *, fmt=None, max_bytes, where=""
) -> ScoreTooLargeError:
    """A :class:`ScoreTooLargeError` naming the smallest of *files*."""
    by_size = sorted(files, key=lambda f: (f.size is None, f.size or 0))
    listed = ", ".join(f"{f.filename} ({human_size(f.size)})" for f in by_size[:5])
    return ScoreTooLargeError(
        f"{where}has {len(files)} {fmt or 'score'} file(s), all over "
        f"max_bytes={max_bytes} ({human_size(max_bytes)}). Smallest: {listed}. "
        "Pass a larger max_bytes (None for no cap), or pick=<filename> from "
        "ref.files()."
    )


def _listed_size(f: ScoreFile) -> str:
    bound = size_bound(f)
    if bound is not None and f.size is not None and bound != f.size:
        return f"listed as {human_size(f.size)} (up to {human_size(bound)})"
    return human_size(f.size)


def apply_pick(pick: Pick, files: List[ScoreFile], *, where: str = "") -> ScoreFile:
    """Run a pick strategy and check it returned one of *files*."""
    chosen = pick(files)
    if chosen not in files:
        raise RuntimeError(
            f"{where}the pick strategy {getattr(pick, '__name__', pick)!r} returned "
            f"{chosen!r}, which is not one of the candidate ScoreFile entries"
        )
    return chosen


def by_filename(
    files: List[ScoreFile],
    filename: str,
    *,
    fmt: Optional[str] = None,
    max_bytes: Optional[int] = None,
    where: str = "",
) -> ScoreFile:
    """The file named *filename*, checked against ``fmt`` and ``max_bytes``."""
    chosen = next((f for f in files if f.filename == filename), None)
    if chosen is None:
        raise RuntimeError(
            f"{where}has no file named {filename!r} "
            f"(see ref.files(); available: {[f.filename for f in files]})"
        )
    if fmt and chosen.kind != kind_for_fmt(fmt):
        raise RuntimeError(f"{where}{filename!r} is a {chosen.kind} file, not {fmt}")
    if over_cap(chosen, max_bytes):
        raise ScoreTooLargeError(
            f"{where}{filename!r} is {_listed_size(chosen)}, over "
            f"max_bytes={max_bytes} ({human_size(max_bytes)}). "
            "Pass a larger max_bytes, or max_bytes=None for no cap."
        )
    return chosen
