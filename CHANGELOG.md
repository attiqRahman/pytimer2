# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.0.0] - unreleased

A full rewrite. The 0.x methods still work (with a `DeprecationWarning`).

### Added

- `Timer` with `start()`, `pause()`, `resume()`, `toggle()`, `stop()`,
  `restart()`, `reset()`, `extend()`, `wait()`, `wait_async()` and `await timer`.
- Callbacks: `on_finish`, `on_tick` (with `tick=`), `on_pause`, `on_resume`,
  `on_stop`. Callbacks may be `async def`.
- Repeating timers (`repeat=True` or a number of cycles) without drift, and `every()`.
- `Deadline` and `DeadlineExceeded`: a pausable time budget, with
  `async with Deadline(...)` on Python 3.11+.
- `Stopwatch` with laps.
- `PausableTimer`: a drop-in replacement for `threading.Timer` that can pause.
- `TimerGroup`, `Watchdog` and `debounce`.
- `Clock`, `SystemClock` (optional `include_suspend`) and `FakeClock` for instant tests.
- `Timer.to_dict()` and `Timer.from_dict()` to save and restore timers.
- `format_time()` and `parse_duration()` (`"25m"`, `"1h30m"`, `"1:30"`, ...).
- Command line tool: `pytimer2 25m` and `python -m pytimer2`.
- Type hints (`py.typed`), `mypy --strict` clean.
- `llms.txt` with an API summary for AI coding assistants.

### Changed

- All timers share one scheduler thread and one callback thread per clock
  instead of one thread per countdown.
- Timing is exact (monotonic clock) instead of counting down in one-second sleeps.
- Threads are daemon threads, so a running timer never keeps the program alive.
- Packaging moved to `pyproject.toml` with a `src/` layout. Requires Python 3.9+.

### Deprecated

- `start_countdown()`, `get_countdown()`, `pause_countdown()`,
  `resume_countdown()` and `stop_countdown()`. They will be removed in 2.0.

## [0.2.4] - 2024-09

- Countdown timer with start, pause, resume and stop.
