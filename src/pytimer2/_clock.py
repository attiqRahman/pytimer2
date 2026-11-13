"""Clocks: the only source of time for every timer in pytimer2."""

from __future__ import annotations

import sys
import threading
import time
from typing import Optional

from ._scheduler import Scheduler

__all__ = ["Clock", "FakeClock", "SystemClock", "default_clock"]


class Clock:
    """Base class for clocks.

    A clock returns the current time in seconds from an arbitrary starting point
    and owns exactly one :class:`Scheduler` (created on first use). Subclasses
    implement :meth:`now`.
    """

    #: True for clocks that only move when told to (no threads are started).
    fake: bool = False
    #: Upper bound for one scheduler sleep, or None. Used by clocks that can jump.
    max_wait: Optional[float] = None

    def __init__(self) -> None:
        self._scheduler: Optional[Scheduler] = None
        self._scheduler_lock = threading.Lock()

    def now(self) -> float:
        """Return the current time in seconds."""
        raise NotImplementedError

    @property
    def scheduler(self) -> Scheduler:
        """The scheduler shared by every timer that uses this clock."""
        if self._scheduler is None:
            with self._scheduler_lock:
                if self._scheduler is None:
                    self._scheduler = Scheduler(self)
        return self._scheduler


class SystemClock(Clock):
    """Real time based on :func:`time.monotonic`.

    With ``include_suspend=True`` the clock keeps counting while the computer is
    asleep. This is exact only on Linux (``CLOCK_BOOTTIME``); on other systems it
    falls back to :func:`time.monotonic`.
    """

    def __init__(self, include_suspend: bool = False) -> None:
        super().__init__()
        self.include_suspend = include_suspend
        boottime: Optional[int] = getattr(time, "CLOCK_BOOTTIME", None)
        self._boottime = boottime if include_suspend and sys.platform.startswith("linux") else None
        if include_suspend:
            # Sleeps are measured with the monotonic clock, so wake up often
            # enough to notice time that passed while the machine was suspended.
            self.max_wait = 1.0

    def now(self) -> float:
        if self._boottime is not None:
            return time.clock_gettime(self._boottime)
        return time.monotonic()

    def __repr__(self) -> str:
        return f"SystemClock(include_suspend={self.include_suspend!r})"


class FakeClock(Clock):
    """A manual clock for tests. Time moves only when :meth:`advance` is called.

    No threads are started. ``advance()`` runs every due event and callback in
    order, in the calling thread, so tests are instant and deterministic.
    """

    fake = True

    def __init__(self, start: float = 0.0) -> None:
        super().__init__()
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        """Move time forward by ``seconds``, firing everything that becomes due.

        Returns the new time.
        """
        if seconds < 0:
            raise ValueError("cannot move a FakeClock backwards")
        self.scheduler.run_until(self._now + seconds)
        return self._now

    def sleep(self, seconds: float) -> None:
        """Alias for :meth:`advance`, handy as a drop-in for ``time.sleep``."""
        self.advance(seconds)

    def _set_time(self, value: float) -> None:
        self._now = value

    def __repr__(self) -> str:
        return f"FakeClock(now={self._now!r})"


_default_clock: Optional[SystemClock] = None
_default_lock = threading.Lock()


def default_clock() -> Clock:
    """Return the shared :class:`SystemClock` used when no clock is given."""
    global _default_clock
    if _default_clock is None:
        with _default_lock:
            if _default_clock is None:
                _default_clock = SystemClock()
    return _default_clock
