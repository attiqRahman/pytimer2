"""Formatting and parsing of durations."""

from __future__ import annotations

import math
import re
from datetime import timedelta
from typing import Union

__all__ = ["Duration", "format_time", "parse_duration"]

#: Anything that can be turned into a number of seconds.
Duration = Union[int, float, str, timedelta]

_UNITS = {
    "ms": 0.001,
    "msec": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
    "d": 86400.0,
    "day": 86400.0,
    "days": 86400.0,
}
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)"
_PART = re.compile(rf"\s*({_NUMBER})\s*([a-z]+)\s*,?")
_CLOCK = re.compile(rf"^(?:(\d+):)?(\d+):({_NUMBER})$")


def parse_duration(value: Duration) -> float:
    """Convert ``value`` to seconds.

    Accepts numbers (seconds), :class:`datetime.timedelta`, and strings such as
    ``"90"``, ``"2.5s"``, ``"500ms"``, ``"25m"``, ``"1h30m"``, ``"1 hour 5 minutes"``,
    ``"1:30"`` (minutes:seconds) and ``"1:02:03"`` (hours:minutes:seconds).

    Raises :class:`ValueError` for negative or unreadable values.
    """
    if isinstance(value, bool):
        raise TypeError("a bool is not a duration")
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
    elif isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        seconds = _parse_text(value)
    else:
        raise TypeError(f"cannot use {type(value).__name__} as a duration")
    if math.isnan(seconds) or math.isinf(seconds):
        raise ValueError(f"duration must be finite, got {value!r}")
    if seconds < 0:
        raise ValueError(f"duration cannot be negative, got {value!r}")
    return seconds


def _parse_text(text: str) -> float:
    s = text.strip().lower()
    if not s:
        raise ValueError("empty duration")
    try:
        return float(s)
    except ValueError:
        pass
    match = _CLOCK.match(s)
    if match:
        hours, minutes, secs = match.groups()
        return int(hours or 0) * 3600.0 + int(minutes) * 60.0 + float(secs)
    total = 0.0
    pos = 0
    for part in _PART.finditer(s):
        if part.start() != pos or part.group(2) not in _UNITS:
            break
        total += float(part.group(1)) * _UNITS[part.group(2)]
        pos = part.end()
    else:
        if pos == len(s) and pos > 0:
            return total
    raise ValueError(f"cannot read duration {text!r} (try '90', '1:30', '25m' or '1h30m')")


def format_time(seconds: float, *, ms: bool = False, ceil: bool = False) -> str:
    """Format seconds as ``MM:SS`` or ``H:MM:SS``.

    * ``ms=True`` adds milliseconds: ``01:05.250``.
    * ``ceil=True`` rounds up to the next whole second, which is what a
      countdown display should do (0.4 s left shows ``00:01``).

    >>> format_time(65)
    '01:05'
    >>> format_time(3725)
    '1:02:05'
    """
    sign = "-" if seconds < 0 else ""
    value = abs(seconds)
    if ms:
        total_ms = int(math.ceil(value * 1000 - 1e-6) if ceil else math.floor(value * 1000 + 1e-6))
        whole, millis = divmod(total_ms, 1000)
        suffix = f".{millis:03d}"
    else:
        whole = int(math.ceil(value - 1e-9) if ceil else math.floor(value + 1e-9))
        suffix = ""
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{sign}{hours}:{minutes:02d}:{secs:02d}{suffix}"
    return f"{sign}{minutes:02d}:{secs:02d}{suffix}"
