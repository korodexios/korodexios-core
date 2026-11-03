"""v0.5.0: scheduler jitter and calendar, rate limiter, supervised processes, durable queue."""

from __future__ import annotations

import random
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.cli.init_project import build_files, project_from_inputs
from korodexios_core.constants import ExitCode
from korodexios_core.events.bus import EventBus
from korodexios_core.kernel.calendar import CalendarSpec, parse_days, parse_spec, parse_time
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.limiter import (
    LimiterBlocked,
    LimiterRegistry,
    read_limiter_states,
)
from korodexios_core.kernel.process import ProcessError, ProcessSupervisor
from korodexios_core.kernel.queue import JobQueue, PermanentJobError
from korodexios_core.kernel.scheduler import TaskScheduler

from .test_ipc_routes import http_call
from .test_scheduler import FakeClock

# ---------------------------------------------------------------------------- R8: init


@pytest.mark.parametrize("source", ["git", "index"])
def test_generated_pyproject_builds_with_a_direct_reference(source: str) -> None:
    project = project_from_inputs("demo_app", chassis_source=source)
    data = tomllib.loads(build_files(project)["pyproject.toml"])
    dependency = data["project"]["dependencies"][0]
    metadata = data["tool"]["hatch"].get("metadata", {})
    if " @ " in dependency:
        assert metadata.get("allow-direct-references") is True
    assert data["build-system"]["build-backend"] == "hatchling.build"


# ---------------------------------------------------------------------------- calendar


def test_parse_spec_reads_days_and_times() -> None:
    spec = parse_spec("mon,thu 02:00-05:00")
    assert spec.weekdays == frozenset({0, 3})
    assert (spec.start, spec.end) == (parse_time("02:00"), parse_time("05:00"))
    assert parse_spec("1st 04:30").month_days == frozenset({1})
    assert parse_spec("03:00").weekdays == frozenset()
    assert parse_spec(spec.describe()) == spec
    assert parse_days("weekdays") == (frozenset(range(5)), frozenset())


@pytest.mark.parametrize("bad", ["", "mon", "mon 25:00", "funday 03:00", "mon 05:00-02:00"])
def test_parse_spec_rejects_nonsense(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_spec(bad)


def test_next_after_lands_inside_the_window_on_an_allowed_day() -> None:
    spec = parse_spec("mon 02:00-05:00")
    rng = random.Random(7)
    start = datetime(2026, 11, 3, 12, 0).timestamp()  # a Tuesday
    for _ in range(20):
        moment = datetime.fromtimestamp(spec.next_after(start, rng))
        assert moment.weekday() == 0 and 2 <= moment.hour < 5
        assert moment > datetime.fromtimestamp(start)
    exact = datetime.fromtimestamp(parse_spec("03:00").next_after(start))
    assert (exact.day, exact.hour, exact.minute) == (4, 3, 0)


def test_calendar_allows_and_a_day_only_constraint() -> None:
    spec = CalendarSpec(weekdays=frozenset({0}), any_time=True)
    monday = datetime(2026, 11, 2, 9, 30).timestamp()
    tuesday = datetime(2026, 11, 3, 9, 30).timestamp()
    assert spec.allows(monday) and not spec.allows(tuesday)
    assert datetime.fromtimestamp(spec.next_after(tuesday)).weekday() == 0


# ---------------------------------------------------------------------------- scheduler


def make_scheduler(**kwargs: Any) -> tuple[TaskScheduler, FakeClock]:
    clock = FakeClock()
    return TaskScheduler(clock=clock, **kwargs), clock


def test_jitter_spreads_the_interval_within_its_share() -> None:
    scheduler, clock = make_scheduler(rng=random.Random(1))
    task = scheduler.schedule(100.0, lambda: None, jitter=0.4, name="spread")
    waits = {round(task.next_due, 3)}
    assert 60.0 <= task.next_due <= 140.0
    for _ in range(30):
        scheduler.run_task("spread", now=clock.now)
        wait = task.next_due - clock.now
        assert 60.0 <= wait <= 140.0
        waits.add(round(wait, 3))
    assert len(waits) > 10
    assert task.as_dict()["jitter"] == 0.4


def test_jitter_must_be_a_fraction() -> None:
    scheduler, _ = make_scheduler()
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError, match="jitter"):
            scheduler.schedule(10.0, lambda: None, jitter=bad)


