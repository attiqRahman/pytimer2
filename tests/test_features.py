"""Deadline, Stopwatch, PausableTimer, helpers, persistence, formatting and the legacy 0.x API."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from typing import Any, List

import pytest

from pytimer2 import (
    Deadline,
    DeadlineExceeded,
    FakeClock,
    PausableTimer,
    Stopwatch,
    Timer,
    TimerGroup,
    TimerState,
    Watchdog,
    debounce,
    format_time,
    parse_duration,
)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# -- Deadline -----------------------------------------------------------------


def test_deadline_counts_down(clock: FakeClock) -> None:
    d = Deadline(10, clock=clock)
    clock.advance(4)
    assert d.remaining == pytest.approx(6)
    assert d.elapsed == pytest.approx(4)
    assert not d.expired
    d.check()


def test_deadline_check_raises(clock: FakeClock) -> None:
    d = Deadline(2, clock=clock, name="fetch")
    clock.advance(2)
    assert d.expired and d.remaining == 0
    with pytest.raises(DeadlineExceeded, match=r"'fetch'.*during page 3"):
        d.check("page 3")
    assert issubclass(DeadlineExceeded, TimeoutError)


def test_deadline_pause_resume(clock: FakeClock) -> None:
    d = Deadline(5, clock=clock)
    clock.advance(2)
    assert d.pause() is True
    assert d.pause() is False
    clock.advance(100)
    assert d.remaining == pytest.approx(3)
    assert d.resume() is True
    assert d.resume() is False
    clock.advance(3)
    assert d.expired


def test_deadline_extend_and_timeout(clock: FakeClock) -> None:
    d = Deadline("10s", clock=clock)
    clock.advance(8)
    assert d.timeout() == pytest.approx(2)
    assert d.timeout(cap=1) == 1
    assert d.extend(5) == pytest.approx(7)
    assert d.budget == 15


def test_deadline_sync_context_manager(clock: FakeClock) -> None:
    with Deadline(1, clock=clock) as d:
        clock.advance(2)
        with pytest.raises(DeadlineExceeded):
            d.check()


# -- Stopwatch ----------------------------------------------------------------


def test_stopwatch_basic(clock: FakeClock) -> None:
    sw = Stopwatch(clock=clock)
    assert sw.elapsed == 0
    sw.start()
    clock.advance(1.5)
    assert sw.elapsed == pytest.approx(1.5)
    assert sw.pause() is True
    clock.advance(10)
    assert sw.elapsed == pytest.approx(1.5)
    assert sw.resume() is True
    clock.advance(0.5)
    assert str(sw) == "00:02.000"


def test_stopwatch_laps(clock: FakeClock) -> None:
    sw = Stopwatch(clock=clock, start=True)
    clock.advance(1)
    assert sw.lap() == pytest.approx(1)
    clock.advance(2.5)
    assert sw.lap() == pytest.approx(2.5)
    assert sw.laps == [pytest.approx(1), pytest.approx(2.5)]
    sw.restart()
    assert sw.laps == [] and sw.running and sw.elapsed == 0


def test_stopwatch_context_manager(clock: FakeClock) -> None:
    with Stopwatch(clock=clock) as sw:
        clock.advance(3)
    clock.advance(3)
    assert sw.elapsed == pytest.approx(3)
    assert not sw.running
    assert sw.stop() is False


# -- PausableTimer ------------------------------------------------------------


def test_pausable_timer_like_threading_timer(clock: FakeClock) -> None:
    calls: List[Any] = []
    t = PausableTimer(5, lambda *a, **k: calls.append((a, k)), args=(1, 2), kwargs={"x": 3}, clock=clock)
    t.start()
    assert t.is_alive()
    clock.advance(2)
    t.pause()
    clock.advance(100)
    t.resume()
    clock.advance(3)
    assert calls == [((1, 2), {"x": 3})]
    assert t.finished.is_set() and not t.is_alive()
    t.join()


def test_pausable_timer_cancel(clock: FakeClock) -> None:
    calls: List[int] = []
    t = PausableTimer(5, lambda: calls.append(1), clock=clock)
    t.start()
    clock.advance(1)
    t.cancel()
    clock.advance(10)
    assert calls == [] and t.finished.is_set() and not t.is_alive()


def test_pausable_timer_start_once(clock: FakeClock) -> None:
    t = PausableTimer(5, lambda: None, clock=clock)
    with pytest.raises(RuntimeError):
        t.join()
    t.start()
    with pytest.raises(RuntimeError):
        t.start()


# -- TimerGroup ---------------------------------------------------------------


def test_timer_group(clock: FakeClock) -> None:
    fired: List[int] = []
    group = TimerGroup([Timer(i, lambda i=i: fired.append(i), clock=clock) for i in (1, 2, 3)])
    assert len(group) == 3
    group.start()
    clock.advance(1.5)
    assert group.pause() == 2
    clock.advance(10)
    assert group.resume() == 2
    clock.advance(0.5)
    assert fired == [1, 2]
    assert group.stop() == 1
    assert not group.active


def test_timer_group_add_remove_context(clock: FakeClock) -> None:
    group = TimerGroup()
    t = group.add(Timer(5, clock=clock))
    assert t in group
    with group:
        assert t.running
    assert t.state is TimerState.STOPPED
    assert group.remove(t) is True
    assert group.remove(t) is False


# -- Watchdog -----------------------------------------------------------------


def test_watchdog(clock: FakeClock) -> None:
    alarms: List[float] = []
    dog = Watchdog(3, lambda: alarms.append(clock.now()), clock=clock)
    dog.kick()  # ignored before start
    dog.start()
    for _ in range(5):
        clock.advance(2)
        dog.kick()
    assert alarms == []
    clock.advance(3)
    assert alarms == [13.0] and dog.fired
    dog.kick()  # re-arms
    clock.advance(3)
    assert alarms == [13.0, 16.0]
    dog.stop()
    dog.kick()
    clock.advance(10)
    assert len(alarms) == 2


# -- debounce -----------------------------------------------------------------


def test_debounce(clock: FakeClock) -> None:
    saved: List[str] = []

    @debounce(0.5, clock=clock)
    def save(text: str) -> None:
        saved.append(text)

    for text in ("a", "ab", "abc"):
        save(text)
        clock.advance(0.2)
    assert saved == [] and save.pending
    clock.advance(0.5)
    assert saved == ["abc"]
    assert save.__name__ == "save"


def test_debounce_cancel_and_flush(clock: FakeClock) -> None:
    calls: List[int] = []
    fn = debounce(1, clock=clock)(calls.append)
    fn(1)
    assert fn.cancel() is True
    assert fn.cancel() is False
    clock.advance(2)
    fn(2)
    assert fn.flush() is True
    assert fn.flush() is False
    clock.advance(2)
    assert calls == [2]


# -- persistence --------------------------------------------------------------


def test_to_dict_is_json(clock: FakeClock) -> None:
    t = Timer(60, tick=1, name="quiz", clock=clock).start()
    clock.advance(10)
    data = json.loads(json.dumps(t.to_dict()))
    assert data["state"] == "running"
    assert data["elapsed"] == pytest.approx(10)
    assert data["name"] == "quiz"


def test_from_dict_paused(clock: FakeClock) -> None:
    t = Timer(60, clock=clock).start()
    clock.advance(10)
    t.pause()
    data = t.to_dict()
    data["saved_at"] -= 1000  # paused timers do not catch up
    clock2 = FakeClock()
    fired: List[float] = []
    t2 = Timer.from_dict(data, clock=clock2, on_finish=lambda: fired.append(clock2.now()))
    assert t2.paused and t2.remaining == pytest.approx(50)
    t2.resume()
    clock2.advance(50)
    assert fired == [50.0]


def test_from_dict_running_catches_up(clock: FakeClock) -> None:
    data = Timer(60, clock=clock).start().to_dict()
    data["saved_at"] = time.time() - 20
    t2 = Timer.from_dict(data, clock=FakeClock())
    assert t2.running
    assert t2.remaining == pytest.approx(40, abs=0.5)
    t3 = Timer.from_dict(data, clock=FakeClock(), catch_up=False)
    assert t3.remaining == pytest.approx(60)


def test_from_dict_overdue_fires_once(clock: FakeClock) -> None:
    data = Timer(5, clock=clock).start().to_dict()
    data["saved_at"] = time.time() - 60
    fired: List[int] = []
    t2 = Timer.from_dict(data, clock=FakeClock(), on_finish=lambda: fired.append(1))
    assert t2.finished and fired == [1]


def test_from_dict_repeating_skips_missed_cycles(clock: FakeClock) -> None:
    data = Timer(10, repeat=True, clock=clock).start().to_dict()
    data["saved_at"] = time.time() - 35
    fired: List[int] = []
    t2 = Timer.from_dict(data, clock=FakeClock(), on_finish=lambda: fired.append(1))
    assert t2.cycles == 3 and fired == []
    assert t2.remaining == pytest.approx(5, abs=0.5)
    t2.stop()


def test_from_dict_finished_and_idle(clock: FakeClock) -> None:
    t = Timer(1, clock=clock).start()
    clock.advance(1)
    assert Timer.from_dict(t.to_dict(), clock=clock).finished
    assert Timer.from_dict(Timer(3, clock=clock).to_dict(), clock=clock).state is TimerState.IDLE


# -- formatting -----------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds, kwargs, expected",
    [
        (0, {}, "00:00"),
        (65, {}, "01:05"),
        (3725, {}, "1:02:05"),
        (59.4, {}, "00:59"),
        (59.4, {"ceil": True}, "01:00"),
        (65.25, {"ms": True}, "01:05.250"),
        (-5, {}, "-00:05"),
    ],
)
def test_format_time(seconds: float, kwargs: Any, expected: str) -> None:
    assert format_time(seconds, **kwargs) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("90", 90),
        ("2.5", 2.5),
        ("2.5s", 2.5),
        ("500ms", 0.5),
        ("25m", 1500),
        ("1h30m", 5400),
        ("1h 30m 15s", 5415),
        ("1 hour, 5 minutes", 3900),
        ("1:30", 90),
        ("1:02:03", 3723),
        ("2d", 172800),
        (" 10 SECONDS ", 10),
    ],
)
def test_parse_duration(text: str, expected: float) -> None:
    assert parse_duration(text) == pytest.approx(expected)


def test_parse_duration_types_and_errors() -> None:
    assert parse_duration(timedelta(minutes=2)) == 120
    assert parse_duration(7) == 7.0
    for bad in ("", "abc", "5x", "1h banana", "-5", "1::2"):
        with pytest.raises(ValueError):
            parse_duration(bad)
    with pytest.raises(TypeError):
        parse_duration(True)
    with pytest.raises(TypeError):
        parse_duration([1])  # type: ignore[arg-type]


# -- legacy 0.x API -------------------------------------------------------------


def test_legacy_api_still_works(clock: FakeClock) -> None:
    t = Timer(clock=clock)
    with pytest.warns(DeprecationWarning, match="start_countdown"):
        t.start_countdown(duration=10)
    clock.advance(2.5)
    with pytest.warns(DeprecationWarning):
        assert t.get_countdown() == 8  # 7.5 rounded up
    with pytest.warns(DeprecationWarning):
        t.pause_countdown()
    clock.advance(5)
    with pytest.warns(DeprecationWarning):
        assert t.get_countdown() == 8
    with pytest.warns(DeprecationWarning):
        t.resume_countdown()
    clock.advance(1)
    with pytest.warns(DeprecationWarning):
        t.stop_countdown()
    with pytest.warns(DeprecationWarning):
        assert t.get_countdown() == 0


def test_legacy_methods_are_safe_when_idle(clock: FakeClock) -> None:
    t = Timer(clock=clock)
    with pytest.warns(DeprecationWarning):
        t.pause_countdown()
    with pytest.warns(DeprecationWarning):
        t.resume_countdown()
    with pytest.warns(DeprecationWarning):
        t.stop_countdown()
    with pytest.warns(DeprecationWarning):
        assert t.get_countdown() == 0


def test_legacy_default_duration(clock: FakeClock) -> None:
    t = Timer(clock=clock)
    with pytest.warns(DeprecationWarning):
        t.start_countdown()
    assert t.duration == 500
    t.stop()
