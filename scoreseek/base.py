"""Core types for scoreseek: :class:`ScoreRef`, :class:`License`, and the
license-partitioning constants.

A search returns :class:`ScoreRef` hits -- lightweight, license-tagged pointers
to a score that can be :meth:`ScoreRef.fetch`-ed on demand. The **license tag on
every hit** is the load-bearing design choice: it lets the facade keep
public-domain / permissive results cleanly separated from copyrighted-user-upload
or gray-licensed results (see :data:`COMMERCIAL_SAFE` / :data:`RESTRICTED`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class License(str, Enum):
    """A coarse license classification for a score, driving safe filtering."""

    PUBLIC_DOMAIN = "public_domain"
    CC0 = "cc0"
    PERMISSIVE = "permissive"  # CC-BY, MIT, BSD, Apache, CC-BY-SA, ...
    NONCOMMERCIAL = "noncommercial"  # CC-BY-NC(-*)
    COPYRIGHTED = "copyrighted"  # all rights reserved (e.g. user uploads)
    GRAY = "gray"  # scraped / unspecified terms
    UNKNOWN = "unknown"


#: License tags safe to build products on (redistribution + commercial, w/ attribution).
COMMERCIAL_SAFE = frozenset({License.PUBLIC_DOMAIN, License.CC0, License.PERMISSIVE})

#: License tags gated behind ``allow_copyrighted=True``: copyrighted, scraped/gray,
#: AND non-commercial (CC-BY-NC). Non-commercial is included because the default
#: "safe" search is about what you can freely *build products on*, and NC content
#: carries a real usage restriction -- so it belongs in the opt-in lane alongside
#: copyrighted/gray, not in the default results.
RESTRICTED = frozenset({License.COPYRIGHTED, License.GRAY, License.NONCOMMERCIAL})

#: Shown by default (``allow_copyrighted=False``): the commercial-safe lanes plus
#: ``UNKNOWN`` -- unclassified provenance (e.g. your own local folders) is *your*
#: content, so it is not hidden, whereas a *known* restriction (NC/copyright/gray)
#: is respected by the safe default.
DEFAULT_VISIBLE = COMMERCIAL_SAFE | frozenset({License.UNKNOWN})


@dataclass
class ScoreRef:
    """A license-tagged pointer to a score returned by a search.

    Attributes:
        title: Score/song title.
        source: Name of the source that produced this hit.
        id: Source-specific identifier.
        composer: Composer or artist.
        formats: Available formats, e.g. ``("musicxml", "midi")``.
        license: A :class:`License` classification.
        url: A human-facing URL for the score, if any.
        score: Match relevance in ``[0, 1]``.
        metadata: Source-specific extra fields.
    """

    title: str
    source: str
    id: str = ""
    composer: str = ""
    formats: tuple = ()
    license: License = License.UNKNOWN
    url: str = ""
    score: float = 0.0
    metadata: dict = field(default_factory=dict)
    _source_obj: Any = field(default=None, repr=False, compare=False)

    @property
    def is_commercial_safe(self) -> bool:
        """True if this score's license permits redistribution/commercial use."""
        return self.license in COMMERCIAL_SAFE

    def fetch(self, *, fmt: Optional[str] = None):
        """Retrieve the score content (returns a filesystem path).

        Delegates to the originating source. The returned path can be handed
        straight to ``audiate.render(...)``.

        Raises:
            NotImplementedError: If the originating source can't fetch (e.g. a
                metadata-only source, or a bare ``ScoreRef`` with no source).
        """
        if self._source_obj is None or not hasattr(self._source_obj, "fetch"):
            raise NotImplementedError(
                f"ScoreRef {self.title!r} from source {self.source!r} cannot be "
                "fetched (no fetch-capable source attached)."
            )
        return self._source_obj.fetch(self, fmt=fmt)
