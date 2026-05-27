"""Shared echo-ignore window for display thermostat mirroring."""

from __future__ import annotations

import time


class DisplayEchoGuard:
    """Tracks display entities recently written by this integration."""

    def __init__(self, window_seconds: float = 2.0) -> None:
        self._window_seconds = window_seconds
        self._ignore_until: dict[str, float] = {}

    def mark_write(self, entity_id: str) -> None:
        self._ignore_until[entity_id] = time.monotonic() + self._window_seconds

    def should_ignore(self, entity_id: str) -> bool:
        until = self._ignore_until.get(entity_id)
        if until is None:
            return False
        if time.monotonic() <= until:
            return True
        self._ignore_until.pop(entity_id, None)
        return False
