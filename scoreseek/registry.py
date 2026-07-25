"""Pluggable score-source registry.

A *source* answers ``search(query, ...) -> list[ScoreRef]`` and, optionally,
``fetch(ref, ...) -> path``. Sources live in a :class:`SourceRegistry` (a
``MutableMapping`` of ``name -> source``) so the set is open for extension: a
downloadable-dataset source, an API source, or a local folder are all just
objects registered here.
"""

from __future__ import annotations

from collections.abc import MutableMapping


class SourceRegistry(MutableMapping):
    """A ``MutableMapping`` of source name -> source object."""

    def __init__(self):
        self._d: dict = {}

    def __getitem__(self, key):
        try:
            return self._d[key]
        except KeyError:
            raise KeyError(
                f"Unknown source {key!r}. Registered sources: {sorted(self._d)}"
            ) from None

    def __setitem__(self, key, value):
        self._d[key] = value

    def __delitem__(self, key):
        del self._d[key]

    def __iter__(self):
        return iter(self._d)

    def __len__(self):
        return len(self._d)


#: The process-wide registry of score sources.
sources = SourceRegistry()


def register_source(source, *, name: str = None):
    """Register a *source* object (must have a ``.name`` unless ``name`` given).

    Returns the source, so it can be used inline::

        local = register_source(LocalFolderSource("~/scores"))
    """
    key = name or getattr(source, "name", None)
    if not key:
        raise ValueError("source has no 'name'; pass name=... to register_source")
    sources[key] = source
    return source
