"""Kaggle chords+lyrics source (GRAY lane): ~135K contemporary songs.

Delegates entirely to the local ``sung`` package (which downloads/caches the
Kaggle ``eitanbentora/chords-and-lyrics-dataset`` via ``haggle``) rather than
hitting any HTTP API. :meth:`~KaggleChordsSource.search` matches song/artist
names; :meth:`~KaggleChordsSource.fetch` writes the row's fixed-width
chords-over-lyrics text to a ``.txt`` file.

This is a **heavyweight, opt-in** source: enabling it pulls in ``sung`` +
``haggle`` + ``kaggle`` + ``pandas``, needs (free) Kaggle credentials
(``~/.kaggle/kaggle.json`` or ``KAGGLE_USERNAME``/``KAGGLE_KEY``), and downloads
a ~283 MB dataset on first use. Use :meth:`KaggleChordsSource.available` to check
before registering. License is a community scrape of e-chords ->
:attr:`License.GRAY` (gated behind ``allow_copyrighted=True``).

The corpus does **not** contain most copyrighted show tunes -- e.g. "You'll Be
Back" (Hamilton) is verified absent -- so expect misses on recent musical theatre.
"""

from __future__ import annotations

import re
from typing import Callable, List, Optional

from scoreseek import _http
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source


class KaggleChordsSource(Source):
    """Search the Kaggle chords+lyrics corpus via ``sung`` (chords-over-lyrics text).

    Args:
        dataset_loader: Optional ``() -> pandas.DataFrame`` override (dependency
            injection for tests). Defaults to
            ``sung.chords_and_lyrics.get_lyrics_and_chords_dataset``, imported
            lazily on first use.
    """

    name = "kaggle_chords"
    license = License.GRAY

    def __init__(self, *, dataset_loader: Optional[Callable] = None):
        self._loader = dataset_loader

    @staticmethod
    def available() -> bool:
        """True if ``sung`` + its Kaggle stack import and authenticate.

        Catches both ``ImportError`` (missing deps) and ``OSError`` (``haggle``
        authenticates against Kaggle *at import time*, even for cache hits).
        """
        try:
            from sung.chords_and_lyrics import (  # noqa: F401
                get_lyrics_and_chords_dataset,
            )

            return True
        except Exception:
            # Broad on purpose: importing sung pulls spotipy, and haggle
            # authenticates against Kaggle at import time (ImportError/OSError).
            return False

    def _dataset(self):
        if self._loader is None:
            from sung.chords_and_lyrics import get_lyrics_and_chords_dataset

            self._loader = get_lyrics_and_chords_dataset
        return self._loader()

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Case-insensitive substring match over song_name (+ artist_name)."""
        needle_title = (title or query).strip()
        if not needle_title and not composer:
            return []
        df = self._dataset()
        mask = None
        if needle_title:
            mask = df["song_name"].str.contains(
                re.escape(needle_title), case=False, na=False
            )
        if composer:
            am = df["artist_name"].str.contains(
                re.escape(composer), case=False, na=False
            )
            mask = am if mask is None else (mask & am)
        hits = df[mask] if mask is not None else df
        out = []
        for _, row in hits.head(limit).iterrows():
            pop = row.get("popularity")
            # pandas stores missing numerics as NaN (and bool(nan) is True, so
            # ``nan or 0`` would keep the NaN); ``pop != pop`` catches NaN.
            score = 0.0 if pop is None or pop != pop else float(pop) / 100.0
            out.append(
                self._ref(
                    title=str(row.get("song_name", "")),
                    id=str(int(row["Unnamed: 0"])),
                    composer=str(row.get("artist_name", "")),
                    formats=("text",),
                    url="https://www.kaggle.com/datasets/eitanbentora/chords-and-lyrics-dataset",
                    score=score,
                    metadata={
                        "artist_name": row.get("artist_name"),
                        "popularity": row.get("popularity"),
                        "genres": row.get("genres"),
                        "lang": row.get("lang"),
                        "name_e_chords": row.get("name_e_chords"),
                    },
                )
            )
        return out

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Write the row's chords-over-lyrics text verbatim to a ``.txt`` file."""
        df = self._dataset()
        sel = df[df["Unnamed: 0"] == int(ref.id)]
        if sel.empty:
            raise KeyError(f"Kaggle chords: no row with id={ref.id!r}")
        text = sel.iloc[0]["chords&lyrics"]
        out = _http.cache_dir("kaggle_chords") / f"kaggle_{ref.id}.txt"
        out.write_text(str(text), encoding="utf-8")
        return str(out)
