"""Real clock, threads, asyncio, process exit and the CLI.

Tolerances are loose on purpose: CI machines (especially Windows and macOS)
can be slow to wake threads.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import threading
import time
from typing import List

import pytest

from pytimer2 import (
    Deadline,
    DeadlineExceeded,
    PausableTimer,
    SystemClock,
    Timer,
    TimerError,
    TimerGroup,
    TimerState,
    every,
)
from pytimer2.__main__ import main

SLACK = 0.25
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def test_fires_on_time() -> None:
    fired: List[float] = []
    start = time.monotonic()
    t = Timer(0.2, lambda: fired.append(time.monotonic())).start()
    assert t.wait(5)
    assert fired, "on_finish ran before wait() returned"
    assert 0.2 <= fired[0] - start < 0.2 + SLACK


def test_wait_timeout() -> None:
    t = Timer(5).start()
    assert t.wait(0.05) is False
    t.stop()
    assert t.wait(2) is True


def test_pause_resume_real_clock() -> None:
    t = Timer(0.3).start()
    time.sleep(0.1)
    t.pause()
    time.sleep(0.3)
    assert t.remaining == pytest.approx(0.2, abs=0.08)
    start = time.monotonic()
    t.resume()
    assert t.wait(5)
    assert 0.15 <= time.monotonic() - start < 0.2 + SLACK


def test_callbacks_run_on_one_callback_thread_not_scheduler() -> None:
    threads: List[str] = []
    timers = [Timer(0.05, lambda: threads.append(threading.current_thread().name)).start() for _ in range(20)]
    for t in timers:
        assert t.wait(5)
    assert set(threads) == {"pytimer2-callbacks"}


def test_no_thread_per_timer() -> None:
    before = threading.active_count()
    timers = [Timer(10).start() for _ in range(200)]
    assert threading.active_count() - before <= 2
    for t in timers:
        t.stop()


def test_wait_from_callback_raises() -> None:
    errors: List[BaseException] = []
    other = Timer(10).start()

    def cb() -> None:
        try:
            other.wait(0.1)
        except TimerError as err:
            errors.append(err)

    assert Timer(0.01, cb).start().wait(5)
    assert len(errors) == 1
    other.stop()


def test_every_real_clock_no_drift() -> None:
    times: List[float] = []
    t = every(0.05, lambda: times.append(time.monotonic()))
    time.sleep(0.53)
    t.stop()
    assert 8 <= len(times) <= 11


def test_include_suspend_clock_works() -> None:
    clock = SystemClock(include_suspend=True)
    t = Timer(0.1, clock=clock).start()
    assert t.wait(5)
    assert clock.now() > 0


def test_pausable_timer_runs_in_own_thread() -> None:
    names: List[str] = []
    t = PausableTimer(0.05, lambda: names.append(threading.current_thread().name), name="worker")
    t.start()
    t.join(5)
    assert names == ["worker"]
    assert not t.is_alive()


def test_group_wait() -> None:
    group = TimerGroup([Timer(0.05), Timer(0.1)]).start()
    assert group.wait(5)


def test_async_callbacks_and_await() -> None:
    async def run() -> List[str]:
        seen: List[str] = []

        async def on_finish() -> None:
            await asyncio.sleep(0)
            seen.append("async")

        t = Timer(0.05, on_finish).start()
        result = await t
        assert result is t
        await asyncio.sleep(0.05)
        return seen

    assert asyncio.run(run()) == ["async"]


def test_wait_async_timeout_and_stop() -> None:
    async def run() -> None:
        t = Timer(5).start()
        assert await t.wait_async(0.05) is False
        asyncio.get_running_loop().call_later(0.05, t.stop)
        assert await t.wait_async(5) is True
        assert t.state is TimerState.STOPPED

    asyncio.run(run())


def test_async_callback_without_loop_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    async def cb() -> None:  # pragma: no cover - must not run
        raise AssertionError

    assert Timer(0.01, cb).start().wait(5)
    assert "needs an event loop" in caplog.text


@pytest.mark.skipif(sys.version_info < (3, 11), reason="async with Deadline needs 3.11+")
def test_async_deadline_cancels_block() -> None:
    async def run() -> None:
        with pytest.raises(DeadlineExceeded):
            async with Deadline(0.05):
                await asyncio.sleep(5)

    asyncio.run(run())


@pytest.mark.skipif(sys.version_info < (3, 11), reason="async with Deadline needs 3.11+")
def test_async_deadline_pause_extends_block() -> None:
    async def run() -> None:
        async with Deadline(0.15) as d:
            d.pause()
            await asyncio.sleep(0.3)
            d.resume()
            await asyncio.sleep(0.05)
        assert d.remaining > 0

    asyncio.run(run())


@pytest.mark.skipif(sys.version_info >= (3, 11), reason="only older Pythons lack asyncio.timeout_at")
def test_async_deadline_needs_311() -> None:  # pragma: no cover - version specific
    async def run() -> None:
        async with Deadline(1):
            pass

    with pytest.raises(TimerError):
        asyncio.run(run())


def test_running_timer_does_not_keep_process_alive() -> None:
    code = "import pytimer2; pytimer2.Timer(60, print).start(); pytimer2.every(1, print); print('bye')"
    env = dict(os.environ, PYTHONPATH=SRC)
    start = time.monotonic()
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "bye"
    assert time.monotonic() - start < 10


def test_cli_counts_down() -> None:
    buf = io.StringIO()
    assert main(["0.25", "--tick", "0.1", "--label", "Tea"], out=buf) == 0
    lines = buf.getvalue().splitlines()
    assert lines[0] == "Tea: 00:01"
    assert lines[-1].startswith("Tea: 00:00 time is up!")


def test_cli_quiet_and_bad_input(capsys: pytest.CaptureFixture[str]) -> None:
    buf = io.StringIO()
    assert main(["0.05", "-q"], out=buf) == 0
    assert buf.getvalue().count("\n") == 1
    with pytest.raises(SystemExit):
        main(["banana"])
    assert "cannot read duration" in capsys.readouterr().err


def test_cli_module_entry_point() -> None:
    env = dict(os.environ, PYTHONPATH=SRC)
    out = subprocess.run(
        [sys.executable, "-m", "pytimer2", "--version"], env=env, capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0
    assert "1.0.0" in out.stdout
