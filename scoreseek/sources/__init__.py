"""Built-in score sources.

Importing this package makes the built-in source classes available. The
zero-config default sources (The Session + OpenScore Lieder) are registered by
:mod:`scoreseek` itself; the heavier / license-gated ones
(:class:`IMSLPSource`, :class:`PDMXSource`, :class:`ChordonomiconSource`,
:class:`KaggleChordsSource`) are imported here but registered explicitly by the
caller (see the scoreseek README).
"""

from scoreseek.sources.base import Source
from scoreseek.sources.chordonomicon import ChordonomiconSource
from scoreseek.sources.imslp import IMSLPSource
from scoreseek.sources.kaggle_chords import KaggleChordsSource
from scoreseek.sources.local import LocalFolderSource
from scoreseek.sources.openscore import OpenScoreLiederSource
from scoreseek.sources.pdmx import PDMXSource
from scoreseek.sources.thesession import TheSessionSource

__all__ = [
    "Source",
    "LocalFolderSource",
    "TheSessionSource",
    "OpenScoreLiederSource",
    "IMSLPSource",
    "PDMXSource",
    "ChordonomiconSource",
    "KaggleChordsSource",
]
