"""Deadline: a pausable time budget for code that should not run too long."""

from __future__ import annotations

import asyncio
import sys
import threading
from types import TracebackType
from typing import Any, Optional, Type

from ._clock import Clock, default_clock
from ._format import Duration, format_time, parse_duration
from ._timer import TimerError

__all__ = ["Deadline", "DeadlineExceeded"]


class DeadlineExceeded(TimeoutError):
    """Raised by :meth:`Deadline.check` (and ``async with Deadline``) when time is up."""


class Deadline:
    """A time budget that can be paused, resumed and extended.

    >>> deadline = Deadline(10)
    >>> for item in items:
    ...     deadline.check()                      # raises DeadlineExceeded when time is up
    ...     fetch(item, timeout=deadline.timeout(cap=2))

    Pause it while waiting for something that should not count (user input,
    a rate limiter). It never interrupts code by itself: call :meth:`check`, or
    pass :meth:`timeout` to blocking calls. On Python 3.11+ ``async with
    Deadline(5):`` cancels the block when the budget runs out, and pausing the
    deadline also pauses that cancellation.
    """

    def __init__(
        self, seconds: Duration, *, clock: Optional[Clock] = None, name: Optional[str] = None
    ) -> None:
        self._clock = clock if clock is not None else default_clock()
        self._budget = parse_duration(seconds)
        self.name = name
        self._lock = threading.Lock()
        self._accum = 0.0
        self._seg_start = self._clock.now()
        self._paused = False
        self._async_cm: Any = None
        self._async_loop: Optional[asyncio.AbstractEventLoop] = None

    # -- state ------------------------------------------------------------------

    @property
    def budget(self) -> float:
        """Total seconds allowed, including extensions."""
        return self._budget

    @property
    def elapsed(self) -> float:
        """Seconds used so far (paused time does not count)."""
        with self._lock:
            return self._elapsed_locked()

    @property
    def remaining(self) -> float:
        """Seconds left, never negative."""
        with self._lock:
            return max(0.0, self._budget - self._elapsed_locked())

    @property
    def expired(self) -> bool:
        return self.remaining <= 0.0

    @property
    def paused(self) -> bool:
        return self._paused

    def timeout(self, cap: Optional[float] = None) -> float:
        """Seconds left, limited to ``cap``. Pass this as the timeout of a blocking call."""
        left = self.remaining
        return left if cap is None else min(left, cap)

    def check(self, what: Optional[str] = None) -> None:
        """Raise :class:`DeadlineExceeded` if the budget is used up."""
        if self.expired:
            label = f" {self.name!r}" if self.name else ""
            during = f" during {what}" if what else ""
            raise DeadlineExceeded(f"deadline{label} of {self._budget:g}s exceeded{during}")

    # -- control ----------------------------------------------------------------

    def pause(self) -> bool:
        """Stop the budget from being used. Returns False if already paused."""
        with self._lock:
            if self._paused:
                return False
            self._accum = self._elapsed_locked()
            self._paused = True
        self._reschedule_async()
        return True

    def resume(self) -> bool:
        """Continue using the budget. Returns False if it was not paused."""
        with self._lock:
            if not self._paused:
                return False
            self._seg_start = self._clock.now()
            self._paused = False
        self._reschedule_async()
        return True

    def extend(self, seconds: Duration) -> float:
        """Add ``seconds`` to the budget. Returns the new remaining time."""
        with self._lock:
            self._budget += parse_duration(seconds)
        self._reschedule_async()
        return self.remaining

    def _elapsed_locked(self) -> float:
        if self._paused:
            return self._accum
        return self._accum + (self._clock.now() - self._seg_start)

    # -- context managers -------------------------------------------------------

    def __enter__(self) -> Deadline:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        return None

    async def __aenter__(self) -> Deadline:
        if sys.version_info < (3, 11):
            raise TimerError("'async with Deadline(...)' needs Python 3.11 or newer (asyncio.timeout_at)")
        if self._async_cm is not None:
            raise TimerError("this Deadline is already used by an 'async with' block")
        loop = asyncio.get_running_loop()
        self._async_loop = loop
        self._async_cm = asyncio.timeout_at(self._loop_deadline(loop))
        await self._async_cm.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        cm, self._async_cm = self._async_cm, None
        self._async_loop = None
        try:
            await cm.__aexit__(exc_type, exc, tb)
        except TimeoutError as err:
            if isinstance(err, DeadlineExceeded):
                raise
            raise DeadlineExceeded(f"deadline of {self._budget:g}s exceeded") from err

    def _loop_deadline(self, loop: asyncio.AbstractEventLoop) -> Optional[float]:
        if self._paused:
            return None
        return loop.time() + self.remaining

    def _reschedule_async(self) -> None:
        cm, loop = self._async_cm, self._async_loop
        if cm is None or loop is None:
            return
        when = self._loop_deadline(loop)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            cm.reschedule(when)
        else:
            loop.call_soon_threadsafe(cm.reschedule, when)

    def __repr__(self) -> str:
        state = "paused" if self._paused else ("expired" if self.expired else "running")
        return f"<Deadline {state} {format_time(self.remaining, ms=True)} left of {self._budget:g}s>"