def test_window_and_days_move_a_due_run_into_the_slot() -> None:
    wall = [datetime(2026, 11, 3, 12, 0).timestamp()]  # Tuesday noon
    clock = FakeClock()
    scheduler = TaskScheduler(clock=clock, wall_clock=lambda: wall[0], rng=random.Random(3))
    task = scheduler.schedule(
        60.0, lambda: None, window=("02:00", "05:00"), days=("mon",), name="weekly"
    )
    due = datetime.fromtimestamp(wall[0] + (task.next_due - clock.now))
    assert due.weekday() == 0 and 2 <= due.hour < 5
    assert task.constraint is not None and task.as_dict()["constraint"] == "mon 02:00-05:00"


def test_schedule_at_runs_on_the_calendar_not_the_interval() -> None:
    wall = [datetime(2026, 11, 3, 12, 0).timestamp()]
    clock = FakeClock()
    scheduler = TaskScheduler(clock=clock, wall_clock=lambda: wall[0], rng=random.Random(5))
    ran: list[int] = []
    task = scheduler.schedule_at("daily 03:00", lambda: ran.append(1), name="nightly")
    assert abs(task.next_due - 15 * 3600) < 1  # noon -> 03:00 next day
    clock.advance(15 * 3600)
    wall[0] += 15 * 3600
    assert scheduler.run_due() == ["nightly"]
    assert abs(task.next_due - clock.now - 24 * 3600) < 1
    assert "at daily 03:00" in scheduler.describe()
    with pytest.raises(ValueError):
        scheduler.schedule_at("whenever", lambda: None)


def test_context_schedule_at_without_a_scheduler_is_none(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path, scheduler=True))
    assert chassis.context.schedule_at("03:00", lambda: None) is not None
    chassis.context.scheduler = None
    assert chassis.context.schedule_at("03:00", lambda: None) is None


# ---------------------------------------------------------------------------- limiter


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 3, 10, 0).timestamp()
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_limiter(tmp_path: Path, clock: Clock, **kwargs: Any) -> Any:
    registry = LimiterRegistry(tmp_path / "limiters")
    limiter = registry.get("external-api", **kwargs)
    limiter._clock = clock
    limiter._sleep = clock.sleep
    return limiter


def test_limiter_waits_out_the_minimum_gap(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, min_interval=4.0)
    limiter.acquire()
    limiter.acquire()
    assert clock.slept and sum(clock.slept) == pytest.approx(4.0)
    assert limiter.status()["used_today"] == 2


def test_limiter_jitter_only_lengthens_the_gap(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, min_interval=10.0, jitter=0.5)
    limiter.acquire()
    wait = limiter.try_acquire()
    assert 10.0 <= wait <= 15.0


def test_limiter_refuses_past_the_daily_budget_and_resets_at_midnight(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, daily_budget=2)
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(LimiterBlocked) as blocked:
        limiter.acquire()
    assert blocked.value.reason == "budget" and blocked.value.retry_after > 0
    assert limiter.status()["remaining_today"] == 0
    clock.now += 86400
    limiter.acquire()
    assert limiter.status()["used_today"] == 1


def test_cooldown_blocks_and_clears_and_never_shortens(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock)
    until = limiter.cooldown(hours=24, reason="HTTP 429")
    assert limiter.cooldown(seconds=5) == until
    with pytest.raises(LimiterBlocked) as blocked:
        limiter.acquire()
    assert blocked.value.reason == "cooldown"
    status = limiter.status()
    assert status["cooling_down"] and status["cooldown_reason"] == "HTTP 429"
    assert limiter.clear_cooldown() is True
    assert limiter.clear_cooldown() is False
    limiter.acquire()


def test_acquire_timeout_refuses_instead_of_sleeping(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, min_interval=60.0)
    limiter.acquire()
    with pytest.raises(LimiterBlocked) as blocked:
        limiter.acquire(timeout=1.0)
    assert blocked.value.reason == "timeout" and not clock.slept


def test_state_is_shared_through_the_file_and_readable_without_declaring(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, daily_budget=10, min_interval=1.0)
    limiter.acquire(cost=3)
    other = LimiterRegistry(tmp_path / "limiters").get("external-api", declared=False)
    other._clock = clock
    other.cooldown(minutes=10, reason="operator")
    rows = read_limiter_states(tmp_path / "limiters", now=clock.now)
    assert rows[0]["used_today"] == 3 and rows[0]["daily_budget"] == 10  # limits kept
    assert rows[0]["cooling_down"] is True
    with pytest.raises(LimiterBlocked):
        limiter.acquire()


