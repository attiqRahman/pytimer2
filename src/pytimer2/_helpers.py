"""Helpers built on Timer: TimerGroup, Watchdog and debounce."""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from types import TracebackType
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Tuple, Type

from ._clock import Clock
from ._format import Duration
from ._timer import Callback, Timer

__all__ = ["Debounced", "TimerGroup", "Watchdog", "debounce"]


class TimerGroup:
    """Control many timers at once (for example, pause everything in a game).

    >>> group = TimerGroup([Timer(5), Timer(10)])
    >>> group.start(); group.pause(); group.resume()
    """

    def __init__(self, timers: Iterable[Timer] = ()) -> None:
        self._lock = threading.Lock()
        self._timers: List[Timer] = list(timers)

    def add(self, timer: Timer) -> Timer:
        """Add a timer and return it."""
        with self._lock:
            self._timers.append(timer)
        return timer

    def remove(self, timer: Timer) -> bool:
        with self._lock:
            if timer in self._timers:
                self._timers.remove(timer)
                return True
            return False

    def _snapshot(self) -> List[Timer]:
        with self._lock:
            return list(self._timers)

    def __iter__(self) -> Iterator[Timer]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._timers)

    def __contains__(self, timer: object) -> bool:
        return timer in self._timers

    @property
    def active(self) -> bool:
        """True if any timer is running or paused."""
        return any(t.active for t in self._snapshot())

    def start(self) -> TimerGroup:
        """Start every timer that is not running or paused. Returns the group."""
        for t in self._snapshot():
            if not t.active:
                t.start()
        return self

    def pause(self) -> int:
        """Pause all running timers. Returns how many were paused."""
        return sum(t.pause() for t in self._snapshot())

    def resume(self) -> int:
        """Resume all paused timers. Returns how many were resumed."""
        return sum(t.resume() for t in self._snapshot())

    def stop(self) -> int:
        """Stop all active timers. Returns how many were stopped."""
        return sum(t.stop() for t in self._snapshot())

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until every timer is done. False if ``timeout`` ran out first."""
        end = None if timeout is None else time.monotonic() + timeout
        for t in self._snapshot():
            left = None if end is None else max(0.0, end - time.monotonic())
            if not t.wait(left):
                return False
        return True

    async def wait_async(self, timeout: Optional[float] = None) -> bool:
        """Await until every timer is done. False if ``timeout`` ran out first."""
        results = await asyncio.gather(*(t.wait_async(timeout) for t in self._snapshot()))
        return all(results)

    def __enter__(self) -> TimerGroup:
        return self.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.stop()


class Watchdog:
    """Calls ``on_timeout`` if :meth:`kick` is not called within ``timeout`` seconds.

    >>> dog = Watchdog(30, on_timeout=reconnect).start()
    >>> for message in stream:
    ...     dog.kick()

    After it fires, the next :meth:`kick` arms it again.
    """

    def __init__(
        self,
        timeout: Duration,
        on_timeout: Callback,
        *,
        name: Optional[str] = None,
        clock: Optional[Clock] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._timer = Timer(timeout, on_timeout, name=name or "watchdog", clock=clock, loop=loop)
        self._armed = False
        self._lock = threading.Lock()

    @property
    def timer(self) -> Timer:
        """The underlying timer."""
        return self._timer

    @property
    def remaining(self) -> float:
        return self._timer.remaining

    @property
    def fired(self) -> bool:
        """True if the watchdog timed out and has not been kicked since."""
        return self._timer.finished

    def start(self) -> Watchdog:
        """Arm the watchdog. Returns it."""
        with self._lock:
            self._armed = True
        self._timer.restart()
        return self

    def kick(self) -> None:
        """Report that all is well: start the countdown again. Ignored before start or after stop."""
        with self._lock:
            armed = self._armed
        if armed:
            self._timer.restart()

    feed = kick

    def pause(self) -> bool:
        return self._timer.pause()

    def resume(self) -> bool:
        return self._timer.resume()

    def stop(self) -> bool:
        """Disarm the watchdog. Returns False if it was not counting down."""
        with self._lock:
            self._armed = False
        return self._timer.stop()

    def __enter__(self) -> Watchdog:
        return self.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.stop()


class Debounced:
    """A function wrapped by :func:`debounce`."""

    def __init__(self, fn: Callable[..., Any], wait: Duration, clock: Optional[Clock]) -> None:
        self.fn = fn
        self._lock = threading.Lock()
        self._call: Optional[Tuple[Tuple[Any, ...], Dict[str, Any]]] = None
        self._timer = Timer(wait, self._fire, name=f"debounce({getattr(fn, '__name__', 'fn')})", clock=clock)
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self._call = (args, kwargs)
        self._timer.restart()

    @property
    def pending(self) -> bool:
        """True if a call is waiting to run."""
        return self._call is not None

    def cancel(self) -> bool:
        """Drop the waiting call. Returns False if nothing was waiting."""
        with self._lock:
            had, self._call = self._call is not None, None
        self._timer.stop()
        return had

    def flush(self) -> bool:
        """Run the waiting call now, in this thread. Returns False if nothing was waiting."""
        self._timer.stop()
        return self._fire()

    def _fire(self) -> bool:
        with self._lock:
            call, self._call = self._call, None
        if call is None:
            return False
        args, kwargs = call
        self.fn(*args, **kwargs)
        return True


def debounce(wait: Duration, *, clock: Optional[Clock] = None) -> Callable[[Callable[..., Any]], Debounced]:
    """Decorator: run the function only after ``wait`` seconds without new calls.

    >>> @debounce(0.5)
    ... def save(text): ...
    >>> save("a"); save("ab"); save("abc")   # only save("abc") runs, 0.5 s later

    The call runs on the pytimer2 callback thread with the latest arguments.
    The wrapper has ``.cancel()``, ``.flush()`` and ``.pending``.
    """

    def wrap(fn: Callable[..., Any]) -> Debounced:
        return Debounced(fn, wait, clock)

    return wrap
