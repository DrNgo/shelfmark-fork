"""Audiobookshelf integration: a read-only client plus its settings tab.

Shelfmark never writes to Audiobookshelf. The client exists so the fork can
name your ABS libraries in settings (destination routing) and know what you
already own (duplicate detection).
"""

from shelfmark.audiobookshelf.client import AudiobookshelfClient, AudiobookshelfLibrary
from shelfmark.audiobookshelf.settings import build_client_from_config

__all__ = [
    "AudiobookshelfClient",
    "AudiobookshelfLibrary",
    "build_client_from_config",
]
