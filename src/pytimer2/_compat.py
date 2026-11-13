"""PausableTimer: a drop-in replacement for threading.Timer that can pause."""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Iterable, Optional

from ._clock import Clock
from ._format import Duration
from ._timer import Timer

__all__ = ["PausableTimer"]


class PausableTimer:
    """Works like :class:`threading.Timer`, plus :meth:`pause` and :meth:`resume`.

    >>> t = PausableTimer(30.0, save_work, args=("draft",))
    >>> t.start()
    >>> t.pause(); t.resume(); t.cancel()

    Waiting does not use a thread of its own (it shares the pytimer2
    scheduler). When the time is up, ``function`` runs in its own new thread,
    like :class:`threading.Timer`. That thread is a daemon thread unless
    ``daemon=False`` is passed. With a FakeClock the function runs directly
    inside ``clock.advance()``.
    """

    def __init__(
        self,
        interval: Duration,
        function: Callable[..., Any],
        args: Optional[Iterable[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
        *,
        daemon: bool = True,
        name: Optional[str] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.function = function
        self.args = list(args) if args is not None else []
        self.kwargs = dict(kwargs) if kwargs is not None else {}
        self.daemon = daemon
        self.name = name or "PausableTimer"
        #: Set when the function has run or the timer was cancelled (as in threading.Timer).
        self.finished = threading.Event()
        self._done = threading.Event()
        self._started = False
        self._timer = Timer(interval, self._fire, name=self.name, clock=clock)
        self.interval = self._timer.duration

    @property
    def remaining(self) -> float:
        return self._timer.remaining

    @property
    def elapsed(self) -> float:
        return self._timer.elapsed

    @property
    def paused(self) -> bool:
        return self._timer.paused

    def start(self) -> None:
        """Start counting down. Can be called only once, like a thread."""
        if self._started:
            raise RuntimeError("threads can only be started once")
        self._started = True
        self._timer.start()

    def cancel(self) -> None:
        """Stop the timer if it has not fired yet."""
        self.finished.set()
        if self._timer.stop():
            self._done.set()

    def pause(self) -> bool:
        return self._timer.pause()

    def resume(self) -> bool:
        return self._timer.resume()

    def is_alive(self) -> bool:
        """True from :meth:`start` until the function returns or the timer is cancelled."""
        return self._started and not self._done.is_set()

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait until the function has returned or the timer was cancelled."""
        if not self._started:
            raise RuntimeError("cannot join thread before it is started")
        self._done.wait(timeout)

    def _fire(self) -> None:
        if self.finished.is_set():  # cancelled just before firing
            self._done.set()
            return
        if self._timer.clock.fake:
            self._run()
        else:
            threading.Thread(target=self._run, name=self.name, daemon=self.daemon).start()

    def _run(self) -> None:
        try:
            self.function(*self.args, **self.kwargs)
        finally:
            self.finished.set()
            self._done.set()

    def __repr__(self) -> str:
        return f"<PausableTimer {self.name!r} {self._timer.state.value} remaining={self.remaining:.3f}s>"
