from __future__ import annotations

import time


class Throttler:
    """Rate-limits how often a caller emits `task.progress` events.

    Not used by EventBus itself — callers (rip/transcode services) check
    `should_emit()` before calling `emit()` so progress events land at most
    once per `interval` seconds.
    """

    def __init__(self, interval: float = 1.0) -> None:
        self._interval = interval
        self._last_emit: float | None = None

    def should_emit(self, *, now: float | None = None) -> bool:
        current = now if now is not None else time.monotonic()
        if self._last_emit is None or current - self._last_emit >= self._interval:
            self._last_emit = current
            return True
        return False