def test_limiter_rejects_negative_limits(tmp_path: Path) -> None:
    registry = LimiterRegistry(tmp_path)
    with pytest.raises(ValueError):
        registry.get("a", min_interval=-1)
    with pytest.raises(ValueError):
        registry.get("b", daily_budget=-1)


def test_kdx_limiter_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "kdx.yml").write_text("core:\n  ipc:\n    enabled: false\n", encoding="utf-8")
    base = ["--home", str(tmp_path)]
    assert main([*base, "limiter", "cooldown", "api", "--hours", "1", "--json"]) == 0
    capsys.readouterr()
    assert main([*base, "limiter", "list", "--json"]) == 0
    assert '"cooling_down": true' in capsys.readouterr().out
    assert main([*base, "limiter", "cooldown", "api"]) == int(ExitCode.USAGE_ERROR)
    assert main([*base, "limiter", "clear", "api", "--json"]) == 0
    assert '"was_cooling_down": true' in capsys.readouterr().out


def test_doctor_reports_a_cooling_limiter(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path))
    chassis.context.limiter("api").cooldown(hours=1, reason="HTTP 429")
    names = [check.message for check in chassis._chassis_diagnostics()]
    assert any("limiter api is cooling down" in message for message in names)


# ---------------------------------------------------------------------------- processes


PY = sys.executable


def test_process_captures_output_and_publishes_the_outcome() -> None:
    bus = EventBus()
    seen: list[Any] = []
    bus.subscribe("core.process.finished", lambda event: seen.append(event.payload))
    supervisor = ProcessSupervisor(bus=bus)
    result = supervisor.run([PY, "-c", "print('out'); import sys; print('err', file=sys.stderr)"])
    assert result.ok and result.stdout == "out" and result.stderr == "err"
    assert seen and seen[0]["ok"] is True


def test_process_timeout_kills_the_child() -> None:
    supervisor = ProcessSupervisor()
    started = time.monotonic()
    result = supervisor.run([PY, "-c", "import time; time.sleep(30)"], timeout=0.4)
    assert result.timed_out and not result.ok
    assert time.monotonic() - started < 10


def test_process_check_raises_with_the_result() -> None:
    supervisor = ProcessSupervisor()
    with pytest.raises(ProcessError) as failed:
        supervisor.run([PY, "-c", "import sys; sys.exit(3)"], check=True)
    assert failed.value.result is not None and failed.value.result.returncode == 3
    with pytest.raises(ProcessError, match="cannot start"):
        supervisor.run(["definitely-not-a-program-xyz"])
    with pytest.raises(TypeError):
        supervisor.run("echo hi")  # type: ignore[arg-type]


def test_process_env_and_cwd(tmp_path: Path) -> None:
    supervisor = ProcessSupervisor()
    code = "import os; print(os.environ['KDX_TEST_VALUE'], os.getcwd())"
    result = supervisor.run([PY, "-c", code], env={"KDX_TEST_VALUE": "42"}, cwd=tmp_path)
    assert result.stdout.split()[0] == "42"


def test_terminate_all_cancels_a_running_child_and_refuses_new_ones() -> None:
    import threading

    supervisor = ProcessSupervisor()
    outcome: list[Any] = []
    worker = threading.Thread(
        target=lambda: outcome.append(supervisor.run([PY, "-c", "import time; time.sleep(30)"]))
    )
    worker.start()
    deadline = time.monotonic() + 5
    while not supervisor.active() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert supervisor.terminate_all(timeout=10) == 1
    worker.join(timeout=10)
    assert outcome and outcome[0].cancelled
    with pytest.raises(ProcessError, match="stopping"):
        supervisor.run([PY, "-c", "pass"])
    supervisor.reopen()
    assert supervisor.run([PY, "-c", "pass"]).ok


def test_context_run_process_goes_through_the_chassis(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path))
    assert chassis.context.run_process([PY, "-c", "print(1)"]).stdout == "1"


# ---------------------------------------------------------------------------- queue


def make_queue(tmp_path: Path) -> tuple[JobQueue, Clock]:
    clock = Clock()
    return JobQueue(tmp_path / "jobs" / "queue.sqlite3", clock=clock), clock


def test_queue_runs_a_task_and_keeps_the_result(tmp_path: Path) -> None:
    queue, _ = make_queue(tmp_path)
    assert queue.tasks() == [] and queue.stats()["queued"] == 0 and not queue.exists
    queue.handle("add", lambda task: {"sum": sum(task.payload)})
    submitted = queue.submit("add", [1, 2, 3], user="ann")
    assert submitted.state == "queued" and submitted.user == "ann"
    settled = queue.run_one()
    assert settled is not None and settled.state == "done" and settled.result == {"sum": 6}
    assert queue.run_one() is None
    assert queue.stats()["done"] == 1


