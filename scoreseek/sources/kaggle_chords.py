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

**The light path: the corpus index.** Holding the corpus as a DataFrame takes
about 1.1 GB per process. ``sung.chords_index`` keeps a SQLite+FTS5 index of the
same corpus (built once, by ``songleaf build-index`` or
``sung.chords_index.build_chords_index``) with the same song ids, read with the
standard library alone. When one is found (:func:`chords_index_path`), this
source reads it instead of the DataFrame, with the same search (substring over
title and artist) and the same fetched text (the row's chart verbatim). Pass
``index_path=`` to choose the file, ``dataset_loader=`` to force the DataFrame,
or ``index=`` for a songleaf-style ranked index (``songleaf.kaggle_index``).
"""

from __future__ import annotations

import os
import re
from functools import cached_property
from typing import Callable, List, Optional

from scoreseek import _http
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source


_DATASET_URL = "https://www.kaggle.com/datasets/eitanbentora/chords-and-lyrics-dataset"


def chords_index_path() -> Optional[str]:
    """The corpus index file, if there is one here: sung's, else songleaf's.

    ``sung.chords_index.local_chords_index`` (``$SUNG_CHORDS_INDEX``, or beside
    the corpus zip), then where songleaf keeps the index it builds. Neither
    lookup imports pandas.
    """
    try:
        from sung.chords_index import local_chords_index

        path = local_chords_index()
    except Exception:
        # Broad on purpose: a missing sung, or $SUNG_CHORDS_INDEX naming no file,
        # only means "no sung index here"; the other places still count.
        path = None
    if path is None:
        try:
            from songleaf.kaggle_index import kaggle_index_path

            path = kaggle_index_path()
        except Exception:
            # Broad on purpose, as above: no songleaf, or its dataset store misconfigured.
            return None
    return path if os.path.isfile(path) else None


class KaggleChordsSource(Source):
    """Search the Kaggle chords+lyrics corpus via ``sung`` (chords-over-lyrics text).

    Args:
        dataset_loader: Optional ``() -> pandas.DataFrame`` override (dependency
            injection for tests). Defaults to
            ``sung.chords_and_lyrics.get_lyrics_and_chords_dataset``, imported
            lazily on first use.
        index_path: A ``sung.chords_index`` file to read instead of the
            DataFrame. Defaults to :func:`chords_index_path` unless a
            ``dataset_loader`` or an ``index`` is given.
        index: A songleaf-style ranked source of the same corpus:
            ``search(title=, artist=, limit=)`` returning hits with ``id``,
            ``title``, ``artist``, ``score`` and ``meta``, and ``get(id)``
            returning a song that ``songleaf.export.to_text`` renders.
    """

    name = "kaggle_chords"
    license = License.GRAY

    def __init__(
        self,
        *,
        dataset_loader: Optional[Callable] = None,
        index=None,
        index_path: Optional[str] = None,
    ):
        self._loader = dataset_loader
        self._index = index
        if index_path is None and index is None and dataset_loader is None:
            index_path = chords_index_path()
        self._index_path = index_path

    @staticmethod
    def available() -> bool:
        """True if a corpus index is here, or ``sung`` + its Kaggle stack import.

        Catches both ``ImportError`` (missing deps) and ``OSError`` (``haggle``
        authenticates against Kaggle *at import time*, even for cache hits).
        """
        if chords_index_path() is not None:
            return True
        try:
            from sung.chords_and_lyrics import (  # noqa: F401
                get_lyrics_and_chords_dataset,
            )

            return True
        except Exception:
            # Broad on purpose: a missing sung or haggle is an ImportError, and
            # haggle authenticates against Kaggle at import time (OSError).
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
        if self._index is not None:
            return self._search_index(needle_title, composer, limit=limit)
        if self._index_path is not None:
            return self._search_chords_index(needle_title, composer, limit=limit)
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
                    url=_DATASET_URL,
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

    @cached_property
    def _index_columns(self) -> dict:
        from sung.chords_index import read_columns

        return read_columns(self._index_path)

    @cached_property
    def _index_row_of_id(self) -> dict:
        return {song_id: row for row, song_id in enumerate(self._index_columns["ids"])}

    def _search_chords_index(self, title, composer, *, limit) -> List[ScoreRef]:
        """The DataFrame search, over the index's columns: same hits, same order."""
        columns = self._index_columns
        title, composer = title.casefold(), composer.casefold()
        out = []
        names = ("ids", "titles", "artists", "langs", "popularity")
        for song_id, name, artist, lang, pop in zip(*(columns[k] for k in names)):
            if len(out) >= limit:
                break
            if title not in name.casefold() or composer not in artist.casefold():
                continue
            out.append(
                self._ref(
                    title=name,
                    id=song_id,
                    composer=artist,
                    formats=("text",),
                    url=_DATASET_URL,
                    score=pop / 100.0,
                    metadata={"artist_name": artist, "popularity": pop, "lang": lang},
                )
            )
        return out

    def _search_index(self, title, composer, *, limit) -> List[ScoreRef]:
        hits = self._index.search(title=title, artist=composer, limit=limit)
        return [
            self._ref(
                title=hit.title,
                id=str(hit.id),
                composer=hit.artist,
                formats=("text",),
                url=_DATASET_URL,
                score=float(hit.score),
                metadata={"artist_name": hit.artist, **(hit.meta or {})},
            )
            for hit in hits
        ]

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Write the song's chords-over-lyrics text to a ``.txt`` file.

        From the DataFrame or the corpus index, the row's text verbatim; from a
        songleaf-style ``index``, the chart re-rendered from the parsed song.
        """
        if self._index is not None:
            from songleaf.export import to_text

            text = to_text(self._index.get(str(ref.id)))
        elif self._index_path is not None:
            from sung.chords_index import read_chart

            row = self._index_row_of_id.get(str(ref.id))
            if row is None:
                raise KeyError(f"Kaggle chords: no row with id={ref.id!r}")
            text = read_chart(self._index_path, row)
        else:
            df = self._dataset()
            sel = df[df["Unnamed: 0"] == int(ref.id)]
            if sel.empty:
                raise KeyError(f"Kaggle chords: no row with id={ref.id!r}")
            text = sel.iloc[0]["chords&lyrics"]
        out = _http.cache_dir("kaggle_chords") / f"kaggle_{ref.id}.txt"
        out.write_text(str(text), encoding="utf-8")
        return str(out)
