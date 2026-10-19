"""IMSLP / Petrucci source: the public-domain classical giant, via its API.

IMSLP is exposed through a legacy MediaWiki API at ``imslp.org/api.php``.
:meth:`~IMSLPSource.search` uses ``action=query&list=search`` (page titles have
the form ``"<Work> (<Surname>, <First>)"``). :meth:`~IMSLPSource.files` lists a
work's files from (1) the page **wikitext** (score-vs-audio split, section, the
per-file ``|Copyright=`` tag) and (2) the **rendered HTML** (numeric IMSLP file
index and displayed size). :meth:`~IMSLPSource.fetch` filters that list by
``fmt`` and a size cap (``max_bytes``, 50 MB by default -- full-score scans can
run to hundreds of MB), lets a ``pick`` strategy choose (default
:func:`prefer_renderable`), and (3) downloads from an IMSLP **mirror** host,
returning the native file (mostly PDF, some MIDI; MusicXML is rare).

Two things make this work reliably and are easy to get wrong (both handled here):

* Direct ``imslp.org/images/...`` downloads now 302 to an MTCaptcha bot-wall.
  The mirror path (``vmirror.imslp.org/files/imglnks/usimg/...``) serves the
  identical bytes with no CAPTCHA.
* IMSLP's MediaWiki ignores ``formatversion=2``: ``query.pages`` is a dict keyed
  by pageid and ``parse.text`` is ``{"*": html}``. Parsed legacy-style here.

Stdlib-only (``urllib`` + ``hashlib`` + ``re``). Formats are honestly
PDF-dominant: search fills each hit's ``formats`` from its page's file list (one
batched request), so filter to ``"midi"``/``"musicxml"`` when a hit must reach
audio.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
import warnings
from typing import Callable, List, Optional

from scoreseek import _http
from scoreseek.base import License, ScoreFile, ScoreRef, ScoreTooLargeError
from scoreseek.sources.base import Source

_API = "https://imslp.org/api.php"
_MIRRORS = ("vmirror.imslp.org", "ks15.imslp.org", "s9.imslp.org")

#: Default size cap for :meth:`IMSLPSource.fetch` (a full orchestral score
#: scan can run to hundreds of MB; 50 MB keeps a casual fetch casual).
DEFAULT_MAX_BYTES = 50 * _http.MB
#: MediaWiki's per-request cap on ``titles=`` for anonymous clients.
_TITLES_PER_QUERY = 50
#: Sentinel: "use the source's configured default" (``None`` means "no cap").
_UNSET = object()

#: A hit's page title looks like "Work Title (Surname, First)".
_TITLE_RE = re.compile(r"^(.*) \(([^,]+), (.+)\)$")
#: Native score extension -> format label (PDF-dominant; MIDI/MusicXML rarer).
_KINDS = {
    ".pdf": "pdf",
    ".mid": "midi",
    ".midi": "midi",
    ".mxl": "musicxml",
    ".xml": "musicxml",
    ".zip": "zip",
}
_SCORE_EXTS = tuple(_KINDS)
_RENDERABLE_KINDS = ("midi", "musicxml")
_KIND_ORDER = ("midi", "musicxml", "pdf", "zip")

#: Canonical fmt label -> acceptable file extensions (``"midi"`` accepts ``.mid``).
_FMT_EXTS = {
    "midi": (".mid", ".midi"),
    "mid": (".mid", ".midi"),
    "musicxml": (".mxl", ".xml"),
    "mxl": (".mxl", ".xml"),
    "xml": (".mxl", ".xml"),
    "pdf": (".pdf",),
    "zip": (".zip",),
}

#: In the rendered page: ``<a href="/wiki/File:NAME" ...>#INDEX</a> - 9.29MB``
#: (the tail is captured in a lookahead so adjacent links are not swallowed).
_FILE_LINK_RE = re.compile(
    r'href="/wiki/File:([^"]+)"[^>]*>#(\d+)</a>(?=(.{0,1000}))', re.DOTALL
)
_SIZE_RE = re.compile(r"\s*-\s*([\d.]+)\s*([KMG]?B)\b", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30}
#: A wikitext heading, e.g. ``====Parts====`` -> ("====", "Parts").
_HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def _copyright_to_license(value: str) -> License:
    """Map an IMSLP ``|Copyright=`` field to a :class:`License`."""
    v = (value or "").lower()
    if "public domain" in v:
        return License.PUBLIC_DOMAIN
    if "noncommercial" in v or "non-commercial" in v:
        return License.NONCOMMERCIAL
    if "creative commons" in v:  # CC-BY / CC-BY-SA (non-NC)
        return License.PERMISSIVE
    return License.GRAY


def _ext_of(filename: str) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename)
    return m.group(1).lower() if m else ""


class IMSLPSource(Source):
    """Search IMSLP works and fetch public-domain score files (mostly PDF)."""

    name = "imslp"
    license = License.PUBLIC_DOMAIN

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        mirrors=_MIRRORS,
        max_bytes: Optional[int] = DEFAULT_MAX_BYTES,
        pick: Callable[[List[ScoreFile]], ScoreFile] = None,
        resolve_formats: bool = True,
    ):
        """
        Args:
            timeout: Per-request timeout (seconds).
            mirrors: Download hosts; the first is used.
            max_bytes: Default size cap for :meth:`fetch` (``None``: no cap).
            pick: Default file-choice strategy for :meth:`fetch`
                (``None``: :func:`prefer_renderable`).
            resolve_formats: Fill each search hit's ``formats`` from its page's
                file list (one extra batched request per search).
        """
        self.timeout = timeout
        self.mirrors = tuple(mirrors)
        self.max_bytes = max_bytes
        self.pick = pick or prefer_renderable
        self.resolve_formats = resolve_formats

    # -- networking (thin wrappers so tests can monkeypatch at the instance) --
    def _get_json(self, url: str) -> dict:
        return _http.get_json(url, timeout=self.timeout)

    def _get_text(self, url: str) -> str:
        return _http.get_text(url, timeout=self.timeout)

    def _get_bytes(self, url: str, *, max_bytes: Optional[int] = None) -> bytes:
        return _http.get_bytes(url, timeout=self.timeout, max_bytes=max_bytes)

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Full-text search IMSLP works. Composer is filtered client-side.

        IMSLP's legacy search is strict-AND over tokenized page text (punctuation
        is part of a token), so keep to 1-2 high-signal terms and use *formal*
        titles. ``composer`` is matched against the ``"(Surname, First)"`` suffix.
        """
        q = " ".join(t for t in (query, title, composer) if t).strip()
        if not q:
            return []
        url = f"{_API}?" + urllib.parse.urlencode(
            {
                "action": "query",
                "list": "search",
                "srsearch": q,
                "srnamespace": 0,
                "srlimit": max(limit * 2, limit),  # room to drop redirects
                "format": "json",
            }
        )
        data = self._get_json(url)
        results = (data.get("query") or {}).get("search") or []
        hits: List[ScoreRef] = []
        for i, item in enumerate(results):
            page = item.get("title", "")
            snippet = item.get("snippet", "")
            if snippet.startswith("#REDIRECT") or page.startswith("#REDIRECT"):
                continue
            m = _TITLE_RE.match(page)
            if m:
                work_title, surname, first = m.group(1), m.group(2), m.group(3)
                comp = f"{first} {surname}".strip()
            else:
                work_title, comp = page, ""
            if composer and composer.lower() not in comp.lower():
                continue
            hits.append(
                self._ref(
                    title=work_title,
                    id=page,  # exact page title; used verbatim by fetch()
                    composer=comp,
                    formats=(),  # filled below (resolve_formats) or by files()
                    url="https://imslp.org/wiki/"
                    + urllib.parse.quote(page.replace(" ", "_")),
                    score=round(1.0 - i / max(len(results), 1), 3),
                    metadata={"page": page},
                )
            )
            if len(hits) >= limit:
                break
        if self.resolve_formats and hits:
            self._fill_formats(hits)
        return hits

    def _fill_formats(self, hits: List[ScoreRef]) -> None:
        """Set each hit's ``formats`` from its page's file list (best effort)."""
        try:
            texts = self._wikitexts([h.id for h in hits])
        except Exception as e:  # formats are a convenience; keep the hits
            warnings.warn(f"IMSLP: could not resolve formats: {e}", stacklevel=3)
            return
        for h in hits:
            if h.id in texts:
                h.formats = _formats_of(f["kind"] for f in _parse_files(texts[h.id]))

    # -- files: wikitext (what/where/license) + rendered HTML (index, size) --
    def _wikitexts(self, pages: List[str]) -> dict:
        """{page title -> wikitext}, batched (one request per 50 titles)."""
        out = {}
        for i in range(0, len(pages), _TITLES_PER_QUERY):
            batch = pages[i : i + _TITLES_PER_QUERY]
            url = f"{_API}?" + urllib.parse.urlencode(
                {
                    "action": "query",
                    "prop": "revisions",
                    "rvprop": "content",
                    "rvslots": "main",
                    "titles": "|".join(batch),
                    "format": "json",
                }
            )
            data = self._get_json(url)
            for pg in ((data.get("query") or {}).get("pages") or {}).values():
                revs = pg.get("revisions") or []
                if revs:
                    rev = revs[0]
                    main = rev.get("slots", {}).get("main", {}) or {}
                    out[pg.get("title", "")] = main.get("*") or rev.get("*", "")
        if len(pages) == 1 and len(out) == 1:
            # a single requested page maps unambiguously (titles may be normalized)
            return {pages[0]: next(iter(out.values()))}
        return out

    def _manifest(self, page: str) -> List[dict]:
        """Score files for *page*, in page order (see :func:`_parse_files`)."""
        return _parse_files(self._wikitexts([page]).get(page, ""))

    def _file_info(self, page: str) -> dict:
        """{filename -> (numeric IMSLP file index, size in bytes or None)}."""
        url = f"{_API}?" + urllib.parse.urlencode(
            {"action": "parse", "page": page, "prop": "text", "format": "json"}
        )
        data = self._get_json(url)
        html = ((data.get("parse") or {}).get("text") or {}).get("*") or ""
        out = {}
        for fname, idx, tail in _FILE_LINK_RE.findall(html):
            out[urllib.parse.unquote(fname)] = (idx, _parse_size(tail))
        return out

    def _mirror_url(self, filename: str, index: str) -> str:
        n = filename.replace(" ", "_")
        n = n[:1].upper() + n[1:]
        h = hashlib.md5(n.encode("utf-8")).hexdigest()
        seg = urllib.parse.quote(n)
        return (
            f"https://{self.mirrors[0]}/files/imglnks/usimg/"
            f"{h[0]}/{h[:2]}/IMSLP{index}-{seg}"
        )

    def files(self, ref: ScoreRef) -> List[ScoreFile]:
        """List *ref*'s score files with kind, size (bytes) and per-file license.

        Two requests (wikitext + rendered page). Also fills ``ref.formats``.
        Sizes come from IMSLP's page and are ``None`` where it shows none.
        """
        page = ref.metadata.get("page") or ref.id
        manifest = self._manifest(page)
        info = self._file_info(page) if manifest else {}
        files = []
        for f in manifest:
            index, size = info.get(f["filename"], (None, None))
            files.append(
                ScoreFile(
                    filename=f["filename"],
                    kind=f["kind"],
                    size=size,
                    license=f["license"],
                    description=f["description"],
                    section=f["section"],
                    metadata={
                        "index": index,
                        "ext": f["ext"],
                        "copyright": f["copyright"],
                    },
                )
            )
        ref.formats = _formats_of(f.kind for f in files)
        return files

    # -- fetch: list files -> filter (fmt, size cap) -> pick -> mirror download --
    def fetch(
        self,
        ref: ScoreRef,
        *,
        fmt: Optional[str] = None,
        max_bytes=_UNSET,
        pick=_UNSET,
    ) -> str:
        """Download one of *ref*'s files and return its cached path.

        Args:
            ref: An IMSLP hit.
            fmt: Restrict to a format (``"midi"`` accepts ``.mid``, ``"musicxml"``
                accepts ``.mxl``/``.xml``, also ``"pdf"``, ``"zip"``).
            max_bytes: Size cap in bytes (default: the source's ``max_bytes``,
                50 MB unless configured). Files IMSLP lists as larger are skipped,
                and the download stops if it passes the cap. ``None``: no cap.
            pick: Which file to take. A ``filename`` from :meth:`files`, or a
                strategy ``(List[ScoreFile]) -> ScoreFile`` choosing among the
                files that match ``fmt`` and fit the cap (default: the source's
                ``pick``, :func:`prefer_renderable`; see also :func:`smallest`).

        Updates ``ref.license`` to the chosen file's own license (search hits
        carry a provisional ``PUBLIC_DOMAIN`` tag, since IMSLP is PD-dominant).

        Raises:
            RuntimeError: No score file, no file matching ``fmt``, or an unknown
                ``pick`` filename.
            ScoreTooLargeError: Every candidate exceeds ``max_bytes`` (the message
                names the smallest ones), or the chosen file does.
        """
        max_bytes = self.max_bytes if max_bytes is _UNSET else max_bytes
        pick = self.pick if pick is _UNSET else pick
        page = ref.metadata.get("page") or ref.id
        files = self.files(ref)
        if not files:
            raise RuntimeError(f"IMSLP: no score files found for {page!r}")

        if isinstance(pick, str):
            chosen = next((f for f in files if f.filename == pick), None)
            if chosen is None:
                raise RuntimeError(
                    f"IMSLP: {page!r} has no file named {pick!r} "
                    f"(see ref.files(); available: {[f.filename for f in files]})"
                )
            if _over(chosen, max_bytes):
                raise ScoreTooLargeError(
                    f"IMSLP: {chosen.filename!r} is {_mb(chosen.size)}, over "
                    f"max_bytes={max_bytes} ({_mb(max_bytes)}). "
                    "Pass a larger max_bytes, or max_bytes=None for no cap."
                )
        else:
            chosen = _choose(page, files, fmt=fmt, max_bytes=max_bytes, pick=pick)

        # Refine the hit's license to the actual file's copyright tag.
        ref.license = chosen.license

        index = chosen.metadata.get("index")
        if index is None:
            raise RuntimeError(
                f"IMSLP: could not resolve a file index for "
                f"{chosen.filename!r} on {page!r}"
            )
        url = self._mirror_url(chosen.filename, index)
        data = self._get_bytes(url, max_bytes=max_bytes)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", chosen.filename)
        out = _http.cache_dir("imslp") / f"IMSLP{index}-{safe}"
        out.write_bytes(data)
        return str(out)