def test_queue_retries_with_backoff_then_fails(tmp_path: Path) -> None:
    queue, clock = make_queue(tmp_path)
    queue.handle("flaky", lambda task: 1 / 0)
    queue.submit("flaky", max_attempts=3)
    first = queue.run_one(backoff=10.0)
    assert first is not None and first.state == "queued" and first.attempts == 1
    assert first.run_after == pytest.approx(clock.now + 10.0)
    assert queue.run_one() is None  # not due yet
    clock.now += 10
    second = queue.run_one(backoff=10.0)
    assert second is not None and second.run_after == pytest.approx(clock.now + 20.0)
    clock.now += 20
    last = queue.run_one(backoff=10.0)
    assert last is not None and last.state == "failed" and "ZeroDivisionError" in str(last.error)
    assert queue.retry(last.id) and not queue.retry(last.id)


def test_permanent_errors_fail_at_once(tmp_path: Path) -> None:
    queue, _ = make_queue(tmp_path)

    def refuse(task: Any) -> None:
        raise PermanentJobError("bad input")

    queue.handle("strict", refuse)
    queue.submit("strict", max_attempts=5)
    settled = queue.run_one()
    assert settled is not None and settled.state == "failed" and settled.attempts == 1


def test_a_process_only_claims_kinds_it_can_run(tmp_path: Path) -> None:
    queue, _ = make_queue(tmp_path)
    queue.submit("foreign")
    queue.handle("mine", lambda task: None)
    assert queue.run_one() is None
    assert queue.get(1) is not None and queue.get(1).state == "queued"  # type: ignore[union-attr]


def test_queue_survives_a_restart_and_recovers_interrupted_tasks(tmp_path: Path) -> None:
    queue, clock = make_queue(tmp_path)
    queue.handle("job", lambda task: None)
    queue.submit("job", max_attempts=2)
    queue.submit("job", max_attempts=1)
    queue.claim()
    queue.claim()  # the process "crashes" with both running
    reopened = JobQueue(queue.path, clock=clock)
    assert reopened.recover() == 2
    states = {task.id: task.state for task in reopened.tasks()}
    assert states == {1: "queued", 2: "failed"}  # task 2 used its only attempt


def test_cancel_and_purge(tmp_path: Path) -> None:
    queue, clock = make_queue(tmp_path)
    queue.handle("job", lambda task: None)
    one, two = queue.submit("job"), queue.submit("job")
    assert queue.cancel(one.id) and not queue.cancel(one.id)
    queue.run_one()
    assert queue.stats()["cancelled"] == 1 and queue.stats()["done"] == 1
    clock.now += 40 * 86400
    assert queue.purge(older_than_days=30) == 2
    assert queue.get(two.id) is None
    with pytest.raises(ValueError):
        queue.submit("  ")
    with pytest.raises(ValueError):
        queue.handle("", lambda task: None)


def test_queue_module_workers_routes_and_cli(tmp_path: Path) -> None:
    config = "modules:\n  job_queue:\n    enabled: true\n    poll: 0.05\n"
    (tmp_path / "kdx.yml").write_text(config, encoding="utf-8")
    chassis = Chassis.create(
        ChassisOptions(
            home=tmp_path,
            environ={},
            cli_overrides={
                "core.log.file_enabled": False,
                "core.ipc.enabled": True,
                "core.ipc.port": 0,
                "core.ipc.token": "sekret",
                "core.scheduler.threaded": True,
            },
            install_signals=False,
        )
    )
    chassis.context.job_queue().handle("echo", lambda task: {"got": task.payload})
    chassis.initialize()
    chassis.start()
    try:
        assert chassis.ipc is not None
        base = str(chassis.ipc.discovery.base_url())  # type: ignore[union-attr]

        def call(route: str, method: str = "GET", payload: object = None) -> tuple[int, Any]:
            return http_call(base, route, token="sekret", method=method, payload=payload)

        status, body = call("/api/v1/queue", "POST", {"kind": "echo", "payload": 5})
        assert status == 202 and body["state"] == "queued"  # type: ignore[index]
        deadline = time.monotonic() + 10
        task: Any = {}
        while time.monotonic() < deadline:
            _, task = call(f"/api/v1/queue?id={body['id']}")  # type: ignore[index]
            if task.get("state") == "done":
                break
            time.sleep(0.05)
        assert task["state"] == "done" and task["result"] == {"got": 5}
        status, listing = call("/api/v1/queue?state=done")
        assert status == 200 and listing["stats"]["done"] == 1
        assert call("/api/v1/queue", "POST", {})[0] == 400
        assert call("/api/v1/queue?state=bogus")[0] == 400
        assert call("/api/v1/queue?id=999")[0] == 404
        _, queued = call("/api/v1/queue", "POST", {"kind": "other", "delay": 3600})
        assert call("/api/v1/queue/cancel", "POST", {"id": queued["id"]})[1]["cancelled"] is True
        assert call("/api/v1/queue/retry", "POST", {"id": queued["id"]})[1]["queued"] is True
    finally:
        chassis.shutdown()


