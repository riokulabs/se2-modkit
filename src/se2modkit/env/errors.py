"""The one error type environment code raises for a missing or unusable piece."""

from __future__ import annotations


class EnvError(RuntimeError):
    """Something the command needs is missing; the message is plain sentences, never shown with a traceback."""
