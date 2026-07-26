"""IMSLP / Petrucci source: the public-domain classical giant, via its API.

IMSLP is exposed through a legacy MediaWiki API at ``imslp.org/api.php``.
:meth:`~IMSLPSource.search` uses ``action=query&list=search`` (page titles have
the form ``"<Work> (<Surname>, <First>)"``). :meth:`~IMSLPSource.fetch` resolves
a work's per-file manifest in three steps -- (1) page **wikitext** for the
score-vs-audio split and the per-file ``|Copyright=`` tag, (2) the **rendered
HTML** for the numeric IMSLP file index, (3) download from an IMSLP **mirror**
host -- and returns the native file (mostly PDF, some MIDI; MusicXML is rare).

Two things make this work reliably and are easy to get wrong (both handled here):

* Direct ``imslp.org/images/...`` downloads now 302 to an MTCaptcha bot-wall.
  The mirror path (``vmirror.imslp.org/files/imglnks/usimg/...``) serves the
  identical bytes with no CAPTCHA.
* IMSLP's MediaWiki ignores ``formatversion=2``: ``query.pages`` is a dict keyed
  by pageid and ``parse.text`` is ``{"*": html}``. Parsed legacy-style here.

Stdlib-only (``urllib`` + ``hashlib`` + ``re``). Formats are honestly
PDF-dominant, so each hit's ``formats`` are surfaced per-file; filter to
renderable formats (``.mid``/``.mxl``/``.xml``) when a hit must reach audio.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import List, Optional

from scoreseek import _http
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

_API = "https://imslp.org/api.php"
_MIRRORS = ("vmirror.imslp.org", "ks15.imslp.org", "s9.imslp.org")

#: A hit's page title looks like "Work Title (Surname, First)".
_TITLE_RE = re.compile(r"^(.*) \(([^,]+), (.+)\)$")
#: Recognized native score extensions (PDF-dominant; MIDI/MusicXML rarer).
_SCORE_EXTS = (".pdf", ".mid", ".midi", ".mxl", ".xml", ".zip")
_RENDERABLE = (".mid", ".midi", ".mxl", ".xml")

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

    def __init__(self, *, timeout: float = 20.0, mirrors=_MIRRORS):
        self.timeout = timeout
        self.mirrors = tuple(mirrors)

    # -- networking (thin wrappers so tests can monkeypatch at the instance) --
    def _get_json(self, url: str) -> dict:
        return _http.get_json(url, timeout=self.timeout)

    def _get_text(self, url: str) -> str:
        return _http.get_text(url, timeout=self.timeout)

    def _get_bytes(self, url: str) -> bytes:
        return _http.get_bytes(url, timeout=self.timeout)

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
                    formats=(),  # populated lazily by fetch()'s manifest
                    url="https://imslp.org/wiki/"
                    + urllib.parse.quote(page.replace(" ", "_")),
                    score=round(1.0 - i / max(len(results), 1), 3),
                    metadata={"page": page},
                )
            )
            if len(hits) >= limit:
                break
        return hits

    # -- fetch: 3 steps (wikitext -> file index in HTML -> mirror download) --
    def _manifest(self, page: str) -> List[dict]:
        """Return score files for *page*: [{filename, ext, copyright, license}]."""
        wt_url = f"{_API}?" + urllib.parse.urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "rvprop": "content",
                "rvslots": "main",
                "titles": page,
                "format": "json",
            }
        )
        data = self._get_json(wt_url)
        pages = (data.get("query") or {}).get("pages") or {}
        if not pages:
            return []
        pg = next(iter(pages.values()))
        revs = pg.get("revisions") or []
        if not revs:
            return []
        rev = revs[0]
        content = (rev.get("slots", {}).get("main", {}) or {}).get("*") or rev.get(
            "*", ""
        )
        if "*****FILES*****" not in content:
            return []
        files_section = content.split("*****FILES*****", 1)[1]
        files = []
        for chunk in files_section.split("{{#fte:imslpfile")[1:]:
            fname = _field(chunk, "File Name 1")
            if not fname:
                continue
            ext = _ext_of(fname)
            if ext not in _SCORE_EXTS:
                continue
            cr = _field(chunk, "Copyright")
            files.append(
                {
                    "filename": fname,
                    "ext": ext,
                    "copyright": cr,
                    "license": _copyright_to_license(cr),
                    "description": _field(chunk, "File Description 1"),
                }
            )
        return files

    def _file_indexes(self, page: str) -> dict:
        """{filename -> numeric IMSLP file index} from the rendered work page."""
        url = f"{_API}?" + urllib.parse.urlencode(
            {"action": "parse", "page": page, "prop": "text", "format": "json"}
        )
        data = self._get_json(url)
        html = ((data.get("parse") or {}).get("text") or {}).get("*") or ""
        out = {}
        for fname, idx in re.findall(
            r'href="/wiki/File:([^"]+)"[^>]*>#(\d+)</a>', html
        ):
            out[urllib.parse.unquote(fname)] = idx
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

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Resolve *ref*'s files and download one (prefers a renderable format).

        Returns a cached filesystem path, and updates ``ref.license`` to the
        chosen file's true per-file license (search hits carry a provisional
        ``PUBLIC_DOMAIN`` tag, since IMSLP is PD-dominant). Raises
        ``RuntimeError`` if the work has no downloadable file matching ``fmt``.
        """
        page = ref.metadata.get("page") or ref.id
        files = self._manifest(page)
        if not files:
            raise RuntimeError(f"IMSLP: no score files found for {page!r}")

        # "midi" accepts .mid/.midi, "musicxml" accepts .mxl/.xml, etc.
        want_exts = (
            _FMT_EXTS.get(fmt.lower().lstrip("."), ("." + fmt.lower().lstrip("."),))
            if fmt
            else None
        )

        def _rank(f):
            if want_exts:
                return (0 if f["ext"] in want_exts else 2, 0)
            # no fmt requested: prefer renderable over pdf/zip
            return (0 if f["ext"] in _RENDERABLE else 1, 0)

        chosen = sorted(files, key=_rank)[0]
        if want_exts and chosen["ext"] not in want_exts:
            raise RuntimeError(
                f"IMSLP: {page!r} has no {fmt} file "
                f"(available: {sorted({f['ext'] for f in files})})"
            )

        # Refine the hit's license to the actual file's copyright tag.
        ref.license = chosen["license"]

        indexes = self._file_indexes(page)
        index = indexes.get(chosen["filename"])
        if index is None:
            raise RuntimeError(
                f"IMSLP: could not resolve a file index for "
                f"{chosen['filename']!r} on {page!r}"
            )
        url = self._mirror_url(chosen["filename"], index)
        data = self._get_bytes(url)
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", chosen["filename"])
        out = _http.cache_dir("imslp") / f"IMSLP{index}-{safe}"
        out.write_bytes(data)
        return str(out)


def _field(chunk: str, name: str) -> str:
    """Extract a single ``|<name>= value`` field from an ``#fte:imslpfile`` block."""
    m = re.search(rf"\|\s*{re.escape(name)}\s*=\s*([^\n|]*)", chunk)
    return m.group(1).strip() if m else ""
