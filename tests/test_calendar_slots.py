"""A calendar slot runs once per allowed day (review 2026-11-08, B2)."""

from __future__ import annotations

import random
from datetime import datetime

from korodexios_core.kernel.scheduler import TaskScheduler


def simulate(
    spec: str, start: datetime, seconds: int, *, step_back_at: int | None = None
) -> list[str]:
    """Run *spec* for *seconds* simulated seconds; return the wall time of every run."""
    clock = {"mono": 1000.0, "wall": start.timestamp()}
    runs: list[str] = []
    scheduler = TaskScheduler(
        clock=lambda: clock["mono"], wall_clock=lambda: clock["wall"], rng=random.Random(7)
    )
    scheduler.schedule_at(
        spec, lambda: runs.append(datetime.fromtimestamp(clock["wall"]).isoformat()), name="job"
    )
    scheduler.start()
    for second in range(seconds):
        if second == step_back_at:
            clock["wall"] -= 5
        scheduler.run_due()
        clock["mono"] += 1.0
        clock["wall"] += 1.0
    return runs


def test_a_window_runs_once_a_day() -> None:
    runs = simulate("daily 04:00-05:00", datetime(2026, 11, 9, 0, 0), 86400)
    assert len(runs) == 1
    assert "T04:" in runs[0]


def test_two_days_give_two_runs_each_inside_the_window() -> None:
    runs = simulate("daily 04:00-05:00", datetime(2026, 11, 9, 0, 0), 2 * 86400)
    assert [run[:10] for run in runs] == ["2026-11-09", "2026-11-10"]
    assert all("T04:" in run for run in runs)


def test_a_weekly_window_runs_once_a_week() -> None:
    # 2026-11-09 is a Monday
    runs = simulate("mon 02:00-05:00", datetime(2026, 11, 9, 0, 0), 7 * 86400)
    assert len(runs) == 1


def test_a_backward_clock_step_does_not_run_a_slot_twice() -> None:
    runs = simulate("daily 03:00", datetime(2026, 11, 9, 2, 0), 7200, step_back_at=1800)
    assert len(runs) == 1


def test_a_single_time_still_runs_every_day() -> None:
    runs = simulate("daily 03:00", datetime(2026, 11, 9, 0, 0), 3 * 86400)
    assert len(runs) == 3


def test_a_manual_early_run_leaves_todays_slot_alone() -> None:
    clock = {"mono": 1000.0, "wall": datetime(2026, 11, 9, 1, 0).timestamp()}
    runs: list[int] = []
    scheduler = TaskScheduler(
        clock=lambda: clock["mono"], wall_clock=lambda: clock["wall"], rng=random.Random(7)
    )
    scheduler.schedule_at("daily 04:00-05:00", lambda: runs.append(1), name="job")
    scheduler.start()
    assert scheduler.run_task("job") is True
    for _ in range(4 * 3600):
        scheduler.run_due()
        clock["mono"] += 1.0
        clock["wall"] += 1.0
    assert len(runs) == 2, "the manual run and today's scheduled one"
