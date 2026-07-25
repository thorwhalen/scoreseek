"""The ``Source`` base class that all score sources implement.

A source turns a query into :class:`~scoreseek.base.ScoreRef` hits and can
optionally fetch a hit's content. Subclass and implement :meth:`search` (and
:meth:`fetch` if the source hands back real files).
"""

from __future__ import annotations

from typing import List, Optional

from scoreseek.base import License, ScoreRef


class Source:
    """Base class for a score source.

    Attributes:
        name: Unique registry name.
        license: Default :class:`~scoreseek.base.License` for this source's hits
            (individual hits may override).
    """

    name: str = "source"
    license: License = License.UNKNOWN

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

    def _ref(self, **kwargs) -> ScoreRef:
        """Build a :class:`ScoreRef` for this source (fills in source/license)."""
        kwargs.setdefault("source", self.name)
        kwargs.setdefault("license", self.license)
        ref = ScoreRef(**kwargs)
        ref._source_obj = self
        return ref
