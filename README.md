# scoreseek

Find a musical **score** by metadata — across many sources, behind one call.

```python
import scoreseek

hits = scoreseek.search("Cooley's")   # out of the box: The Session (CC-BY-SA)
ref = hits[0]
path = ref.fetch()                    # -> a local .abc file
# hand straight to audiate.render(path) to hear it
```

Every hit is a license-tagged `ScoreRef`, so **public-domain / permissive**
results stay cleanly separated from **copyrighted / gray** ones — searches are
copyright-safe by default.

## Install

```bash
pip install scoreseek                # zero required dependencies
pip install "scoreseek[render]"      # + audiate, to render hits to audio
```

## Why

Scores live in many places — public-domain corpora (PDMX, IMSLP, OpenScore),
downloadable datasets, chord/lyric collections, and your own folders. `scoreseek`
puts them behind **one pluggable-source interface** and tags every result with a
license, so you can ask for "only what I can legally build on" or opt into
copyrighted material explicitly.

## Sources

A *source* answers `search(query) -> [ScoreRef]` and optionally `fetch(ref) -> path`.
Register any object with that shape.

**On by default** (zero-config, lightweight, cleanly licensed):

| Source | What | License |
|---|---|---|
| `thesession` | thesession.org traditional tunes (ABC) | CC-BY-SA 4.0 |
| `openscore_lieder` | ~1,356 OpenScore art songs (compressed MusicXML) | CC0 |

**Opt-in** (import from `scoreseek.sources` and `register_source(...)` — heavier, or license-gated):

| Source | What | License | Notes |
|---|---|---|---|
| `IMSLPSource` | IMSLP/Petrucci public-domain classical | Public Domain | live API; mostly PDF, some MIDI |
| `PDMXSource` | 250K Public-Domain MusicXML corpus | PD / CC0 | **local corpus** — one-time download (see below) |
| `ChordonomiconSource` | 680K chord progressions | CC-BY-NC (gray lane) | no title/artist — genre/chord-text search |
| `KaggleChordsSource` | 135K chords+lyrics songs (via `sung`) | scraped / gray | needs Kaggle creds + a ~283 MB download |
| `LocalFolderSource` | a folder of `.mid`/MusicXML/ABC/`kern` files | you set it | offline |

```python
from scoreseek import register_source, License
from scoreseek.sources import IMSLPSource, LocalFolderSource

register_source(IMSLPSource())                                   # opt in to IMSLP
register_source(LocalFolderSource("~/scores", license=License.PUBLIC_DOMAIN))

hits = scoreseek.search("nocturne", composer="Chopin", sources=["imslp"])
hits = scoreseek.search("hey jude", allow_copyrighted=True)      # opt into gray/copyrighted
```

### Canonicalize a messy query (MusicBrainz)

`normalize=True` resolves a fuzzy `"title [+ artist]"` to a canonical work via
MusicBrainz *before* searching (adds a ~1 s network round-trip), and stamps the
resolved MBID onto each hit:

```python
hits = scoreseek.search("moonlight sonata beethovan", normalize=True)
```

### PDMX: a local public-domain corpus

PDMX ships only as bulk tarballs, so `PDMXSource` searches a local extraction.
Download once (index-only = 225 MB; + MusicXML = ~2.1 GB) from
[Zenodo record 15571083](https://zenodo.org/records/15571083) and point the
source at it:

```python
from scoreseek.sources import PDMXSource
register_source(PDMXSource("~/pdmx"))          # dir with PDMX.csv (+ extracted mxl/, mid/)
```

`search()` works with just `PDMX.csv`; `fetch()` needs the extracted `mxl/`/`mid/`
trees and otherwise raises with the exact tarball URL to download.

### Write your own source

```python
from scoreseek.sources import Source
from scoreseek.base import License

class MySource(Source):
    name = "mine"
    license = License.CC0

    def search(self, query="", *, title="", composer="", limit=10):
        return [self._ref(title="...", id="...", formats=("musicxml",))]

    def fetch(self, ref, *, fmt=None):
        ...
        return "/path/to/score.musicxml"
```

## License tags

`License` classifies each hit: `PUBLIC_DOMAIN`, `CC0`, `PERMISSIVE` (CC-BY/-SA,
MIT, …), `NONCOMMERCIAL` (CC-BY-NC), `COPYRIGHTED`, `GRAY` (scrapes/unspecified),
`UNKNOWN`.

`search(..., allow_copyrighted=False)` (the default) is **copyright-safe**: it
returns the commercial-safe lanes (`PUBLIC_DOMAIN`/`CC0`/`PERMISSIVE`) plus
`UNKNOWN` (unclassified provenance, e.g. your own local folders), and drops the
**restricted lanes** — `COPYRIGHTED`, `GRAY`, and `NONCOMMERCIAL` (a known usage
restriction). Pass `allow_copyrighted=True` to include them.

## Where it fits

`scoreseek` is the **search** stage of a larger pipeline:
**scoreseek** (find a score) → [`audiate`](https://github.com/thorwhalen/audiate)
(render to audio) → [`arioso`](https://github.com/thorwhalen/arioso)
(AI-enhance).

## License

MIT
