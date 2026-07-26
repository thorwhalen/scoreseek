"""Shared, source-agnostic relevance matching for in-memory / cached corpora.

Sources that search a local or cached index (OpenScore Lieder, PDMX, ...) share
this substring matcher so ranking is consistent across them. :func:`match_score`
returns a relevance in ``[0, 1]`` when every provided constraint matches, else
``None`` (meaning "not a hit").
"""

from __future__ import annotations

from typing import Optional


def _sub(needle: str, hay: str) -> Optional[float]:
    """Substring relevance: exact=1.0, prefix=0.8, contained=0.6, else None."""
    n, h = needle.lower().strip(), hay.lower()
    if not n:
        return None
    if n == h.strip():
        return 1.0
    if h.startswith(n):
        return 0.8
    if n in h:
        return 0.6
    return None


def match_score(
    query: str = "",
    title: str = "",
    composer: str = "",
    *,
    cand_title: str = "",
    cand_composer: str = "",
) -> Optional[float]:
    """Score a candidate against constraints; ``None`` if any constraint misses.

    - ``title`` must substring-match ``cand_title``.
    - ``composer`` must substring-match ``cand_composer``.
    - ``query`` (free text) is token-ANDed against the combined title+composer:
      every whitespace token must appear somewhere in it.

    With no constraints at all, returns a low default (``0.3``) so a bare listing
    still ranks below any real match.
    """
    scores = []
    if title:
        s = _sub(title, cand_title)
        if s is None:
            return None
        scores.append(s)
    if composer:
        s = _sub(composer, cand_composer)
        if s is None:
            return None
        scores.append(s)
    if query:
        hay = f"{cand_title} {cand_composer}".lower()
        toks = query.lower().split()
        if not all(t in hay for t in toks):
            return None
        exact = _sub(query, cand_title)
        scores.append(exact if exact is not None else 0.7)
    if not scores:
        return 0.3
    return sum(scores) / len(scores)
