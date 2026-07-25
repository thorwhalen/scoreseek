"""scoreseek -- find a musical score by metadata, across pluggable sources.

Search many score sources (APIs, downloadable datasets, local folders) through
one call and get back license-tagged :class:`~scoreseek.base.ScoreRef` hits you
can :meth:`~scoreseek.base.ScoreRef.fetch` on demand. Every hit carries a
:class:`~scoreseek.base.License` tag, so copyright-free / permissive results stay
cleanly separated from copyrighted-user-upload or gray results.

Simple things simple::

    import scoreseek

    hits = scoreseek.search("Cooley's")          # out-of-the-box: The Session
    ref = hits[0]
    path = ref.fetch()                            # -> a local .abc file
    # -> hand straight to audiate.render(path)

Complex things possible::

    from scoreseek import register_source, License
    from scoreseek.sources import LocalFolderSource

    register_source(LocalFolderSource("~/my_scores", license=License.PUBLIC_DOMAIN))
    hits = scoreseek.search("prelude", sources=["local"], allow_copyrighted=False)

This is the **search** stage of a larger song->score->audio->AI-enhanced
pipeline (render = ``audiate``, AI-enhance = ``arioso``).
"""

from __future__ import annotations

from typing import List, Optional

from scoreseek.base import (
    COMMERCIAL_SAFE,
    RESTRICTED,
    License,
    ScoreRef,
)

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
]


def _install_default_sources() -> None:
    """Register the zero-config out-of-the-box source(s)."""
    if "thesession" not in source_registry:
        from scoreseek.sources.thesession import TheSessionSource

        register_source(TheSessionSource())


_install_default_sources()


def list_sources() -> list:
    """Return the names of the registered score sources (sorted)."""
    return sorted(source_registry)


def search(
    query: str = "",
    *,
    title: str = "",
    composer: str = "",
    sources: Optional[List[str]] = None,
    allow_copyrighted: bool = False,
    limit: int = 10,
) -> List[ScoreRef]:
    """Search registered sources and return license-filtered, ranked hits.

    Args:
        query: Free-text query (matched against title/composer per source).
        title: Restrict to a title (source-dependent).
        composer: Restrict to a composer/artist (source-dependent).
        sources: Names of sources to query; ``None`` queries all registered.
        allow_copyrighted: If ``False`` (default), copyrighted/gray-licensed hits
            (see :data:`~scoreseek.base.RESTRICTED`) are dropped -- so results are
            safe to redistribute/build on. Set ``True`` to include them.
        limit: Max hits per source.

    Returns:
        A list of :class:`~scoreseek.base.ScoreRef`, best match first.
    """
    from scoreseek.registry import sources as _registry

    names = sources if sources is not None else list(_registry)
    hits: List[ScoreRef] = []
    for name in names:
        source = _registry[name]  # raises KeyError with a helpful message
        try:
            found = source.search(query, title=title, composer=composer, limit=limit)
        except Exception as e:  # a flaky source shouldn't sink the whole search
            import warnings

            warnings.warn(f"source {name!r} failed: {e}", stacklevel=2)
            continue
        hits.extend(found)

    if not allow_copyrighted:
        hits = [h for h in hits if h.license not in RESTRICTED]

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits
