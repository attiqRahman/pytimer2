"""Stopwatch: measure running time with pause, resume and laps."""

from __future__ import annotations

import threading
from types import TracebackType
from typing import List, Optional, Type

from ._clock import Clock, default_clock
from ._format import format_time

__all__ = ["Stopwatch"]


class Stopwatch:
    """Counts up. Pausing freezes it; laps record split times.

    >>> with Stopwatch() as sw:
    ...     do_work()
    >>> print(sw)            # 00:01.234
    """

    def __init__(self, *, clock: Optional[Clock] = None, start: bool = False) -> None:
        self._clock = clock if clock is not None else default_clock()
        self._lock = threading.Lock()
        self._accum = 0.0
        self._seg_start = 0.0
        self._running = False
        self._lap_mark = 0.0
        self._laps: List[float] = []
        if start:
            self.start()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def elapsed(self) -> float:
        """Total running time in seconds."""
        with self._lock:
            return self._elapsed_locked()

    @property
    def laps(self) -> List[float]:
        """Durations of the recorded laps, in seconds."""
        with self._lock:
            return list(self._laps)

    def start(self) -> Stopwatch:
        """Start, or continue after :meth:`pause`/:meth:`stop`. Returns the stopwatch."""
        with self._lock:
            if not self._running:
                self._seg_start = self._clock.now()
                self._running = True
        return self

    def pause(self) -> bool:
        """Freeze the stopwatch. Returns False if it was not running."""
        with self._lock:
            if not self._running:
                return False
            self._accum = self._elapsed_locked()
            self._running = False
            return True

    def resume(self) -> bool:
        """Continue after a pause. Returns False if it was already running."""
        with self._lock:
            if self._running:
                return False
            self._seg_start = self._clock.now()
            self._running = True
            return True

    def stop(self) -> bool:
        """Same as :meth:`pause`; reads better at the end of a measurement."""
        return self.pause()

    def reset(self) -> Stopwatch:
        """Zero the time and laps and stop. Returns the stopwatch."""
        with self._lock:
            self._accum = 0.0
            self._running = False
            self._lap_mark = 0.0
            self._laps = []
        return self

    def restart(self) -> Stopwatch:
        """Reset and start again. Returns the stopwatch."""
        return self.reset().start()

    def lap(self) -> float:
        """Record a lap and return its duration in seconds."""
        with self._lock:
            now = self._elapsed_locked()
            lap = now - self._lap_mark
            self._lap_mark = now
            self._laps.append(lap)
            return lap

    def _elapsed_locked(self) -> float:
        if self._running:
            return self._accum + (self._clock.now() - self._seg_start)
        return self._accum

    def __enter__(self) -> Stopwatch:
        return self.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.stop()

    def __str__(self) -> str:
        return format_time(self.elapsed, ms=True)

    def __repr__(self) -> str:
        state = "running" if self._running else "stopped"
        return f"<Stopwatch {state} {self} laps={len(self._laps)}>"
