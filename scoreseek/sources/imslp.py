"""IMSLP / Petrucci source: the public-domain classical giant, via its API.

IMSLP is exposed through a legacy MediaWiki API at ``imslp.org/api.php``.
:meth:`~IMSLPSource.search` uses ``action=query&list=search`` (page titles have
the form ``"<Work> (<Surname>, <First>)"``). :meth:`~IMSLPSource.files` lists a
work's files from (1) the page **wikitext** (score-vs-audio split, section, the
per-file ``|Copyright=`` tag) and (2) the **rendered HTML** (numeric IMSLP file
index and displayed size). :meth:`~IMSLPSource.fetch` filters that list by
``fmt`` and a size cap (``max_bytes``, 50 MB by default -- full-score scans can
run to hundreds of MB), lets a ``pick`` strategy choose (default
:func:`~scoreseek.picks.prefer_safe_renderable`), and (3) downloads from an
IMSLP **mirror** host, returning the native file (mostly PDF, some MIDI;
MusicXML is rare). MIDI renditions sit with the recordings (``imslpaudio``
blocks), so both sections are read.

Two things make this work reliably and are easy to get wrong (both handled here):

* Direct ``imslp.org/images/...`` downloads now 302 to an MTCaptcha bot-wall.
  The mirror path (``vmirror.imslp.org/files/imglnks/usimg/...``) serves the
  identical bytes with no CAPTCHA.
* Many search hits are **redirects** (e.g. "Greensleeves (Vaughan Williams)" ->
  "Sir John in Love"), and IMSLP gives them an empty snippet. Requests pass
  ``redirects=1``; a redirect hit is pointed at its target page and duplicates
  of one target are dropped.
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
from typing import List, Optional

from scoreseek import _http, picks
from scoreseek.base import (
    DEFAULT_MAX_BYTES,
    UNSET,
    License,
    ScoreFile,
    ScoreRef,
    ScoreTooLargeError,
)
from scoreseek.sources.base import Source

_API = "https://imslp.org/api.php"
_MIRRORS = ("vmirror.imslp.org", "ks15.imslp.org", "s9.imslp.org")

#: MediaWiki's per-request cap on ``titles=`` for anonymous clients.
_TITLES_PER_QUERY = 50

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
_KIND_ORDER = ("midi", "musicxml", "pdf", "zip")

#: In the rendered page: ``<a href="/wiki/File:NAME" ...>#INDEX</a> - 9.29MB``
#: (the tail is captured in a lookahead so adjacent links are not swallowed).
_FILE_LINK_RE = re.compile(
    r'href="/wiki/File:([^"]+)"[^>]*>#(\d+)</a>(?=(.{0,1000}))', re.DOTALL
)
_SIZE_RE = re.compile(r"\s*-\s*([\d.]+)\s*([KMG]?B)\b", re.IGNORECASE)
_UNITS = {"B": 1, "KB": 2**10, "MB": 2**20, "GB": 2**30}
#: A wikitext heading, e.g. ``====Parts====`` -> ("====", "Parts").
_FILE_NUM_RE = re.compile(r"\|\s*File Name (\d+)\s*=")
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
        pick: Optional[picks.Pick] = None,
        resolve_formats: bool = True,
    ):
        """
        Args:
            timeout: Per-request timeout (seconds).
            mirrors: Download hosts; the first is used.
            max_bytes: Default size cap for :meth:`fetch` (``None``: no cap).
            pick: Default file-choice strategy for :meth:`fetch` (``None``:
                :func:`~scoreseek.picks.prefer_safe_renderable`).
            resolve_formats: Fill each search hit's ``formats`` from its page's
                file list (one extra batched request per search).
        """
        self.timeout = timeout
        self.mirrors = tuple(mirrors)
        self.max_bytes = max_bytes
        self.pick = pick or picks.prefer_safe_renderable
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
            hits = self._fill_formats(hits)
        return hits

    def _fill_formats(self, hits: List[ScoreRef]) -> List[ScoreRef]:
        """Fill each hit's ``formats`` from its page's files (best effort), point
        redirect hits at their target page, and drop hits whose target an earlier
        hit already covers. Returns the kept hits."""
        try:
            texts = self._wikitexts([h.id for h in hits])
        except Exception as e:  # formats are a convenience; keep the hits
            warnings.warn(f"IMSLP: could not resolve formats: {e}", stacklevel=3)
            return hits
        kept, seen = [], set()
        for h in hits:
            target, text = texts.get(h.id, (h.id, None))
            if target in seen:
                continue  # several redirects to one page: keep the best-ranked
            seen.add(target)
            if target != h.id:
                h.metadata["page"] = target
                h.metadata["redirected_from"] = h.id
            if text is not None:
                h.formats = _formats_of(f["kind"] for f in _parse_files(text))
            kept.append(h)
        return kept

    # -- files: wikitext (what/where/license) + rendered HTML (index, size) --
    def _wikitexts(self, pages: List[str]) -> dict:
        """{requested title -> (resolved page title, wikitext)}, following
        redirects, batched (one request per 50 titles, plus any continuations)."""
        out = {}
        for i in range(0, len(pages), _TITLES_PER_QUERY):
            batch = pages[i : i + _TITLES_PER_QUERY]
            params = {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": "|".join(batch),
                "redirects": 1,
                "format": "json",
            }
            texts, renames, cont = {}, {}, {}
            while True:
                url = f"{_API}?" + urllib.parse.urlencode({**params, **cont})
                data = self._get_json(url)
                query = data.get("query") or {}
                for key in ("normalized", "redirects"):
                    for r in query.get(key) or []:
                        renames[r.get("from", "")] = r.get("to", "")
                for pg in (query.get("pages") or {}).values():
                    revs = pg.get("revisions") or []
                    if revs:
                        rev = revs[0]
                        main = rev.get("slots", {}).get("main", {}) or {}
                        texts[pg.get("title", "")] = main.get("*") or rev.get("*", "")
                # a big batch can be cut short; continue until complete
                cont = _continuation(data)
                if not cont:
                    break
            for title in batch:
                resolved, hops = title, 0
                while resolved in renames and hops < 5:  # normalized -> redirect
                    resolved, hops = renames[resolved], hops + 1
                if resolved in texts:
                    out[title] = (resolved, texts[resolved])
        if len(pages) == 1 and not out and len(texts) == 1:
            # a single requested page maps unambiguously (title spelled differently)
            ((resolved, text),) = texts.items()
            out[pages[0]] = (resolved, text)
        return out

    def _manifest(self, page: str) -> tuple:
        """(resolved page title, score files in page order) for *page*."""
        resolved, text = self._wikitexts([page]).get(page, (page, ""))
        return resolved, _parse_files(text)

    def _file_info(self, page: str) -> dict:
        """{filename -> (numeric IMSLP file index, size, size upper bound)}."""
        url = f"{_API}?" + urllib.parse.urlencode(
            {
                "action": "parse",
                "page": page,
                "prop": "text",
                "redirects": 1,
                "format": "json",
            }
        )
        data = self._get_json(url)
        html = ((data.get("parse") or {}).get("text") or {}).get("*") or ""
        out = {}
        for fname, idx, tail in _FILE_LINK_RE.findall(html):
            out[urllib.parse.unquote(fname)] = (idx, *_parse_size(tail))
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

        Covers the score files and the MIDI renditions IMSLP keeps with its
        recordings. Two requests (wikitext + rendered page). Also fills
        ``ref.formats`` and, for a redirect hit, ``ref.metadata["page"]``.
        Sizes are IMSLP's displayed ones (``None`` where it shows none).
        """
        page = ref.metadata.get("page") or ref.id
        resolved, manifest = self._manifest(page)
        if resolved != page:
            ref.metadata["page"] = resolved
        info = self._file_info(resolved) if manifest else {}
        files = []
        for f in manifest:
            index, size, size_max = info.get(f["filename"], (None, None, None))
            meta = {"index": index, "copyright": f["copyright"]}
            if size_max is not None:
                meta["size_max"] = size_max
            files.append(
                ScoreFile(
                    filename=f["filename"],
                    kind=f["kind"],
                    size=size,
                    license=f["license"],
                    description=f["description"],
                    section=f["section"],
                    metadata=meta,
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
        max_bytes=UNSET,
        pick=None,
    ) -> str:
        """Download one of *ref*'s files and return its cached path.

        Args:
            ref: An IMSLP hit.
            fmt: Restrict to a format (``"midi"``/``"mid"``, ``"musicxml"``/
                ``"mxl"``, ``"pdf"``, ``"zip"``).
            max_bytes: Size cap in bytes (default: the source's ``max_bytes``,
                50 MB unless configured). Files IMSLP lists as larger are skipped,
                and the download stops if it passes the cap. ``None``: no cap.
            pick: Which file to take. A ``filename`` from :meth:`files`, or a
                strategy ``(List[ScoreFile]) -> ScoreFile`` choosing among the
                files that match ``fmt`` and fit the cap. ``None`` (default): the
                source's ``pick`` (:func:`~scoreseek.picks.prefer_safe_renderable`
                unless configured; see also :func:`~scoreseek.picks.smallest`).
                If a strategy's choice turns out over the cap while downloading
                (its size was not listed), the next choice is tried.

        Updates ``ref.license`` to the chosen file's own license (search hits
        carry a provisional ``PUBLIC_DOMAIN`` tag, since IMSLP is PD-dominant).

        Raises:
            RuntimeError: No score file, no file matching ``fmt``, or a ``pick``
                filename that is unknown or not of format ``fmt``.
            ScoreTooLargeError: Every candidate exceeds ``max_bytes`` (the message
                names the smallest ones), or the chosen file does.
        """
        max_bytes = self._cap(max_bytes)
        pick = self.pick if pick is None else pick
        files = self.files(ref)
        page = ref.metadata.get("page") or ref.id
        where = f"IMSLP: {page!r} "
        if not files:
            raise RuntimeError(f"IMSLP: no score files found for {page!r}")

        if isinstance(pick, str):
            chosen = picks.by_filename(
                files, pick, fmt=fmt, max_bytes=max_bytes, where=where
            )
            return self._download(ref, chosen, page, max_bytes)

        pool = picks.candidates(files, fmt=fmt, max_bytes=max_bytes, where=where)
        tried = []
        while pool:
            chosen = picks.apply_pick(pick, pool, where=where)
            try:
                return self._download(ref, chosen, page, max_bytes)
            except ScoreTooLargeError:
                # its size was not listed; drop it (by value, as apply_pick
                # matched it) and let the strategy choose again
                tried.append(chosen)
                pool = [f for f in pool if f != chosen]
        raise picks.too_large_error(tried, fmt=fmt, max_bytes=max_bytes, where=where)

    def _download(self, ref, chosen: ScoreFile, page: str, max_bytes) -> str:
        index = chosen.metadata.get("index")
        if index is None:
            raise RuntimeError(
                f"IMSLP: could not resolve a file index for "
                f"{chosen.filename!r} on {page!r}"
            )
        url = self._mirror_url(chosen.filename, index)
        data = self._get_bytes(url, max_bytes=max_bytes)
        # Refine the hit's license to the actual file's copyright tag.
        ref.license = chosen.license
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", chosen.filename)
        out = _http.cache_dir("imslp") / f"IMSLP{index}-{safe}"
        out.write_bytes(data)
        return str(out)


#: Re-exported: the file-choice strategies live in :mod:`scoreseek.picks`.
prefer_safe_renderable = picks.prefer_safe_renderable
smallest = picks.smallest


def _continuation(data: dict) -> dict:
    """Request params to fetch the rest of a truncated query, or ``{}``.

    IMSLP runs MediaWiki 1.18, which reports ``query-continue: {module: {...}}``;
    newer servers report a flat ``continue: {...}``. Both are honoured.
    """
    if data.get("continue"):
        return dict(data["continue"])
    cont = {}
    for params in (data.get("query-continue") or {}).values():
        cont.update(params)
    return cont


def _parse_size(tail: str) -> tuple:
    """(bytes, upper-bound bytes) from the text after a file link, e.g.
    ``" - 9.29MB - 7:28"`` (or with the number or a preview badge wrapped in
    tags). IMSLP rounds to 0.01 of the unit, so the bound adds half of that.
    The tail is cut at the next file link so a size-less file never borrows its
    neighbour's. ``(None, None)`` when no size is shown."""
    tail = tail.split('href="/wiki/File:', 1)[0]
    m = _SIZE_RE.search(re.sub(r"<[^>]+>", "", tail))
    if not m:
        return None, None
    number, unit = m.group(1), _UNITS[m.group(2).upper()]
    decimals = len(number.split(".", 1)[1]) if "." in number else 0
    half_step = 0.5 * 10**-decimals * unit
    return int(float(number) * unit), int(float(number) * unit + half_step)


def _formats_of(kinds) -> tuple:
    """Distinct kinds in a stable, renderable-first order."""
    present = set(kinds)
    return tuple(k for k in _KIND_ORDER if k in present)


def _parse_files(wikitext: str) -> List[dict]:
    """Score files from a work page's wikitext, in page order: the FILES section
    (``imslpfile`` blocks), then the MIDI/MusicXML kept among the recordings
    (``imslpaudio`` blocks; audio formats are not score files and are skipped).
    Each: {filename, ext, kind, copyright, license, description, section}."""
    if "*****FILES*****" in wikitext:
        before, files_section = wikitext.split("*****FILES*****", 1)
    else:
        before, files_section = wikitext, ""
    audio_section = (
        before.split("*****AUDIO*****", 1)[1] if "*****AUDIO*****" in before else ""
    )
    return _parse_blocks(files_section, "imslpfile") + _parse_blocks(
        audio_section, "imslpaudio"
    )


def _parse_blocks(section_text: str, block: str) -> List[dict]:
    """Files from the ``{{#fte:<block>`` templates of one wikitext section."""
    opener = "{{#fte:" + block
    files, headings = [], {}
    for chunk in re.split(r"(?=\{\{#fte:)", section_text):
        if chunk.startswith(opener):
            section = " / ".join(headings[k] for k in sorted(headings))
            cr = _field(chunk, "Copyright")
            for n in _FILE_NUM_RE.findall(chunk):  # File Name 1, 2, ... (gaps ok)
                fname = _field(chunk, f"File Name {n}")
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