# -- file-choice strategies: (List[ScoreFile]) -> ScoreFile --


def prefer_renderable(files: List[ScoreFile]) -> ScoreFile:
    """Default ``pick``: a renderable file (MIDI/MusicXML) over PDF/zip, a full
    score over a single part, a known size over an unknown one (only a known size
    can be checked against the cap before downloading), then IMSLP's page order
    (main editions first)."""

    def rank(item):
        i, f = item
        part = "part" in f"{f.section} {f.description}".lower()
        return (f.kind not in _RENDERABLE_KINDS, part, f.size is None, i)

    return min(enumerate(files), key=rank)[1]


def smallest(files: List[ScoreFile]) -> ScoreFile:
    """A ``pick`` strategy: the smallest file (unknown sizes last)."""
    return min(files, key=lambda f: (f.size is None, f.size or 0))


def _choose(page, files, *, fmt, max_bytes, pick) -> ScoreFile:
    """Filter *files* by ``fmt`` and the size cap, then apply the ``pick`` strategy."""
    if fmt:
        label = fmt.lower().lstrip(".")
        want_exts = _FMT_EXTS.get(label, ("." + label,))
        matching = [f for f in files if f.metadata["ext"] in want_exts]
        if not matching:
            raise RuntimeError(
                f"IMSLP: {page!r} has no {fmt} file "
                f"(available: {sorted({f.kind for f in files})})"
            )
    else:
        matching = files
    fitting = [f for f in matching if not _over(f, max_bytes)]
    if not fitting:
        by_size = sorted(matching, key=lambda f: f.size)
        listed = ", ".join(f"{f.filename} ({_mb(f.size)})" for f in by_size[:5])
        raise ScoreTooLargeError(
            f"IMSLP: all {len(matching)} {fmt or 'score'} file(s) on {page!r} "
            f"exceed max_bytes={max_bytes} ({_mb(max_bytes)}). Smallest: {listed}. "
            "Pass a larger max_bytes (None for no cap), or pick=<filename> "
            "from ref.files()."
        )
    chosen = pick(fitting)
    if chosen is None:
        raise RuntimeError(f"IMSLP: the pick strategy chose no file on {page!r}")
    return chosen


