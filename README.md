# scoreseek

Find a musical **score** by metadata — across many sources, behind one call.

```python
import scoreseek

hits = scoreseek.search("Cooley's")  # out of the box: The Session (CC-BY-SA)
ref = hits[0]
path = ref.fetch()  # -> a local .abc file
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

| Source | What | License | Status |
|---|---|---|---|
| `thesession` | thesession.org traditional tunes (ABC) | CC-BY-SA 4.0 | **built in, on by default** |
| `LocalFolderSource` | a folder of `.mid`/MusicXML/ABC/`kern` files | you set it | built in |
| PDMX · OpenScore Lieder · IMSLP · MusicBrainz (normalizer) | public-domain / CC0 corpora + APIs | PD/CC0 | planned |

```python
from scoreseek import register_source, License
from scoreseek.sources import LocalFolderSource

register_source(LocalFolderSource("~/scores", license=License.PUBLIC_DOMAIN))

hits = scoreseek.search("prelude", sources=["local"])
hits = scoreseek.search("hey jude", allow_copyrighted=True)  # opt into gray/copyrighted
```

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
`UNKNOWN`. `search(..., allow_copyrighted=False)` (the default) drops
`COPYRIGHTED` and `GRAY` hits.

## Where it fits

`scoreseek` is the **search** stage of a larger pipeline:
**scoreseek** (find a score) → [`audiate`](https://github.com/thorwhalen/audiate)
(render to audio) → [`arioso`](https://github.com/thorwhalen/arioso)
(AI-enhance).

## License

MIT
