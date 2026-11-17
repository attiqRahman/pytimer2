"""Timer logic, driven by a FakeClock (instant and deterministic)."""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from pytimer2 import FakeClock, Timer, TimerError, TimerState, every


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


class Recorder:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.events: List[Tuple[Any, ...]] = []

    def __call__(self, name: str) -> Any:
        def cb(*args: Any) -> None:
            self.events.append((name, self.clock.now(), *args))

        return cb

    def names(self) -> List[str]:
        return [e[0] for e in self.events]


def test_initial_state(clock: FakeClock) -> None:
    t = Timer(5, clock=clock)
    assert t.state is TimerState.IDLE
    assert t.remaining == 5
    assert t.elapsed == 0
    assert t.progress == 0
    assert not t.active


def test_finishes_on_time(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), clock=clock).start()
    clock.advance(4.999)
    assert rec.events == []
    assert t.running
    clock.advance(0.001)
    assert rec.events == [("finish", 5.0)]
    assert t.finished and t.remaining == 0 and t.progress == 1.0


def test_elapsed_and_remaining_while_running(clock: FakeClock) -> None:
    t = Timer(10, clock=clock).start()
    clock.advance(3)
    assert t.elapsed == pytest.approx(3)
    assert t.remaining == pytest.approx(7)
    assert t.progress == pytest.approx(0.3)


def test_pause_freezes_time(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), clock=clock).start()
    clock.advance(2)
    assert t.pause() is True
    clock.advance(100)
    assert t.remaining == pytest.approx(3)
    assert rec.events == []
    assert t.resume() is True
    clock.advance(3)
    assert rec.events == [("finish", 105.0)]


def test_pause_resume_return_values(clock: FakeClock) -> None:
    t = Timer(5, clock=clock)
    assert t.pause() is False
    assert t.resume() is False
    t.start()
    assert t.resume() is False
    assert t.pause() is True
    assert t.pause() is False
    assert t.resume() is True


