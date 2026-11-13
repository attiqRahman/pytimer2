"""Command line countdown: ``pytimer2 25m`` or ``python -m pytimer2 1:30``."""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, TextIO

from . import __version__
from ._format import format_time, parse_duration
from ._timer import Timer


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pytimer2",
        description="pytimer2: pausable timers and deadlines for Python. Runs a terminal countdown.",
        epilog="Examples: pytimer2 90   pytimer2 25m   pytimer2 1h30m   pytimer2 1:30 -l Tea",
    )
    parser.add_argument("duration", help="how long: 90, 2.5s, 25m, 1h30m, 1:30 or 1:02:03")
    parser.add_argument("-l", "--label", default="", help="text shown before the time")
    parser.add_argument(
        "-t", "--tick", type=float, default=1.0, help="update interval in seconds (default 1)"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="print only the final message")
    parser.add_argument("--no-bell", action="store_true", help="do not ring the terminal bell at the end")
    parser.add_argument("-V", "--version", action="version", version=f"pytimer2 {__version__}")
    return parser


def main(argv: Optional[List[str]] = None, out: Optional[TextIO] = None) -> int:
    """Run the CLI. Returns the exit code (0 done, 130 interrupted)."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        seconds = parse_duration(args.duration)
    except ValueError as err:
        parser.error(str(err))
    if not args.tick > 0:
        parser.error("--tick must be greater than zero")
    stream = out if out is not None else sys.stdout
    tty = stream.isatty()
    prefix = f"{args.label}: " if args.label else ""

    def show(remaining: float) -> None:
        if args.quiet:
            return
        line = f"{prefix}{format_time(remaining, ceil=True)}"
        if tty:
            stream.write(f"\r{line}   ")
        else:
            stream.write(line + "\n")
        stream.flush()

    timer = Timer(seconds, tick=args.tick, on_tick=show, name=args.label or None)
    show(seconds)
    timer.start()
    try:
        while not timer.wait(0.2):
            pass
    except KeyboardInterrupt:
        timer.stop()
        if tty and not args.quiet:
            stream.write("\n")
        stream.write(f"{prefix}stopped with {format_time(timer.remaining, ceil=True)} left\n")
        stream.flush()
        return 130
    if tty and not args.quiet:
        stream.write("\r")
    bell = "\a" if tty and not args.no_bell else ""
    stream.write(f"{prefix}{format_time(0)} time is up!{bell}   \n")
    stream.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
