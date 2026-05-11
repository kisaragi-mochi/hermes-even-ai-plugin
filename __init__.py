"""Even AI platform adapter for Hermes Agent.

Connects Even Realities G2 smart glasses (via Even App "Add Agent" UI) to
Hermes as a first-class platform alongside Telegram/Discord/LINE/etc.

See ``adapter.py`` for the implementation entry point and
``plugin.yaml`` for metadata.
"""

from .adapter import register

__all__ = ["register"]
