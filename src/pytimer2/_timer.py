"""Timer: a pausable countdown with ticks, repeats, callbacks and async support."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
import time
import warnings
from enum import Enum
from types import TracebackType
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Type, Union

from ._clock import Clock, default_clock
from ._format import Duration, format_time, parse_duration
from ._scheduler import Handle

__all__ = ["Callback", "Timer", "TimerError", "TimerState", "every"]

log = logging.getLogger("pytimer2")

#: A callback may be a plain function or an ``async def`` function.
Callback = Callable[..., Any]

_EPS = 1e-9


class TimerError(RuntimeError):
    """Raised when a timer is used in a way its state does not allow."""


class TimerState(Enum):
    """The life cycle of a :class:`Timer`."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"
    STOPPED = "stopped"


_ACTIVE = (TimerState.RUNNING, TimerState.PAUSED)


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _run_coroutine(loop: asyncio.AbstractEventLoop, fn: Callback, args: Tuple[Any, ...]) -> None:
    if loop.is_closed():
        log.warning("pytimer2: event loop is closed, async callback %r skipped", fn)
        return
    future = asyncio.run_coroutine_threadsafe(fn(*args), loop)

    def report(fut: Any) -> None:
        if not fut.cancelled() and fut.exception() is not None:
            log.error("pytimer2: async callback %r raised", fn, exc_info=fut.exception())

    future.add_done_callback(report)


def _resolve(fut: asyncio.Future[bool], value: bool) -> None:
    if not fut.done():
        fut.set_result(value)


def _deprecated(old: str, new: str) -> None:
    warnings.warn(
        f"Timer.{old}() is deprecated and will be removed in pytimer2 2.0; use Timer.{new}() instead",
        DeprecationWarning,
        stacklevel=3,
    )


