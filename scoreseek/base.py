"""Core types for scoreseek: :class:`ScoreRef`, :class:`License`, and the
license-partitioning constants.

A search returns :class:`ScoreRef` hits -- lightweight, license-tagged pointers
to a score that can be :meth:`ScoreRef.fetch`-ed on demand. A hit that bundles
several files (an IMSLP work page lists scores, parts, arrangements) can be
inspected first with :meth:`ScoreRef.files`, which returns :class:`ScoreFile`
entries with their kind and size. The **license tag on
every hit** is the load-bearing design choice: it lets the facade keep
public-domain / permissive results cleanly separated from copyrighted-user-upload
or gray-licensed results (see :data:`COMMERCIAL_SAFE` / :data:`RESTRICTED`).
"""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


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


#: Default ``max_bytes`` for a source that downloads (a full orchestral score
#: scan can run to hundreds of MB; 50 MB keeps a casual fetch casual). Local
#: corpora default to no cap: nothing is downloaded.
DEFAULT_MAX_BYTES = 50 * 2**20


class _Unset:
    """Type of :data:`UNSET`."""

    def __repr__(self):
        return "UNSET"


#: Sentinel for ``max_bytes``: "use the source's default" (``None`` means no cap).
UNSET = _Unset()


class ScoreTooLargeError(RuntimeError):
    """Raised when every candidate file (or the download itself) exceeds a size cap."""


@dataclass(frozen=True)
class ScoreFile:
    """One downloadable file behind a :class:`ScoreRef` (see :meth:`ScoreRef.files`).

    Attributes:
        filename: The source's file name; pass it as ``pick=`` to fetch this file.
        kind: Format label, e.g. ``"pdf"``, ``"midi"``, ``"musicxml"``, ``"zip"``.
        size: Size in bytes, or ``None`` when the source does not report it.
        license: This file's own :class:`License` (may differ from the hit's).
        description: Human description, e.g. ``"Complete Score"``.
        section: Where the file sits on its page, e.g. ``"Parts"``.
        metadata: Source-specific extra fields.
    """

    filename: str
    kind: str
    size: Optional[int] = None
    license: License = License.UNKNOWN
    description: str = ""
    section: str = ""
    metadata: dict = field(default_factory=dict, compare=False, hash=False)


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

    def fetch(
        self,
        *,
        fmt: Optional[str] = None,
        max_bytes=UNSET,
        pick=None,
        **options,
    ):
        """Retrieve the score content (returns a filesystem path).

        Delegates to the originating source. The returned path can be handed
        straight to ``audiate.render(...)``. ``max_bytes`` and ``pick`` work for
        every source, so a loop over mixed search results needs no branching.

        Args:
            fmt: Requested format (source-dependent, e.g. ``"midi"``).
            max_bytes: Size cap in bytes. Default: the source's ``max_bytes``
                (:data:`DEFAULT_MAX_BYTES` for sources that download, no cap for
                local corpora). ``None``: no cap. A source that downloads applies
                it while downloading; for the others the fetched file is checked.
            pick: Which file to take when a hit has several (see :meth:`files`):
                a ``filename``, or a strategy ``(List[ScoreFile]) -> ScoreFile``
                (see :mod:`scoreseek.picks`). A source without :meth:`files` has
                one file per hit: a strategy has nothing to choose there, and a
                filename is an error.
            **options: Passed through to the source's ``fetch``.

        Raises:
            NotImplementedError: If the originating source can't fetch (e.g. a
                metadata-only source, or a bare ``ScoreRef`` with no source).
            ScoreTooLargeError: The file exceeds ``max_bytes``.
            TypeError: The source's ``fetch`` does not take one of ``options``
                (the message names the ones it does take), or ``pick`` is a
                filename for a source that lists no files.
        """
        source = self._require_source("fetch", "fetched")
        _validate_max_bytes(max_bytes)
        if isinstance(pick, ScoreFile):  # an entry from ref.files()
            pick = pick.filename
        if options:
            _check_options(source.fetch, options, source=self.source)
        kwargs = dict(options)
        # "native" only when fetch *names* the option: a **kwargs catch-all may
        # silently ignore it, so the generic size check must still run then
        native_cap = _names(source.fetch, "max_bytes")
        if native_cap and max_bytes is not UNSET:
            kwargs["max_bytes"] = max_bytes
        if pick is not None:
            if _names(source.fetch, "pick"):
                kwargs["pick"] = pick
            elif isinstance(pick, str):
                raise TypeError(
                    f"source {self.source!r} has one file per hit and lists no "
                    f"files, so pick={pick!r} (a filename) cannot apply"
                )
        path = source.fetch(self, fmt=fmt, **kwargs)
        if not native_cap:
            cap = (
                getattr(source, "max_bytes", None) if max_bytes is UNSET else max_bytes
            )
            _check_file_size(path, cap, source=self.source)
        return path

    def files(self) -> List[ScoreFile]:
        """List the downloadable files behind this hit, with kinds and sizes.

        Use it to see what :meth:`fetch` would choose from, then pass a
        ``filename`` as ``fetch(pick=...)``.

        Raises:
            NotImplementedError: If the originating source can't list files.
        """
        return self._require_source("files", "listed").files(self)

    def _require_source(self, method: str, verb: str):
        if self._source_obj is None or not hasattr(self._source_obj, method):
            raise NotImplementedError(
                f"ScoreRef {self.title!r} from source {self.source!r} cannot be "
                f"{verb} (no {method}-capable source attached)."
            )
        return self._source_obj


def _check_options(func, options: dict, *, source: str) -> None:
    """Raise a clear ``TypeError`` if *func* cannot take every key of *options*."""
    params = inspect.signature(func).parameters
    if any(p.kind is p.VAR_KEYWORD for p in params.values()):
        return
    unsupported = sorted(set(options) - set(params))
    if unsupported:
        takes = sorted(n for n, p in params.items() if p.kind is p.KEYWORD_ONLY)
        raise TypeError(
            f"source {source!r} does not support fetch option(s) {unsupported} "
            f"(its fetch takes: {takes})"
        )


def _names(func, name: str) -> bool:
    """True if *func* has a parameter literally named ``name`` (not via ``**kw``)."""
    try:
        return name in inspect.signature(func).parameters
    except (TypeError, ValueError):  # no inspectable signature: assume not
        return False


def _validate_max_bytes(max_bytes) -> None:
    if max_bytes is UNSET or max_bytes is None:
        return
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
        raise TypeError(f"max_bytes must be an int (bytes) or None, not {max_bytes!r}")
    if max_bytes < 0:
        raise ValueError(f"max_bytes must be >= 0, not {max_bytes}")


def _check_file_size(path, max_bytes: Optional[int], *, source: str) -> None:
    """Raise :class:`ScoreTooLargeError` if the file at *path* exceeds ``max_bytes``."""
    if max_bytes is None or not isinstance(path, (str, os.PathLike)):
        return
    if not os.path.isfile(path):
        return
    size = os.path.getsize(path)
    if size > max_bytes:
        raise ScoreTooLargeError(
            f"source {source!r}: {path} is {size} bytes, over max_bytes={max_bytes}. "
            "Pass a larger max_bytes, or max_bytes=None for no cap."
        )