def _over(f: ScoreFile, max_bytes: Optional[int]) -> bool:
    return max_bytes is not None and f.size is not None and f.size > max_bytes


def _mb(n: Optional[int]) -> str:
    return "unknown size" if n is None else f"{n / _http.MB:.1f} MB"


def _parse_size(tail: str) -> Optional[int]:
    """Bytes from the text after a file link, e.g. ``" - 9.29MB - 7:28"`` (or the
    same with the number or a preview badge wrapped in tags). The tail is cut at
    the next file link so a size-less file never borrows its neighbour's."""
    tail = tail.split('href="/wiki/File:', 1)[0]
    m = _SIZE_RE.search(re.sub(r"<[^>]+>", "", tail))
    if not m:
        return None
    return int(float(m.group(1)) * _UNITS[m.group(2).upper()])


def _formats_of(kinds) -> tuple:
    """Distinct kinds in a stable, renderable-first order."""
    present = set(kinds)
    return tuple(k for k in _KIND_ORDER if k in present)


def _parse_files(wikitext: str) -> List[dict]:
    """Score files from a work page's wikitext, in page order:
    [{filename, ext, kind, copyright, license, description, section}]."""
    if "*****FILES*****" not in wikitext:
        return []
    files_section = wikitext.split("*****FILES*****", 1)[1]
    files, headings = [], {}
    for chunk in re.split(r"(?=\{\{#fte:imslpfile)", files_section):
        if chunk.startswith("{{#fte:imslpfile"):
            section = " / ".join(headings[k] for k in sorted(headings))
            cr = _field(chunk, "Copyright")
            n = 1
            while fname := _field(chunk, f"File Name {n}"):
                ext = _ext_of(fname)
                if ext in _SCORE_EXTS:
                    files.append(
                        {
                            "filename": fname,
                            "ext": ext,
                            "kind": _KINDS[ext],
                            "copyright": cr,
                            "license": _copyright_to_license(cr),
                            "description": _field(chunk, f"File Description {n}"),
                            "section": section,
                        }
                    )
                n += 1
        # headings after this block (e.g. "====Parts====") apply to the next files;
        # a heading replaces its own level and clears the deeper ones.
        for marks, text in _HEADING_RE.findall(chunk):
            level = len(marks)
            headings = {k: v for k, v in headings.items() if k < level}
            headings[level] = text
    return files


def _field(chunk: str, name: str) -> str:
    """Extract a single ``|<name>= value`` field from an ``#fte:imslpfile`` block."""
    m = re.search(rf"\|\s*{re.escape(name)}\s*=\s*([^\n|]*)", chunk)
    return m.group(1).strip() if m else ""
