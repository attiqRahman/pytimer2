"""pytimer2: pausable timers and deadlines for Python.

Pausable timers, countdowns, deadlines and stopwatches that work with threads
and asyncio, run on one shared scheduler thread, and can be tested instantly
with a fake clock. Pure Python, no dependencies.
"""

from __future__ import annotations

from ._clock import Clock, FakeClock, SystemClock, default_clock
from ._compat import PausableTimer
from ._deadline import Deadline, DeadlineExceeded
from ._format import format_time, parse_duration
from ._helpers import Debounced, TimerGroup, Watchdog, debounce
from ._stopwatch import Stopwatch
from ._timer import Timer, TimerError, TimerState, every

__version__ = "1.0.0"

__all__ = [
    "Clock",
    "Deadline",
    "DeadlineExceeded",
    "Debounced",
    "FakeClock",
    "PausableTimer",
    "Stopwatch",
    "SystemClock",
    "Timer",
    "TimerError",
    "TimerGroup",
    "TimerState",
    "Watchdog",
    "__version__",
    "debounce",
    "default_clock",
    "every",
    "format_time",
    "parse_duration",
]
