"""Web MIDI source: search free-MIDI websites (bitmidi, midis101, midiworld).

Popular, film and game music, which no clean corpus has, lives on hobbyist
MIDI sites. Their files are user uploads of copyrighted works, so every hit is
tagged :attr:`License.GRAY`: hidden by the default copyright-safe search, shown
with ``allow_copyrighted=True``. Use them for private study.

Each site is a small strategy (:class:`MidiSite`): how to turn a query into
``(title, id)`` pairs by reading its search page, and how to build the download
URL for an id. :data:`SITES` is the registry; pass ``sites=`` to pick or add
some. The sites have no API, so this is regular-expression scraping of their
HTML, and a site that changes its markup returns no hits rather than raising
(search is best effort; fetch raises).

Fetched files are cached under scoreseek's cache dir (``webmidi/<site>/``).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from scoreseek import _http
from scoreseek._match import match_score
from scoreseek.base import License, ScoreRef
from scoreseek.sources.base import Source

#: Some of these sites refuse a library User-Agent; they serve a browser one.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
    )
}


@dataclass(frozen=True)
class MidiSite:
    """How to search one MIDI website and download from it.

    Attributes:
        name: Short site name (used in ref ids and the cache path).
        search_url: Format string with ``{q}`` (URL-quoted query).
        hit_pattern: Regex over the search page; groups ``id`` and ``title``.
        download_url: Format string with ``{id}``.
        page_url: Format string with ``{id}``: the human-facing page.
        referer: Send the page URL as ``Referer`` when downloading.
        clean_title: Turns the matched title text into a display title.
        resolve_download: For sites whose file URL is only on the hit's page:
            ``(page_html) -> download URL``. The page is fetched first when
            this is set; otherwise ``download_url`` is used directly.
    """

    name: str
    search_url: str
    hit_pattern: str
    download_url: str
    page_url: str
    referer: bool = False
    clean_title: Callable[[str], str] = field(default=lambda s: s, compare=False)
    resolve_download: Optional[Callable[[str], str]] = field(default=None, compare=False)

    def quote(self, text: str) -> str:
        """URL-quote a query: ``+`` for spaces in a query string, ``%20`` in a path."""
        if "?" in self.search_url:
            return urllib.parse.quote_plus(text)
        return urllib.parse.quote(text, safe="")

    def parse_hits(self, html: str) -> List[Tuple[str, str]]:
        """``(id, title)`` pairs found on a search page, de-duplicated."""
        seen, out = set(), []
        for m in re.finditer(self.hit_pattern, html, flags=re.I | re.S):
            ident = m.group("id")
            if ident in seen:
                continue
            seen.add(ident)
            out.append((ident, self.clean_title(_strip_tags(m.group("title")))))
        return out


def _strip_tags(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _bitmidi_download(html: str) -> str:
    """bitmidi pages name the real file in their JSON state."""
    m = re.search(r'"downloadUrl":"(?P<u>/uploads/\d+\.mid)"', html)
    if not m:
        raise RuntimeError("bitmidi: no download link on the page")
    return "https://bitmidi.com" + m.group("u")


def _slug_title(slug: str) -> str:
    """``58174-soundtrack-star-wars-sw-main`` -> ``soundtrack star wars sw main``."""
    words = slug.split("-")
    if words and words[0].isdigit():
        words = words[1:]
    if words and words[-1] == "mid":
        words = words[:-1]
    return " ".join(words)


#: The built-in sites (verified 2026-10-03).
SITES: Dict[str, MidiSite] = {
    "bitmidi": MidiSite(
        name="bitmidi",
        search_url="https://bitmidi.com/search?q={q}",
        hit_pattern=r'href="/(?P<id>[a-z0-9-]+-mid)"(?P<title>)',
        download_url="https://bitmidi.com/{id}",  # unused: see resolve_download
        page_url="https://bitmidi.com/{id}",
        resolve_download=lambda html: _bitmidi_download(html),
    ),
    "midis101": MidiSite(
        name="midis101",
        search_url="https://www.midis101.com/search/{q}",
        hit_pattern=r'href="/free-midi/(?P<id>\d+-[a-z0-9-]+)"(?P<title>)',
        download_url="https://www.midis101.com/download/{id}",
        page_url="https://www.midis101.com/free-midi/{id}",
        referer=True,
    ),
    "midiworld": MidiSite(
        name="midiworld",
        search_url="https://www.midiworld.com/search/?q={q}",
        hit_pattern=(
            r"<li>\s*(?P<title>[^<]{3,200}?)\s*-\s*<a[^>]*"
            r'href="https://www\.midiworld\.com/download/(?P<id>\d+)"'
        ),
        download_url="https://www.midiworld.com/download/{id}",
        page_url="https://www.midiworld.com/download/{id}",
    ),
}


class WebMidiSource(Source):
    """Search free-MIDI websites; every hit is ``License.GRAY``.

    Args:
        sites: Site names from :data:`SITES` or :class:`MidiSite` objects
            (default: all built-in sites).
        name: Registry name (default ``'webmidi'``).
        headers: Request headers (default: a browser User-Agent).
        timeout: Per-request timeout in seconds.
    """

    name = "webmidi"
    license = License.GRAY
    uniform_license = True

    def __init__(
        self,
        sites: Optional[Iterable] = None,
        *,
        name: str = "webmidi",
        headers: Optional[dict] = None,
        timeout: float = 20.0,
    ):
        self.name = name
        chosen = list(SITES) if sites is None else list(sites)
        self.sites = {
            (s if isinstance(s, MidiSite) else SITES[s]).name: (
                s if isinstance(s, MidiSite) else SITES[s]
            )
            for s in chosen
        }
        self.headers = dict(BROWSER_HEADERS if headers is None else headers)
        self.timeout = timeout

    # Thin wrappers so tests can monkeypatch at the instance.
    def _get_text(self, url: str, headers: Optional[dict] = None) -> str:
        return _http.get_text(
            url, timeout=self.timeout, headers={**self.headers, **(headers or {})}
        )

    def _get_bytes(self, url: str, headers: Optional[dict] = None) -> bytes:
        return _http.get_bytes(
            url, timeout=self.timeout, headers={**self.headers, **(headers or {})}
        )

    def search(self, query="", *, title="", composer="", limit=10) -> List[ScoreRef]:
        """Query each site's search page; rank hits by title match."""
        text = " ".join(x for x in (query, title, composer) if x).strip()
        if not text:
            return []
        hits: List[ScoreRef] = []
        for site in self.sites.values():
            url = site.search_url.format(q=site.quote(text))
            try:
                html = self._get_text(url)
            except Exception:  # best effort: one site down must not sink search
                continue
            for ident, raw_title in site.parse_hits(html):
                cand = raw_title or _slug_title(ident)
                score = match_score(text, cand_title=cand) or match_score(
                    query, title, composer, cand_title=cand
                )
                if score is None:
                    continue
                hits.append(
                    self._ref(
                        title=cand,
                        id=f"{site.name}:{ident}",
                        composer=composer,
                        formats=("midi",),
                        url=site.page_url.format(id=ident),
                        score=score,
                        metadata={"site": site.name, "site_id": ident},
                    )
                )
        hits.sort(key=lambda r: -r.score)
        return hits[:limit]

    def _download_url(self, site: MidiSite, ident: str) -> str:
        if site.resolve_download is not None:
            return site.resolve_download(self._get_text(site.page_url.format(id=ident)))
        return site.download_url.format(id=ident)

    def fetch(self, ref: ScoreRef, *, fmt: Optional[str] = None) -> str:
        """Download the MIDI (cached) and return its local path."""
        if fmt not in (None, "midi", "mid"):
            raise ValueError(f"webmidi only serves MIDI, not {fmt!r}")
        site_name = ref.metadata.get("site") or ref.id.split(":", 1)[0]
        ident = ref.metadata.get("site_id") or ref.id.split(":", 1)[1]
        site = self.sites.get(site_name) or SITES[site_name]
        path = _http.cache_dir("webmidi", site.name) / f"{_safe(ident)}.mid"
        if path.exists() and path.stat().st_size:
            return str(path)
        headers = {"Referer": site.page_url.format(id=ident)} if site.referer else None
        data = self._get_bytes(self._download_url(site, ident), headers=headers)
        if data[:4] != b"MThd":
            raise RuntimeError(
                f"{site.name}: {ident!r} did not return a MIDI file "
                f"(got {data[:16]!r}); the site may block automated downloads."
            )
        path.write_bytes(data)
        return str(path)


def _safe(ident: str) -> str:
    """A collision-free, traversal-free file stem for a site id."""
    import hashlib

    digest = hashlib.sha1(ident.encode("utf-8")).hexdigest()[:10]
    readable = re.sub(r"[^A-Za-z0-9_-]+", "_", ident)[:80]
    return f"{readable}-{digest}"