def test_kdx_queue_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = "core:\n  ipc:\n    enabled: false\nmodules:\n  job_queue:\n    enabled: true\n"
    (tmp_path / "kdx.yml").write_text(config, encoding="utf-8")
    base = ["--home", str(tmp_path)]
    assert main([*base, "queue", "list"]) == 0
    assert main([*base, "queue", "submit", "render", "--payload", '{"id": 7}', "--json"]) == 0
    assert '"kind": "render"' in capsys.readouterr().out
    assert main([*base, "queue", "submit", "x", "--payload", "{nope"]) == int(
        ExitCode.USAGE_ERROR
    )
    assert main([*base, "queue", "show", "1", "--json"]) == 0
    assert main([*base, "queue", "show", "99"]) == int(ExitCode.ERROR)
    assert main([*base, "queue", "cancel", "1"]) == 0
    assert main([*base, "queue", "cancel", "1"]) == int(ExitCode.ERROR)
    assert main([*base, "queue", "retry", "1"]) == 0
    assert main([*base, "queue", "list", "--state", "queued", "--json"]) == 0
    assert main([*base, "queue", "purge", "--days", "0"]) == 0


# ---------------------------------------------------------------------------- v0.5.1 follow-ups


def test_new_primitives_are_importable_from_the_package_root() -> None:
    import korodexios_core

    for name in (
        "LimiterBlocked",
        "RateLimiter",
        "LimiterRegistry",
        "ProcessResult",
        "ProcessError",
        "ProcessSupervisor",
        "JobQueue",
        "QueueTask",
        "PermanentJobError",
    ):
        assert name in korodexios_core.__all__ and hasattr(korodexios_core, name)


def test_a_bare_limiter_handle_keeps_the_stored_limits_and_enforces_them(tmp_path: Path) -> None:
    clock = Clock()
    registry = LimiterRegistry(tmp_path / "limiters")
    declared = registry.get("api", min_interval=5.0, jitter=0.2, daily_budget=3)
    declared._clock = clock
    declared.acquire()
    other = LimiterRegistry(tmp_path / "limiters").get("api")  # no limits: read-only
    other._clock = clock
    assert other.status()["daily_budget"] == 3 and other.status()["min_interval"] == 5.0
    assert other.try_acquire() > 0  # the stored 5 s gap applies to the bare handle too
    same = registry.get("api")  # a bare call on the declaring registry changes nothing
    assert same is declared and same.min_interval == 5.0 and same.daily_budget == 3
    registry.get("api", min_interval=1.0)  # a partial redeclaration keeps the rest
    assert declared.daily_budget == 3 and declared.min_interval == 1.0
    registry.get("api", daily_budget=None)  # an explicit None means "no budget"
    assert declared.daily_budget is None


def test_context_limiter_without_limits_does_not_reset_them(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path))
    chassis.context.limiter("api", daily_budget=7)
    chassis.context.limiter("api")
    assert chassis.context.limiter("api").status()["daily_budget"] == 7


def test_finish_counts_the_gap_from_the_end_of_the_call(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, min_interval=10.0)
    limiter.acquire()
    clock.now += 30  # the call ran for 30 s: the gap from its start is long gone
    assert limiter.try_acquire() == 0.0
    clock.now += 30
    limiter.finish()
    assert limiter.try_acquire() == pytest.approx(10.0)


def test_refund_returns_the_budget_and_the_gap(tmp_path: Path) -> None:
    clock = Clock()
    limiter = make_limiter(tmp_path, clock, min_interval=60.0, daily_budget=1)
    limiter.acquire()
    limiter.refund()
    assert limiter.status()["remaining_today"] == 1
    limiter.acquire()  # no wait: the failed call did not count
    assert not clock.slept
