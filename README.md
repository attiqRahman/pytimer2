# pytimer2

**pytimer2: pausable timers and deadlines for Python.**

[![PyPI](https://img.shields.io/pypi/v/pytimer2)](https://pypi.org/project/pytimer2/)
[![Python](https://img.shields.io/pypi/pyversions/pytimer2)](https://pypi.org/project/pytimer2/)
[![CI](https://github.com/attiqRahman/pytimer2/actions/workflows/ci.yml/badge.svg)](https://github.com/attiqRahman/pytimer2/actions/workflows/ci.yml)
[![Downloads](https://static.pepy.tech/badge/pytimer2)](https://pepy.tech/project/pytimer2)

Countdowns you can pause and resume, time budgets for slow code, stopwatches,
watchdogs and debouncing. Works with threads and asyncio, runs any number of
timers on one scheduler thread, and can be tested instantly with a fake clock.

- Pure Python, **no dependencies**, Python 3.9+, fully typed.
- **Pause, resume, extend, restart** any timer. Paused time never counts.
- **Ticks** (`on_tick` every second, for example) and **repeats** without drift.
- **One scheduler thread** for all timers, not one thread per timer.
- **asyncio**: `async def` callbacks, `await timer`, `async with Deadline(5)`.
- **Instant tests**: `FakeClock().advance(60)` fires a minute of timers in microseconds.
- **Save and restore** timers across restarts with `to_dict()` / `from_dict()`.
- A small **CLI**: `pytimer2 25m`.

```bash
pip install pytimer2
```

## Quick start

```python
from pytimer2 import Timer

def done():
    print("Time is up!")

timer = Timer(10, on_finish=done).start()   # starts counting, returns at once
timer.pause()                               # freeze
print(timer.remaining)                      # 10.0 (or very close)
timer.resume()                              # carry on where it stopped
timer.wait()                                # block until it finishes (optional)
```

Durations can be numbers of seconds or strings: `90`, `"2.5s"`, `"500ms"`,
`"25m"`, `"1h30m"`, `"1:30"`, `"1:02:03"`.

## A quiz timer with ticks

```python
from pytimer2 import Timer, format_time

def show(remaining):
    print("Time left:", format_time(remaining, ceil=True))

def time_up():
    print("Pencils down!")

quiz = Timer("2m", on_finish=time_up, tick=1, on_tick=show).start()

# The student asks a question, so stop the clock for a moment:
quiz.pause()
quiz.resume()
quiz.extend(30)    # give 30 more seconds
```

`on_tick(remaining)` is called every `tick` seconds of running time, strictly
before the end. `on_finish()` covers the end.

## Timer reference

```python
Timer(duration=0, on_finish=None, *, tick=None, on_tick=None, repeat=False,
      on_pause=None, on_resume=None, on_stop=None, name=None, clock=None, loop=None)
```

| Method / property | What it does |
| --- | --- |
| `start(duration=None)` | Start (again from zero if finished or stopped). Returns the timer. |
| `pause()`, `resume()`, `toggle()` | Return `True` if the state changed. |
| `stop()` (alias `cancel()`) | Stop for good, calls `on_stop`. Returns `True` if it was active. |
| `restart(duration=None)` | Start again from zero in any state. Returns the timer. |
| `reset(duration=None)` | Back to idle, no callbacks. Returns the timer. |
| `extend(seconds)` | Add time to the current cycle only. A negative number shortens it; if that is less than the time already run, the cycle ends now. The next repeat, `start()`, `restart()` and `reset()` use the normal `duration` again. Returns `False` if the timer is not running or paused. |
| `wait(timeout=None)` | Block until finished or stopped. `False` on timeout. |
| `await timer`, `await timer.wait_async(timeout)` | The asyncio versions. |
| `elapsed`, `remaining`, `progress` | Seconds run, seconds left, 0.0 to 1.0. |
| `state`, `running`, `paused`, `finished`, `active` | `TimerState.IDLE / RUNNING / PAUSED / FINISHED / STOPPED`. |
| `cycles` | Completed cycles (for repeating timers). |
| `to_dict()`, `Timer.from_dict(data, **callbacks)` | Save and restore. |

`with Timer(5, on_finish=f):` starts the timer and stops it when the block ends.

## Repeating timers

```python
from pytimer2 import Timer, every

heartbeat = every(5, lambda: print("still alive"))   # every 5 s until stopped
three = Timer(1, lambda: print("beep"), repeat=3).start()   # three cycles in total

heartbeat.pause()
heartbeat.resume()
heartbeat.stop()
```

Repeats are scheduled from the ideal end time of the previous cycle, so they do
not drift. If a whole cycle is missed (for example the machine was busy), the
next one starts from now instead of firing a burst of catch-up calls.

## Deadlines: a pausable time budget

```python
from pytimer2 import Deadline, DeadlineExceeded

deadline = Deadline("30s", name="sync")
for page in range(1, 100):
    deadline.check(f"page {page}")              # raises DeadlineExceeded when time is up
    fetch(page, timeout=deadline.timeout(cap=5))  # never wait longer than what is left

    if needs_user_input(page):
        deadline.pause()                        # waiting for a person does not count
        ask_user()
        deadline.resume()
```

`DeadlineExceeded` is a subclass of `TimeoutError`. A deadline never interrupts
your code by itself; call `check()` or pass `timeout()` to blocking calls.

On Python 3.11+ it can also cancel an async block. Pausing the deadline pauses
the cancellation too:

```python
async with Deadline(5) as d:
    await download()
```

## Stopwatch

```python
from pytimer2 import Stopwatch

with Stopwatch() as sw:
    step_one()
    sw.lap()
    step_two()
    sw.lap()
print(sw, sw.laps)        # 00:01.234 [0.51, 0.72]
```

Also `start()`, `pause()`, `resume()`, `stop()`, `reset()`, `restart()`, `elapsed`.

## Drop-in for threading.Timer

`PausableTimer` has the same constructor and methods as `threading.Timer`
(`start`, `cancel`, `join`, `is_alive`, `finished`), plus `pause()`, `resume()`
and `remaining`:

```python
from pytimer2 import PausableTimer

t = PausableTimer(30.0, autosave, args=("draft.txt",))
t.start()
t.pause()
t.resume()
```

Like `threading.Timer`, the function runs in its own thread when the time is up.

## Groups, watchdogs and debounce

```python
from pytimer2 import TimerGroup, Watchdog, debounce, Timer

# Pause every timer in a game at once
group = TimerGroup([Timer(30), Timer(60)]).start()
group.pause()        # returns how many were paused
group.resume()

# Call reconnect() if no message arrives for 10 seconds
dog = Watchdog(10, on_timeout=reconnect).start()
for message in stream:
    dog.kick()

# Save only after the user stops typing for half a second
@debounce(0.5)
def save(text):
    write_to_disk(text)
```

The debounced function also has `.cancel()`, `.flush()` and `.pending`.

## asyncio

Callbacks may be `async def`. They run in the event loop that was running when
`start()` was called (or pass `loop=`).

```python
import asyncio
from pytimer2 import Timer

async def main():
    async def ring():
        print("ring")

    timer = Timer(2, on_finish=ring).start()
    timer.pause()
    timer.resume()
    await timer                    # or: await timer.wait_async(timeout=5)

asyncio.run(main())
```

## Testing with a fake clock

Pass a `FakeClock` and time only moves when you say so. No threads are
started; `advance()` fires everything in order, in your test's thread.

```python
from pytimer2 import FakeClock, Timer

def test_quiz_ends_after_two_minutes():
    clock = FakeClock()
    ended = []
    quiz = Timer("2m", on_finish=lambda: ended.append(True), clock=clock).start()

    clock.advance(60)
    quiz.pause()
    clock.advance(3600)        # an hour passes while paused
    quiz.resume()
    clock.advance(60)

    assert ended == [True]
```

`Timer.wait()` raises `TimerError` with a `FakeClock`, because it would block forever.

## Save and restore

```python
import json
from pytimer2 import Timer

timer = Timer("25m", name="focus").start()
saved = json.dumps(timer.to_dict())

# Later, maybe in another process:
timer = Timer.from_dict(json.loads(saved), on_finish=notify)
```

A running timer keeps running, and the wall-clock time since it was saved
counts as elapsed (`catch_up=True`). If it should already have ended,
`on_finish` is called once right away.

## Formatting helpers

```python
from pytimer2 import format_time, parse_duration

format_time(65)                  # '01:05'
format_time(3725)                # '1:02:05'
format_time(65.25, ms=True)      # '01:05.250'
format_time(0.4, ceil=True)      # '00:01' (countdown style)
parse_duration("1h30m")          # 5400.0
```

## Command line

```bash
pytimer2 25m               # Pomodoro
pytimer2 1:30 -l Tea       # Tea: 01:30 ... Tea: 00:00 time is up!
python -m pytimer2 90 -q   # only print the final line
```

Press Ctrl+C to stop.

## How it works

- Every clock has **one scheduler**: a heap of events, one daemon thread that
  decides when things happen and one daemon thread that runs your callbacks.
  10,000 timers still use two threads.
- Callbacks never run while the scheduler's lock is held, so a callback can
  safely pause, stop or restart any timer.
- Time comes only from a clock (`time.monotonic()` by default), so changing the
  system time does not affect timers.
- All threads are daemon threads: a running timer never keeps your program alive.

## Known limits

- Python cannot kill a running thread, so nothing here can interrupt blocking
  code. Use `Deadline.check()` or pass `Deadline.timeout()` to blocking calls.
- Callbacks share one thread. A slow, CPU-heavy callback delays the others;
  hand heavy work to your own thread or executor.
- `SystemClock(include_suspend=True)` keeps counting while the computer sleeps
  only on Linux (`CLOCK_BOOTTIME`). Elsewhere it behaves like `time.monotonic()`.
- `async with Deadline(...)` needs Python 3.11+.

## Upgrading from 0.x

The old methods still work and show a `DeprecationWarning`. They will be
removed in 2.0.

| 0.x | 1.0 |
| --- | --- |
| `Timer()` then `start_countdown(500)` | `Timer(500).start()` |
| `get_countdown()` | `remaining` (a float; `get_countdown()` rounds up to whole seconds) |
| `pause_countdown()` | `pause()` |
| `resume_countdown()` | `resume()` |
| `stop_countdown()` | `stop()` |

The 1.0 timer no longer starts a thread per countdown and no longer sleeps in
one-second steps, so it is exact to the millisecond.

## License

MIT. See [LICENSE](LICENSE).
