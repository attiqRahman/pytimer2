"""The scheduler: one heap, one scheduler thread and one callback thread per clock."""

from __future__ import annotations

import heapq
import itertools
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple, cast

if TYPE_CHECKING:
    from ._clock import Clock, FakeClock

__all__ = ["Handle", "Scheduler"]

log = logging.getLogger("pytimer2")

_Call = Tuple[Callable[..., Any], Tuple[Any, ...]]


class Handle:
    """A scheduled event. Call :meth:`cancel` (with the scheduler lock held) to drop it."""

    __slots__ = ("_sched", "cancelled", "fn", "seq", "when")

    def __init__(self, when: float, seq: int, fn: Callable[[], None], sched: Scheduler) -> None:
        self.when = when
        self.seq = seq
        self.fn = fn
        self.cancelled = False
        self._sched = sched

    def __lt__(self, other: Handle) -> bool:
        return (self.when, self.seq) < (other.when, other.seq)

    def cancel(self) -> None:
        if not self.cancelled:
            self.cancelled = True
            self._sched._note_cancelled()


class Scheduler:
    """Runs timed events for every timer that shares one clock.

    * ``lock`` is the single lock for the scheduler and all its timers.
    * Events (internal timer methods) run with the lock held, on the scheduler
      thread, and decide what happens next.
    * User callbacks never run with the lock held. They are queued with
      :meth:`emit` and run on the callback thread, in order.

    With a fake clock no threads are started. Events and callbacks run inside
    ``FakeClock.advance()`` and inside :meth:`after_call`.
    """

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.lock = threading.RLock()
        self.done = threading.Condition(self.lock)
        self._wake = threading.Condition(self.lock)
        self._heap: List[Handle] = []
        self._seq = itertools.count()
        self._cancelled = 0
        self._thread: Optional[threading.Thread] = None
        self._cb_thread: Optional[threading.Thread] = None
        self._queue: queue.SimpleQueue[_Call] = queue.SimpleQueue()
        self._pending: List[_Call] = []
        self._draining = False

    @property
    def fake(self) -> bool:
        return self.clock.fake

    # -- scheduling (lock held) -------------------------------------------

    def schedule(self, when: float, fn: Callable[[], None]) -> Handle:
        """Run ``fn`` (with the lock held) at clock time ``when``."""
        handle = Handle(when, next(self._seq), fn, self)
        heapq.heappush(self._heap, handle)
        if not self.fake:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="pytimer2-scheduler", daemon=True)
                self._thread.start()
            if self._heap[0] is handle:
                self._wake.notify()
        return handle

    def _note_cancelled(self) -> None:
        self._cancelled += 1
        if self._cancelled > 64 and self._cancelled * 2 > len(self._heap):
            self._heap = [h for h in self._heap if not h.cancelled]
            heapq.heapify(self._heap)
            self._cancelled = 0

    def _pop_due(self, limit: float) -> Optional[Handle]:
        heap = self._heap
        while heap:
            first = heap[0]
            if first.cancelled:
                heapq.heappop(heap)
                self._cancelled = max(0, self._cancelled - 1)
                continue
            if first.when <= limit:
                heapq.heappop(heap)
                first.cancelled = True  # spent; a later cancel() is a no-op
                return first
            return None
        return None

    def _call_event(self, handle: Handle) -> None:
        try:
            handle.fn()
        except Exception:  # pragma: no cover - internal events should not raise
            log.exception("pytimer2: internal timer event failed")

    def pending_events(self) -> int:
        """Number of live scheduled events (mainly for tests)."""
        with self.lock:
            return sum(1 for h in self._heap if not h.cancelled)

    # -- real-time thread ---------------------------------------------------

    def _run(self) -> None:
        with self.lock:
            while True:
                now = self.clock.now()
                handle = self._pop_due(now)
                if handle is not None:
                    self._call_event(handle)
                    continue
                timeout: Optional[float] = None
                if self._heap:
                    timeout = max(0.0, self._heap[0].when - now)
                cap = self.clock.max_wait
                if cap is not None:
                    timeout = cap if timeout is None else min(timeout, cap)
                self._wake.wait(timeout)

    # -- callbacks ------------------------------------------------------------

    def emit(self, fn: Callable[..., Any], *args: Any) -> None:
        """Queue a user callback. It runs later, never with the lock held."""
        if self.fake:
            self._pending.append((fn, args))
            return
        if self._cb_thread is None:
            with self.lock:
                if self._cb_thread is None:
                    self._cb_thread = threading.Thread(
                        target=self._run_callbacks, name="pytimer2-callbacks", daemon=True
                    )
                    self._cb_thread.start()
        self._queue.put((fn, args))

    def _run_callbacks(self) -> None:
        while True:
            fn, args = self._queue.get()
            _safe_call(fn, args)

    def in_callback_thread(self) -> bool:
        """True when called from this scheduler's callback thread."""
        return self._cb_thread is not None and threading.current_thread() is self._cb_thread

    def after_call(self) -> None:
        """Called at the end of public methods, outside the lock.

        With a fake clock this runs events that are already due and every
        queued callback. With a real clock it does nothing.
        """
        if self.fake:
            self.run_until(self.clock.now())

    # -- fake-clock driver --------------------------------------------------

    def run_until(self, target: float) -> None:
        """Fire every event up to ``target`` in order (fake clocks only)."""
        clock = cast("FakeClock", self.clock)
        while True:
            with self.lock:
                handle = self._pop_due(target)
                if handle is not None:
                    if handle.when > clock.now():
                        clock._set_time(handle.when)
                    self._call_event(handle)
            if handle is None:
                break
            self._drain()
        with self.lock:
            if target > clock.now():
                clock._set_time(target)
        self._drain()

    def _drain(self) -> None:
        if self._draining:
            return  # the outer drain loop will pick up new callbacks
        self._draining = True
        try:
            while self._pending:
                fn, args = self._pending.pop(0)
                _safe_call(fn, args)
        finally:
            self._draining = False


def _safe_call(fn: Callable[..., Any], args: Tuple[Any, ...]) -> None:
    try:
        fn(*args)
    except Exception:
        log.exception("pytimer2: callback %r raised", fn)