class Timer:
    """A pausable countdown.

    >>> t = Timer(5, on_finish=lambda: print("done")).start()
    >>> t.pause(); t.resume()

    Args:
        duration: seconds, or a string such as ``"25m"`` or ``"1:30"``.
        on_finish: called when a cycle ends (once, or every cycle when repeating).
        tick: if set, ``on_tick(remaining)`` is called every ``tick`` seconds of
            running time, strictly before the end.
        repeat: ``False`` runs once, ``True`` repeats until stopped, an integer
            ``n`` runs ``n`` cycles in total.
        on_pause, on_resume, on_stop: called after the matching state change.
        name: a label shown in ``repr()``.
        clock: the clock to use. Defaults to a shared :class:`SystemClock`;
            pass a :class:`FakeClock` in tests.
        loop: event loop for ``async def`` callbacks. Defaults to the loop
            running when :meth:`start` is called.

    Callbacks run on one shared callback thread (or in the event loop for
    ``async def`` callbacks), never while internal locks are held, so they may
    call any timer method.
    """

    def __init__(
        self,
        duration: Duration = 0,
        on_finish: Optional[Callback] = None,
        *,
        tick: Optional[float] = None,
        on_tick: Optional[Callback] = None,
        repeat: Union[bool, int] = False,
        on_pause: Optional[Callback] = None,
        on_resume: Optional[Callback] = None,
        on_stop: Optional[Callback] = None,
        name: Optional[str] = None,
        clock: Optional[Clock] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self._clock = clock if clock is not None else default_clock()
        self._sched = self._clock.scheduler
        self._lock = self._sched.lock
        self._duration = parse_duration(duration)
        if tick is not None and not tick > 0:
            raise ValueError("tick must be a positive number of seconds")
        self._tick = float(tick) if tick is not None else None
        self._repeat = _repeat_count(repeat)
        self.on_finish = on_finish
        self.on_tick = on_tick
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_stop = on_stop
        self.name = name
        self._user_loop = loop
        self._loop: Optional[asyncio.AbstractEventLoop] = loop

        self._state = TimerState.IDLE
        self._accum = 0.0  # running time in this cycle before _seg_start
        self._seg_start = 0.0  # clock time the current running segment began
        self._handle: Optional[Handle] = None
        self._ticks_done = 0
        self._cycles = 0
        self._gen = 0  # bumped on every (re)start
        self._done_gen = 0  # == _gen once waiters may be released
        self._waiters: List[Tuple[asyncio.AbstractEventLoop, asyncio.Future[bool]]] = []

    # -- properties ---------------------------------------------------------

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def state(self) -> TimerState:
        return self._state

    @property
    def duration(self) -> float:
        """Length of one cycle in seconds."""
        return self._duration

    @property
    def tick(self) -> Optional[float]:
        return self._tick

    @property
    def repeat(self) -> Union[bool, int]:
        """``True`` for endless repeats, otherwise the total number of cycles."""
        return True if self._repeat is None else self._repeat

    @property
    def elapsed(self) -> float:
        """Running time in the current cycle (pauses do not count)."""
        with self._lock:
            return self._elapsed_locked()

    @property
    def remaining(self) -> float:
        """Seconds left in the current cycle."""
        with self._lock:
            return max(0.0, self._duration - self._elapsed_locked())

    @property
    def progress(self) -> float:
        """Fraction of the current cycle done, from 0.0 to 1.0."""
        with self._lock:
            if self._duration <= 0:
                return 1.0 if self._state is not TimerState.IDLE else 0.0
            return min(1.0, self._elapsed_locked() / self._duration)

    @property
    def cycles(self) -> int:
        """Number of cycles completed since the last start."""
        return self._cycles

    @property
    def running(self) -> bool:
        return self._state is TimerState.RUNNING

    @property
    def paused(self) -> bool:
        return self._state is TimerState.PAUSED

    @property
    def finished(self) -> bool:
        return self._state is TimerState.FINISHED

    @property
    def active(self) -> bool:
        """True while running or paused."""
        return self._state in _ACTIVE

    # -- public control -----------------------------------------------------

    def start(self, duration: Optional[Duration] = None) -> Timer:
        """Start the timer (optionally with a new duration). Returns the timer.

        A finished or stopped timer starts again from zero. Raises
        :class:`TimerError` if it is already running or paused.
        """
        with self._lock:
            if self._state in _ACTIVE:
                raise TimerError(f"timer is already {self._state.value}; use restart() or resume()")
            self._prepare(duration)
            self._begin_locked()
        self._sched.after_call()
        return self

    def restart(self, duration: Optional[Duration] = None) -> Timer:
        """Start again from zero in any state, without calling ``on_stop``. Returns the timer."""
        with self._lock:
            self._prepare(duration)
            self._begin_locked()
        self._sched.after_call()
        return self

    def reset(self, duration: Optional[Duration] = None) -> Timer:
        """Go back to IDLE with zero elapsed time, without callbacks. Returns the timer."""
        with self._lock:
            if duration is not None:
                self._duration = parse_duration(duration)
            self._cancel_handle()
            self._state = TimerState.IDLE
            self._accum = 0.0
            self._ticks_done = 0
            self._cycles = 0
            self._gen += 1
            self._signal_done_locked()
        self._sched.after_call()
        return self

    def pause(self) -> bool:
        """Pause a running timer. Returns False if it was not running."""
        with self._lock:
            if self._state is not TimerState.RUNNING:
                return False
            self._accum = self._elapsed_locked()
            self._cancel_handle()
            self._state = TimerState.PAUSED
            self._emit(self.on_pause)
        self._sched.after_call()
        return True

    def resume(self) -> bool:
        """Resume a paused timer. Returns False if it was not paused."""
        with self._lock:
            if self._state is not TimerState.PAUSED:
                return False
            self._seg_start = self._clock.now()
            self._state = TimerState.RUNNING
            self._schedule_next_locked()
            self._emit(self.on_resume)
        self._sched.after_call()
        return True

    def toggle(self) -> bool:
        """Pause if running, resume if paused. Returns True if the state changed."""
        with self._lock:
            paused = self._state is TimerState.PAUSED
        return self.resume() if paused else self.pause()

    def stop(self) -> bool:
        """Stop a running or paused timer for good. Returns False if it was not active."""
        with self._lock:
            if self._state not in _ACTIVE:
                return False
            self._accum = self._elapsed_locked()
            self._cancel_handle()
            self._state = TimerState.STOPPED
            self._emit(self.on_stop)
            self._signal_done_locked()
        self._sched.after_call()
        return True

    cancel = stop

    def extend(self, seconds: Duration) -> bool:
        """Add time to the current cycle (a negative number shortens it).

        Returns False if the timer is not running or paused. If the new length
        is already used up, the cycle ends right away.
        """
        delta = _signed_seconds(seconds)
        with self._lock:
            if self._state not in _ACTIVE:
                return False
            self._duration = max(0.0, self._duration + delta)
            if self._state is TimerState.RUNNING:
                self._schedule_next_locked()
        self._sched.after_call()
        return True

    # -- waiting --------------------------------------------------------------

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the timer finishes or is stopped.

        Returns True when it is done, False if ``timeout`` ran out first. A
        repeating timer is done only when stopped or out of cycles. Raises
        :class:`TimerError` with a FakeClock or when called from a callback.
        """
        if self._clock.fake:
            raise TimerError("wait() would block forever with a FakeClock; call clock.advance() instead")
        if self._sched.in_callback_thread():
            raise TimerError("wait() cannot be called from a timer callback")
        with self._lock:
            return self._sched.done.wait_for(lambda: self._done_gen == self._gen, timeout)

    async def wait_async(self, timeout: Optional[float] = None) -> bool:
        """Await until the timer finishes or is stopped. False if ``timeout`` ran out."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._done_gen == self._gen:
                return True
            fut: asyncio.Future[bool] = loop.create_future()
            entry = (loop, fut)
            self._waiters.append(entry)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except asyncio.TimeoutError:
            with self._lock:
                if entry in self._waiters:
                    self._waiters.remove(entry)
            return False

    def __await__(self) -> Generator[Any, None, Timer]:
        return self._await_self().__await__()

    async def _await_self(self) -> Timer:
        await self.wait_async()
        return self

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> Timer:
        with self._lock:
            idle = self._state not in _ACTIVE
        if idle:
            self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.stop()

    # -- persistence ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """Save the timer's state as a JSON-friendly dict (callbacks are not saved)."""
        with self._lock:
            return {
                "version": 1,
                "name": self.name,
                "duration": self._duration,
                "elapsed": self._elapsed_locked(),
                "state": self._state.value,
                "tick": self._tick,
                "repeat": self.repeat,
                "cycles": self._cycles,
                "saved_at": time.time(),
            }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        *,
        catch_up: bool = True,
        clock: Optional[Clock] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        **callbacks: Any,
    ) -> Timer:
        """Rebuild a timer saved with :meth:`to_dict`, possibly in another process.

        A running timer keeps running. With ``catch_up=True`` the wall-clock
        time since saving counts as elapsed, so a timer that should have ended
        finishes right away (``on_finish`` is called once). Whole cycles missed
        by a repeating timer are counted but their callbacks are not called.
        Pass callbacks as keyword arguments (``on_finish=...``).
        """
        timer = cls(
            float(data["duration"]),
            tick=data.get("tick"),
            repeat=data.get("repeat", False),
            name=data.get("name"),
            clock=clock,
            loop=loop,
            **callbacks,
        )
        state = TimerState(data.get("state", "idle"))
        elapsed = max(0.0, float(data.get("elapsed", 0.0)))
        cycles = int(data.get("cycles", 0))
        if state is TimerState.RUNNING and catch_up and "saved_at" in data:
            elapsed += max(0.0, time.time() - float(data["saved_at"]))
        with timer._lock:
            dur = timer._duration
            if state in _ACTIVE and timer._repeats_left(cycles) and dur > 0 and elapsed >= dur:
                # Skip whole missed cycles, but leave the last one so it can finish normally.
                missed = int(elapsed // dur)
                if timer._repeat is not None:
                    missed = min(missed, timer._repeat - cycles - 1)
                cycles += missed
                elapsed -= missed * dur
            if state is TimerState.RUNNING:
                timer._begin_locked(accum=elapsed, cycles=cycles)
            else:
                timer._accum = min(elapsed, dur) if state is not TimerState.PAUSED else elapsed
                timer._cycles = cycles
                timer._state = state
                if state is TimerState.PAUSED:
                    timer._gen += 1
                    timer._ticks_done = timer._ticks_before(elapsed)
        timer._sched.after_call()
        return timer

    # -- legacy 0.x API -----------------------------------------------------

    def start_countdown(self, duration: float = 500) -> None:
        """Deprecated: use :meth:`start` (or :meth:`restart`)."""
        _deprecated("start_countdown", "start")
        self.restart(duration)

    def get_countdown(self) -> int:
        """Deprecated: use :attr:`remaining`. Whole seconds left, rounded up; 0 after stop."""
        _deprecated("get_countdown", "remaining")
        with self._lock:
            if self._state is TimerState.STOPPED:
                return 0
            left = max(0.0, self._duration - self._elapsed_locked())
        return math.ceil(left - _EPS)

    def pause_countdown(self) -> None:
        """Deprecated: use :meth:`pause`."""
        _deprecated("pause_countdown", "pause")
        self.pause()

    def resume_countdown(self) -> None:
        """Deprecated: use :meth:`resume`."""
        _deprecated("resume_countdown", "resume")
        self.resume()

    def stop_countdown(self) -> None:
        """Deprecated: use :meth:`stop`."""
        _deprecated("stop_countdown", "stop")
        self.stop()

    # -- internals (lock held) ----------------------------------------------

    def _prepare(self, duration: Optional[Duration]) -> None:
        if duration is not None:
            self._duration = parse_duration(duration)
        if self._duration <= 0 and self._repeat != 1:
            raise ValueError("a repeating timer needs a duration greater than zero")

    def _begin_locked(self, accum: float = 0.0, cycles: int = 0) -> None:
        self._loop = self._user_loop or _running_loop()
        self._cancel_handle()
        self._gen += 1
        self._accum = accum
        self._cycles = cycles
        self._ticks_done = self._ticks_before(accum)
        self._seg_start = self._clock.now()
        self._state = TimerState.RUNNING
        self._schedule_next_locked()

    def _ticks_before(self, elapsed: float) -> int:
        if self._tick is None or elapsed <= 0:
            return 0
        return math.floor(elapsed / self._tick + _EPS)

    def _repeats_left(self, cycles: int) -> bool:
        return self._repeat is None or cycles < self._repeat

    def _elapsed_locked(self) -> float:
        if self._state is TimerState.RUNNING:
            return min(self._duration, self._accum + (self._clock.now() - self._seg_start))
        return self._accum

    def _cancel_handle(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None

    def _next_point(self) -> Tuple[float, bool]:
        if self._tick is not None:
            point = (self._ticks_done + 1) * self._tick
            if point < self._duration - _EPS:
                return point, False
        return self._duration, True

    def _schedule_next_locked(self) -> None:
        self._cancel_handle()
        if self._state is not TimerState.RUNNING:
            return
        point, is_end = self._next_point()
        when = self._seg_start + (point - self._accum)
        self._handle = self._sched.schedule(when, lambda: self._on_event(point, is_end))

    def _on_event(self, point: float, is_end: bool) -> None:
        if self._state is not TimerState.RUNNING:
            return
        self._handle = None
        if not is_end:
            self._ticks_done += 1
            self._emit(self.on_tick, max(0.0, self._duration - point))
            self._schedule_next_locked()
            return
        self._cycles += 1
        ideal_end = self._seg_start + (self._duration - self._accum)
        self._emit(self.on_finish)
        if self._repeats_left(self._cycles):
            now = self._clock.now()
            # Restart from the ideal end time so repeats do not drift, unless
            # a whole cycle was missed (then start from now).
            self._seg_start = ideal_end if now - ideal_end < self._duration else now
            self._accum = 0.0
            self._ticks_done = 0
            self._schedule_next_locked()
        else:
            self._accum = self._duration
            self._state = TimerState.FINISHED
            self._signal_done_locked()

    def _emit(self, fn: Optional[Callback], *args: Any) -> None:
        if fn is None:
            return
        if inspect.iscoroutinefunction(fn):
            if self._loop is None:
                log.warning("pytimer2: async callback %r needs an event loop (pass loop=)", fn)
                return
            self._sched.emit(_run_coroutine, self._loop, fn, args)
        else:
            self._sched.emit(fn, *args)

    def _signal_done_locked(self) -> None:
        # Released through the callback queue so waiters wake after on_finish/on_stop ran.
        self._sched.emit(self._notify_done, self._gen)

    def _notify_done(self, gen: int) -> None:
        with self._lock:
            if gen != self._gen:
                return
            self._done_gen = gen
            self._sched.done.notify_all()
            waiters, self._waiters = self._waiters, []
        for loop, fut in waiters:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(_resolve, fut, True)
            except RuntimeError:  # pragma: no cover - loop closed in between
                pass

    def __repr__(self) -> str:
        with self._lock:
            left = max(0.0, self._duration - self._elapsed_locked())
            state = self._state.value
        label = f" {self.name!r}" if self.name else ""
        return f"<Timer{label} {state} {format_time(left, ms=True, ceil=True)} left of {self._duration:g}s>"


def _signed_seconds(value: Duration) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip().startswith("-"):
        return -parse_duration(value.strip()[1:])
    return parse_duration(value)


def _repeat_count(repeat: Union[bool, int]) -> Optional[int]:
    if repeat is True:
        return None
    if repeat is False:
        return 1
    if not isinstance(repeat, int) or repeat < 1:
        raise ValueError("repeat must be True, False or a positive number of cycles")
    return repeat


def every(
    interval: Duration,
    fn: Callback,
    *,
    tick: Optional[float] = None,
    on_tick: Optional[Callback] = None,
    name: Optional[str] = None,
    clock: Optional[Clock] = None,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> Timer:
    """Call ``fn`` every ``interval`` seconds until the returned timer is stopped.

    The timer is already started. It can be paused and resumed like any other.
    """
    return Timer(
        interval, fn, repeat=True, tick=tick, on_tick=on_tick, name=name, clock=clock, loop=loop
    ).start()
