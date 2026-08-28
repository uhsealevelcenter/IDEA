"""Run cancellation abstraction shared by graph nodes and service workers."""

from __future__ import annotations

import threading


class RunCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason: str | None = None

    @property
    def requested(self) -> bool:
        return self._event.is_set()

    def request(self, reason: str = "user_requested") -> None:
        self.reason = reason
        self._event.set()
