"""scoreseek -- find a musical score by metadata, across pluggable sources.

Search many score sources (APIs, downloadable datasets, local folders) through
one call and get back license-tagged :class:`~scoreseek.base.ScoreRef` hits you
can :meth:`~scoreseek.base.ScoreRef.fetch` on demand. Every hit carries a
:class:`~scoreseek.base.License` tag, so copyright-free / permissive results stay
cleanly separated from copyrighted-user-upload or gray results.

Simple things simple::

    import scoreseek

    hits = scoreseek.search("Cooley's")          # out-of-the-box sources
    ref = hits[0]
    path = ref.fetch()                            # -> a local score file
    # -> hand straight to audiate.render(path)

Complex things possible::

    from scoreseek import register_source, License
    from scoreseek.sources import IMSLPSource, PDMXSource

    register_source(IMSLPSource())                          # opt in to IMSLP
    register_source(PDMXSource("~/pdmx"))                   # a local PDMX corpus
    hits = scoreseek.search("nocturne", composer="Chopin", sources=["imslp"])

    # canonicalize a messy query via MusicBrainz before searching:
    hits = scoreseek.search("moonlight sonata beethovan", normalize=True)

This is the **search** stage of a larger song->score->audio->AI-enhanced
pipeline (render = ``audiate``, AI-enhance = ``arioso``).
"""

from __future__ import annotations

import warnings
from typing import List, Optional

from scoreseek.base import (
    COMMERCIAL_SAFE,
    DEFAULT_VISIBLE,
    RESTRICTED,
    License,
    ScoreRef,
)
from scoreseek.normalize import MusicBrainzNormalizer

# NOTE: exposed as ``source_registry`` (not ``sources``) on purpose -- the name
# ``scoreseek.sources`` belongs to the sources *subpackage* (the source classes),
# so binding the registry there would shadow it.
from scoreseek.registry import (
    SourceRegistry,
    register_source,
    sources as source_registry,
)

__all__ = [
    "search",
    "list_sources",
    "register_source",
    "source_registry",
    "ScoreRef",
    "License",
    "SourceRegistry",
    "MusicBrainzNormalizer",
]


def _install_default_sources() -> None:
    """Register the zero-config, out-of-the-box sources (lightweight + clean).

    Heavier or license-gated sources (IMSLP, PDMX, Chordonomicon, Kaggle) are
    importable from :mod:`scoreseek.sources` and registered by the caller.
    """
    if "thesession" not in source_registry:
        from scoreseek.sources.thesession import TheSessionSource

        register_source(TheSessionSource())
    if "openscore_lieder" not in source_registry:
        from scoreseek.sources.openscore import OpenScoreLiederSource

        register_source(OpenScoreLiederSource())


_install_default_sources()


def list_sources() -> list:
    """Return the names of the registered score sources (sorted)."""
    return sorted(source_registry)


def _normalize_query(query, title, composer, normalizer):
    """Canonicalize (query, title, composer) via MusicBrainz; return the triple
    plus an ``mbid`` (or None) to stamp on hits. Falls through on any failure."""
    try:
        norm = normalizer.normalize(query or title, artist=composer or None)
    except Exception as e:
        warnings.warn(f"normalize failed: {e}", stacklevel=2)
        return query, title, composer, None
    if norm and norm.get("title"):
        canonical_title = norm["title"]
        canonical_artist = norm.get("composer") or norm.get("artist") or composer
        return canonical_title, canonical_title, canonical_artist, norm.get("mbid")
    return query, title, composer, None


def search(
    query: str = "",
    *,
    title: str = "",
    composer: str = "",
    sources: Optional[List[str]] = None,
    allow_copyrighted: bool = False,
    normalize: bool = False,
    normalizer: Optional[MusicBrainzNormalizer] = None,
    limit: int = 10,
) -> List[ScoreRef]:
    """Search registered sources and return license-filtered, ranked hits.

    Args:
        query: Free-text query (matched against title/composer per source).
        title: Restrict to a title (source-dependent).
        composer: Restrict to a composer/artist (source-dependent).
        sources: Names of sources to query; ``None`` queries all registered.
        allow_copyrighted: If ``False`` (default), hits in the restricted lanes
            (copyrighted, scraped/gray, and **non-commercial**; see
            :data:`~scoreseek.base.RESTRICTED`) are dropped -- so results are safe
            to redistribute/build on. Set ``True`` to include them.
        normalize: If ``True`` (or a ``normalizer`` is given), canonicalize the
            query via MusicBrainz *before* fanning out (adds a ~1s network
            round-trip). The resolved MBID is stamped on each hit's metadata.
        normalizer: An explicit :class:`MusicBrainzNormalizer` (implies
            ``normalize``); ``None`` builds a default one when ``normalize=True``.
        limit: Max hits per source.

    Returns:
        A list of :class:`~scoreseek.base.ScoreRef`, best match first.
    """
    from scoreseek.registry import sources as _registry

    mbid = None
    if normalize or normalizer is not None:
        normalizer = normalizer or MusicBrainzNormalizer()
        query, title, composer, mbid = _normalize_query(
            query, title, composer, normalizer
        )

    names = sources if sources is not None else list(_registry)
    hits: List[ScoreRef] = []
    for name in names:
        source = _registry[name]  # raises KeyError with a helpful message
        try:
            found = source.search(query, title=title, composer=composer, limit=limit)
        except Exception as e:  # a flaky source shouldn't sink the whole search
            warnings.warn(f"source {name!r} failed: {e}", stacklevel=2)
            continue
        hits.extend(found)

    if not allow_copyrighted:
        # Allowlist (fail-safe): only the commercial-safe lanes + UNKNOWN pass;
        # any restricted or future/unclassified license is hidden by default.
        hits = [h for h in hits if h.license in DEFAULT_VISIBLE]

    if mbid:
        for h in hits:
            h.metadata.setdefault("mbid", mbid)

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