def test_toggle(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    assert t.toggle() and t.paused
    assert t.toggle() and t.running


def test_many_pauses_do_not_lose_time(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(10, rec("finish"), clock=clock).start()
    for _ in range(9):
        clock.advance(1)
        t.pause()
        clock.advance(0.5)
        t.resume()
    clock.advance(1)
    assert rec.events == [("finish", 14.5)]


def test_stop(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), on_stop=rec("stop"), clock=clock).start()
    clock.advance(2)
    assert t.stop() is True
    assert t.stop() is False
    clock.advance(10)
    assert rec.names() == ["stop"]
    assert t.state is TimerState.STOPPED
    assert t.elapsed == pytest.approx(2)


def test_stop_while_paused(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    t.pause()
    assert t.stop() is True
    assert t.state is TimerState.STOPPED


def test_start_twice_raises(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    with pytest.raises(TimerError):
        t.start()
    t.pause()
    with pytest.raises(TimerError):
        t.start()


def test_start_returns_self_and_new_duration(clock: FakeClock) -> None:
    t = Timer(clock=clock)
    assert t.start(3) is t
    assert t.duration == 3


def test_start_after_finish_runs_again(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(1, rec("finish"), clock=clock).start()
    clock.advance(1)
    t.start()
    clock.advance(1)
    assert rec.names() == ["finish", "finish"]


def test_restart(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), on_stop=rec("stop"), clock=clock).start()
    clock.advance(4)
    assert t.restart() is t
    clock.advance(4)
    assert rec.events == []
    clock.advance(1)
    assert rec.events == [("finish", 9.0)]


def test_reset(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), clock=clock).start()
    clock.advance(3)
    assert t.reset() is t
    assert t.state is TimerState.IDLE
    assert t.remaining == 5
    clock.advance(10)
    assert rec.events == []


def test_zero_duration_finishes_immediately(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(0, rec("finish"), clock=clock).start()
    assert t.finished
    assert rec.events == [("finish", 0.0)]


def test_string_duration(clock: FakeClock) -> None:
    assert Timer("1m30s", clock=clock).duration == 90
    assert Timer("1:30", clock=clock).duration == 90


@pytest.mark.parametrize("bad", [-1, "soon", float("nan")])
def test_bad_duration(clock: FakeClock, bad: Any) -> None:
    with pytest.raises(ValueError):
        Timer(bad, clock=clock)


def test_bad_tick_and_repeat(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        Timer(5, tick=0, clock=clock)
    with pytest.raises(ValueError):
        Timer(5, repeat=0, clock=clock)
    with pytest.raises(ValueError):
        Timer(0, repeat=True, clock=clock).start()


def test_ticks(clock: FakeClock) -> None:
    rec = Recorder(clock)
    Timer(3, rec("finish"), tick=1, on_tick=rec("tick"), clock=clock).start()
    clock.advance(10)
    assert rec.events == [("tick", 1.0, 2.0), ("tick", 2.0, 1.0), ("finish", 3.0)]


def test_ticks_are_strictly_before_end(clock: FakeClock) -> None:
    rec = Recorder(clock)
    Timer(1, tick=0.1, on_tick=rec("tick"), clock=clock).start()
    clock.advance(2)
    assert len(rec.events) == 9


def test_ticks_count_running_time_only(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(4, tick=1, on_tick=rec("tick"), clock=clock).start()
    clock.advance(1.5)
    t.pause()
    clock.advance(5)
    t.resume()
    clock.advance(1)
    assert rec.events == [("tick", 1.0, 3.0), ("tick", 7.0, 2.0)]


def test_repeat_forever_no_drift(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(0.1, rec("fire"), repeat=True, clock=clock).start()
    clock.advance(100.05)
    assert len(rec.events) == 1000
    assert rec.events[-1][1] == pytest.approx(100.0)
    assert t.cycles == 1000 and t.running
    t.stop()


def test_repeat_count(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(2, rec("fire"), repeat=3, clock=clock).start()
    clock.advance(100)
    assert [e[1] for e in rec.events] == [2.0, 4.0, 6.0]
    assert t.finished and t.cycles == 3
    assert t.repeat == 3


def test_repeat_with_ticks(clock: FakeClock) -> None:
    rec = Recorder(clock)
    Timer(2, rec("fin"), tick=1, on_tick=rec("tick"), repeat=2, clock=clock).start()
    clock.advance(10)
    assert rec.names() == ["tick", "fin", "tick", "fin"]


def test_pause_inside_repeat(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(2, rec("fire"), repeat=True, clock=clock).start()
    clock.advance(3)
    t.pause()
    clock.advance(10)
    t.resume()
    clock.advance(1)
    assert [e[1] for e in rec.events] == [2.0, 14.0]
    t.stop()


def test_every(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = every(1, rec("fire"), clock=clock)
    clock.advance(3.5)
    assert len(rec.events) == 3
    assert t.repeat is True
    t.stop()


def test_extend_running(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, rec("finish"), clock=clock).start()
    clock.advance(4)
    assert t.extend(3) is True
    clock.advance(3.5)
    assert rec.events == []
    clock.advance(0.5)
    assert rec.events == [("finish", 8.0)]


def test_extend_negative_finishes_now(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(10, rec("finish"), clock=clock).start()
    clock.advance(4)
    t.extend("-7s")
    assert rec.events == [("finish", 4.0)]
    assert t.extend(1) is False


def test_extend_while_paused(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    clock.advance(1)
    t.pause()
    t.extend(5)
    assert t.remaining == pytest.approx(9)


def test_extend_lasts_one_cycle_only(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    t.extend(10)
    assert t.remaining == pytest.approx(15)
    assert t.duration == 5
    t.restart()
    assert t.remaining == pytest.approx(5)
    t.extend(10)
    t.stop()
    t.start()
    assert t.remaining == pytest.approx(5)
    t.extend(10)
    t.reset()
    assert t.remaining == pytest.approx(5)


def test_extend_does_not_carry_into_next_repeat(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(2, rec("finish"), repeat=3, clock=clock).start()
    clock.advance(1)
    t.extend(3)
    clock.advance(10)
    assert rec.events == [("finish", 5.0), ("finish", 7.0), ("finish", 9.0)]


def test_negative_extend_on_repeat_restores_length(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(2, rec("finish"), repeat=True, clock=clock).start()
    clock.advance(1)
    t.extend(-5)
    assert rec.events == [("finish", 1.0)]
    clock.advance(2)
    assert rec.events == [("finish", 1.0), ("finish", 3.0)]
    t.stop()


def test_extended_timer_save_and_restore(clock: FakeClock) -> None:
    t = Timer(5, repeat=2, clock=clock).start()
    clock.advance(1)
    t.extend(10)
    data = t.to_dict()
    assert data["duration"] == 5
    assert data["extended"] == pytest.approx(10)
    t.stop()

    restored = Timer.from_dict(data, catch_up=False, clock=clock)
    assert restored.remaining == pytest.approx(14)
    assert restored.duration == 5
    clock.advance(14)
    assert restored.cycles == 1
    assert restored.remaining == pytest.approx(5)
    restored.stop()


def test_restore_skips_finished_extended_cycle(clock: FakeClock) -> None:
    data = Timer(5, repeat=3, clock=clock).start().to_dict()
    data.update(elapsed=17.0, extended=10.0)
    del data["saved_at"]
    restored = Timer.from_dict(data, clock=clock)
    assert restored.cycles == 1
    assert restored.remaining == pytest.approx(3)
    restored.stop()


def test_callbacks_can_control_timer(clock: FakeClock) -> None:
    rec = Recorder(clock)
    holder: List[Timer] = []

    def on_tick(remaining: float) -> None:
        rec("tick")(remaining)
        if remaining == 2:
            holder[0].stop()

    t = Timer(5, rec("finish"), tick=1, on_tick=on_tick, clock=clock)
    holder.append(t)
    t.start()
    clock.advance(10)
    assert rec.names() == ["tick", "tick", "tick"]
    assert t.state is TimerState.STOPPED


def test_callback_restart_from_on_finish(clock: FakeClock) -> None:
    count: List[float] = []
    holder: List[Timer] = []

    def again() -> None:
        count.append(clock.now())
        if len(count) < 3:
            holder[0].restart()

    holder.append(Timer(1, again, clock=clock))
    holder[0].start()
    clock.advance(10)
    assert count == [1.0, 2.0, 3.0]


def test_callback_errors_are_logged_not_raised(clock: FakeClock, caplog: pytest.LogCaptureFixture) -> None:
    rec = Recorder(clock)

    def boom() -> None:
        raise ValueError("boom")

    Timer(1, boom, clock=clock).start()
    Timer(2, rec("after"), clock=clock).start()
    clock.advance(5)
    assert rec.names() == ["after"]
    assert "boom" in caplog.text


def test_state_callbacks(clock: FakeClock) -> None:
    rec = Recorder(clock)
    t = Timer(5, on_pause=rec("pause"), on_resume=rec("resume"), clock=clock).start()
    t.pause()
    t.resume()
    assert rec.names() == ["pause", "resume"]


def test_many_timers_share_one_scheduler(clock: FakeClock) -> None:
    rec = Recorder(clock)
    timers = [Timer(i % 7 + 1, rec("fire"), clock=clock).start() for i in range(500)]
    assert all(t.clock.scheduler is clock.scheduler for t in timers)
    clock.advance(10)
    assert len(rec.events) == 500
    times = [e[1] for e in rec.events]
    assert times == sorted(times)


def test_cancelled_events_are_compacted(clock: FakeClock) -> None:
    t = Timer(5, clock=clock).start()
    for _ in range(1000):
        t.pause()
        t.resume()
    assert clock.scheduler.pending_events() == 1
    assert len(clock.scheduler._heap) < 200


def test_wait_raises_with_fake_clock(clock: FakeClock) -> None:
    t = Timer(1, clock=clock).start()
    with pytest.raises(TimerError):
        t.wait()


def test_context_manager(clock: FakeClock) -> None:
    with Timer(5, clock=clock) as t:
        assert t.running
    assert t.state is TimerState.STOPPED


def test_repr(clock: FakeClock) -> None:
    t = Timer(90, name="tea", clock=clock).start()
    clock.advance(30)
    assert repr(t) == "<Timer 'tea' running 01:00.000 left of 90s>"


def test_fake_clock_cannot_go_back(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        clock.advance(-1)
    clock.sleep(2)
    assert clock.now() == 2
