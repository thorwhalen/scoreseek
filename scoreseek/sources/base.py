"""The ``Source`` base class that all score sources implement.

A source turns a query into :class:`~scoreseek.base.ScoreRef` hits and can
optionally fetch a hit's content. Subclass and implement :meth:`search` (and
:meth:`fetch` if the source hands back real files).
"""

from __future__ import annotations

from typing import List, Optional

from scoreseek.base import UNSET, License, ScoreFile, ScoreRef


class Source:
    """Base class for a score source.

    Attributes:
        name: Unique registry name.
        license: Default :class:`~scoreseek.base.License` for this source's hits
            (individual hits may override).
        uniform_license: Whether every hit has exactly ``license``.
        max_bytes: Default fetch size cap in bytes (``None``: no cap). Sources
            that download set :data:`~scoreseek.base.DEFAULT_MAX_BYTES`.
    """

    name: str = "source"
    license: License = License.UNKNOWN
    #: True when every hit carries ``license`` (no per-hit override), so a
    #: search that would hide that license can skip the source entirely.
    uniform_license: bool = False
    #: Default ``max_bytes`` for :meth:`ScoreRef.fetch` (``None``: no cap).
    max_bytes: Optional[int] = None

    def search(
        self,
        query: str = "",
        *,
        title: str = "",
        composer: str = "",
        limit: int = 10,
    ) -> List[ScoreRef]:
        """Return up to ``limit`` :class:`ScoreRef` hits for the query.

        Subclasses must implement this.
        """
        raise NotImplementedError

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Retrieve ``ref``'s content and return a filesystem path.

        Subclasses that can deliver content override this; the default raises.
        """
        raise NotImplementedError(
            f"Source {self.name!r} is metadata-only and cannot fetch content."
        )

    def files(self, ref: ScoreRef) -> List[ScoreFile]:
        """List the downloadable files behind ``ref`` (kinds, sizes, licenses).

        Sources whose hits bundle several files override this; the default raises.
        """
        raise NotImplementedError(
            f"Source {self.name!r} does not list the files behind a hit."
        )

    def _cap(self, max_bytes) -> Optional[int]:
        """Resolve a ``max_bytes`` argument (:data:`~scoreseek.base.UNSET` -> default)."""
        return self.max_bytes if max_bytes is UNSET else max_bytes

    def _ref(self, **kwargs) -> ScoreRef:
        """Build a :class:`ScoreRef` for this source (fills in source/license)."""
        kwargs.setdefault("source", self.name)
        kwargs.setdefault("license", self.license)
        ref = ScoreRef(**kwargs)
        ref._source_obj = self
        return ref
