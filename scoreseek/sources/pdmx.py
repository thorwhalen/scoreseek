"""PDMX source: search a local extraction of the Public Domain MusicXML corpus.

PDMX (>250K CC0/public-domain MusicXML scores scraped from MuseScore) is
distributed **only** as monolithic tarballs -- there is no per-file API (the HF
viewer is disabled and the IPFS CIDs are unpinned). So this is a *local-corpus*
source: point it at a ``pdmx_root`` directory containing ``PDMX.csv`` (and,
for :meth:`~PDMXSource.fetch`, the extracted ``mxl/`` / ``mid/`` trees).

* :meth:`~PDMXSource.search` streams the rich CSV index (stdlib ``csv``) --
  title, composer, genre, per-row license, and relative file paths.
* :meth:`~PDMXSource.fetch` returns the local file path; if the corpus wasn't
  downloaded it raises an informative error naming the Zenodo tarball.

One-time download (opt-in, heavy -- ~2.1 GB for CSV + MusicXML, more for MIDI/PDF)::

    # index only (225 MB) -- search works, fetch raises until you add the corpus:
    https://zenodo.org/records/15571083/files/PDMX.csv?download=1
    # + the MusicXML corpus (1.9 GB) and optional MIDI (214 MB):
    https://zenodo.org/records/15571083/files/mxl.tar.gz?download=1
    https://zenodo.org/records/15571083/files/mid.tar.gz?download=1
    # extract the tarballs into pdmx_root (they expand to mxl/, mid/, ...).

Stdlib-only for both search and fetch. Per-row license comes from the CSV
``license`` column; ~12% of rows carry ``license_conflict=True`` (PD/CC0 claim
doubtful) -- ``clean_only=True`` (default) drops those from the clean lane.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

from scoreseek._match import match_score
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_ZENODO = "https://zenodo.org/records/15571083"

#: PDMX ``license`` column value -> :class:`License`.
_LICENSE = {"publicdomain": License.PUBLIC_DOMAIN, "cc-zero": License.CC0}

#: Requested format -> (CSV column, extracted-tree subdir).
_FMT_COL = {"musicxml": "mxl", "mxl": "mxl", "midi": "mid", "mid": "mid", "pdf": "pdf"}


def _na(value):
    """PDMX encodes missing values as the literal string ``"NA"``."""
    return None if value in (None, "", "NA") else value


class PDMXSource(Source):
    """Search a local PDMX corpus (CC0/public-domain MusicXML) by its CSV index.

    Args:
        pdmx_root: Directory holding ``PDMX.csv`` and (for fetch) the extracted
            ``mxl/``/``mid/`` trees.
        name: Registry name (default ``'pdmx'``).
        clean_only: Drop rows flagged ``license_conflict`` (default ``True``) so
            the lane stays cleanly PD/CC0.
        csv_name: Index filename under ``pdmx_root`` (default ``'PDMX.csv'``).
    """

    name = "pdmx"
    license = License.PUBLIC_DOMAIN

    def __init__(
        self,
        pdmx_root,
        *,
        name: str = "pdmx",
        clean_only: bool = True,
        csv_name: str = "PDMX.csv",
    ):
        self.name = name
        self.root = Path(pdmx_root).expanduser()
        self.clean_only = clean_only
        self.csv_path = self.root / csv_name

    def _iter_rows(self):
        if not self.csv_path.exists():
            raise FileNotFoundError(
                f"PDMX index not found at {self.csv_path}. Download it once from "
                f"{_ZENODO}/files/PDMX.csv?download=1 (225 MB) into {self.root}."
            )
        with open(self.csv_path, newline="", encoding="utf-8") as f:
            yield from csv.DictReader(f)

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Stream the CSV index and substring-match title/composer/query."""
        hits: List[ScoreRef] = []
        for row in self._iter_rows():
            conflict = row.get("license_conflict") == "True"
            if self.clean_only and conflict:
                continue
            if _na(row.get("mxl")) is None and _na(row.get("mid")) is None:
                continue  # skip the handful of corrupt rows with no score file
            cand_title = _na(row.get("title")) or _na(row.get("song_name")) or ""
            cand_comp = (
                _na(row.get("composer_name")) or _na(row.get("artist_name")) or ""
            )
            score = match_score(
                query, title, composer, cand_title=cand_title, cand_composer=cand_comp
            )
            if score is None:
                continue
            # Unknown/unspecified license strings default to the gated GRAY lane
            # (matches imslp.py + base.py's "scraped/unspecified -> GRAY" rule).
            lic = (
                License.GRAY
                if conflict
                else _LICENSE.get(_na(row.get("license")) or "", License.GRAY)
            )
            mxl, mid = _na(row.get("mxl")), _na(row.get("mid"))
            formats = tuple(
                f for f, present in (("musicxml", mxl), ("midi", mid)) if present
            )
            hits.append(
                self._ref(
                    title=cand_title or "(untitled)",
                    id=_cid(mxl or mid or ""),
                    composer=cand_comp,
                    formats=formats,
                    license=lic,
                    url=_ZENODO,
                    score=score,
                    metadata={
                        "mxl": mxl,
                        "mid": mid,
                        "genres": _na(row.get("genres")),
                        "n_tracks": _na(row.get("n_tracks")),
                        "rating": _na(row.get("rating")),
                        "license_conflict": conflict,
                    },
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Return the local path to *ref*'s score file (no network).

        Raises ``FileNotFoundError`` with the exact Zenodo tarball URL if the
        corpus for the requested format hasn't been downloaded/extracted.
        """
        col = _FMT_COL.get((fmt or "musicxml").lower())
        if col is None:
            raise ValueError(
                f"PDMX: unsupported fmt {fmt!r} (use 'musicxml' or 'midi')"
            )
        # search() only indexes the mxl/mid paths, so only those are fetchable.
        rel = ref.metadata.get(col)
        if not rel:
            have = [k for k in ("mxl", "mid") if ref.metadata.get(k)]
            raise ValueError(
                f"PDMX ref {ref.title!r} has no {col!r} file for fmt={fmt!r} "
                f"(available: {have})"
            )
        local = self.root / rel.lstrip("./")
        if not local.exists():
            tar = {"mxl": "mxl.tar.gz", "mid": "mid.tar.gz"}[col]
            raise FileNotFoundError(
                f"PDMX file not found: {local}. Download the {col} corpus once from "
                f"{_ZENODO}/files/{tar}?download=1 and extract it into {self.root}."
            )
        return str(local)


def _cid(rel_path: str) -> str:
    """Extract the content-addressed id (CID) from a PDMX relative file path."""
    stem = rel_path.rsplit("/", 1)[-1]
    return stem.rsplit(".", 1)[0] if "." in stem else stem
