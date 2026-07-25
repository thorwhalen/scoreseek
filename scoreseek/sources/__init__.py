"""Built-in score sources.

Importing this package makes the built-in source classes available and (via
:mod:`scoreseek`) registers the default out-of-the-box source (The Session).
"""

from scoreseek.sources.base import Source
from scoreseek.sources.local import LocalFolderSource
from scoreseek.sources.thesession import TheSessionSource

__all__ = ["Source", "LocalFolderSource", "TheSessionSource"]
