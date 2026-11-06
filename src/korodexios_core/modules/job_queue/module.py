"""Job queue module: worker threads, control-plane routes, and 'kdx queue'."""

from __future__ import annotations

import json
import threading
from typing import Any

from ...cli.render import dump_json, print_table, tr, write_line
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.ipc import IpcRequest, IpcRoute
from ...kernel.metrics import Sample
from ...kernel.queue import STATES, JobQueue, QueueTask
from ...kernel.scheduler import ScheduledTask

DEFAULT_WORKERS = 1
DEFAULT_POLL = 1.0
DEFAULT_BACKOFF = 30.0
DEFAULT_KEEP_DAYS = 30.0
MAX_WORKERS = 32
QUEUE_PATH = "/api/v1/queue"
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
ID_ARG = CliArgument(("task_id",), metavar="ID", type="int", help="Task id (see 'kdx queue list').")


class JobQueueModule(Module):
    """Runs workers over the durable queue and exposes it on the control plane."""

    name = "job_queue"
    description = "Runs the durable work queue: workers, retries, /api/v1/queue and kdx queue."
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._live: list[threading.Thread] = []
        self._stop = threading.Event()
        self._pump_task: ScheduledTask | None = None
        self._arm = threading.Lock()
        self._purge_task: ScheduledTask | None = None
        self._limit = DEFAULT_WORKERS
        self._backoff = DEFAULT_BACKOFF
        self._ran = 0

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "workers",
                    kind=ConfigKind.INT,
                    default=DEFAULT_WORKERS,
                    description="Most tasks this process runs at once (0 runs none).",
                ),
                ConfigField(
                    "poll",
                    kind=ConfigKind.FLOAT,
                    default=DEFAULT_POLL,
                    description="Seconds an idle worker waits before looking for work again.",
                ),
                ConfigField(
                    "backoff",
                    kind=ConfigKind.FLOAT,
                    default=DEFAULT_BACKOFF,
                    description="First retry delay in seconds; it doubles with every attempt.",
                ),
                ConfigField(
                    "keep_days",
                    kind=ConfigKind.FLOAT,
                    default=DEFAULT_KEEP_DAYS,
                    description="Finished tasks older than this many days are purged (0: keep).",
                ),
            ),
        )

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx queue'."""
        surface.add_command(
            CliCommand(
                name="queue",
                help="Durable work queue: list, submit, cancel, retry.",
                module_name=self.name,
                subcommands=(
                    CliCommand(
                        name="list",
                        help="List tasks, newest first.",
                        module_name=self.name,
                        handler=_list,
                        args=(
                            CliArgument(
                                ("--state",), choices=STATES, help="Only tasks in this state."
                            ),
                            CliArgument(
                                ("-n", "--limit"), type="int", default=50, help="How many to show."
                            ),
                            JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="show",
                        help="Show one task with its payload, result and error.",
                        module_name=self.name,
                        handler=_show,
                        args=(ID_ARG, JSON_ARG),
                    ),
                    CliCommand(
                        name="submit",
                        help="Queue a task for the running daemon's handler of that kind.",
                        module_name=self.name,
                        handler=_submit,
                        args=(
                            CliArgument(("kind",), metavar="KIND", help="Handler kind."),
                            CliArgument(
                                ("--payload",),
                                default="null",
                                help="JSON payload, e.g. '{\"id\": 7}'.",
                            ),
                            CliArgument(
                                ("--max-attempts",),
                                dest="max_attempts",
                                type="int",
                                default=3,
                                help="Tries before the task is marked failed.",
                            ),
                            CliArgument(
                                ("--delay",), type="float", default=0.0, help="Seconds to wait."
                            ),
                            JSON_ARG,
                        ),
                    ),
                    CliCommand(
                        name="cancel",
                        help="Cancel a queued task (a running one cannot be recalled).",
                        module_name=self.name,
                        handler=_cancel,
                        args=(ID_ARG, JSON_ARG),
                    ),
                    CliCommand(
                        name="retry",
                        help="Queue a failed or cancelled task again.",
                        module_name=self.name,
                        handler=_retry,
                        args=(ID_ARG, JSON_ARG),
                    ),
                    CliCommand(
                        name="purge",
                        help="Delete finished tasks older than --days.",
                        module_name=self.name,
                        handler=_purge,
                        args=(
                            CliArgument(
                                ("--days",), type="float", default=30.0, help="Age in days."
                            ),
                            JSON_ARG,
                        ),
                    ),
                ),
            )
        )

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Serve the queue: viewers read, operators submit, cancel and retry."""
        return (
            IpcRoute(
                path=QUEUE_PATH,
                handler=self._queue_route,
                methods=("GET", "POST"),
                module=self.name,
                summary="list or submit durable queue tasks",
            ),
            IpcRoute(
                path=f"{QUEUE_PATH}/cancel",
                handler=self._cancel_route,
                methods=("POST",),
                module=self.name,
                summary="cancel a queued task",
            ),
            IpcRoute(
                path=f"{QUEUE_PATH}/retry",
                handler=self._retry_route,
                methods=("POST",),
                module=self.name,
                summary="queue a failed or cancelled task again",
            ),
        )

    def start(self) -> None:
        """Requeue what a crash interrupted; pump the queue once a handler exists.

        A project that registers no handler pays nothing: no scheduled task, no thread.
        Once one does, the scheduler asks for due work every ``poll`` seconds and each
        task that starts runs on a thread of its own.
        """
        super().start()
        context = self.require_context()
        queue = context.job_queue()
        requeued = queue.recover()
        if requeued:
            self.logger.warning("tasks interrupted by a restart were recovered", count=requeued)
        self._stop.clear()
        self._limit = max(0, min(MAX_WORKERS, self.config.get_int("workers", DEFAULT_WORKERS)))
        self._backoff = max(0.0, self.config.get_float("backoff", DEFAULT_BACKOFF))
        queue.on_register = self._arm_pump
        if queue.kinds():
            self._arm_pump()
        context.metrics.collector("job_queue.tasks", lambda: _queue_samples(queue))
        self.logger.info("job queue started", limit=self._limit, path=str(queue.path))

    def _arm_pump(self) -> None:
        """Schedule the pump and the daily purge, once, when the first handler appears."""
        context = self.context
        if context is None or self._stop.is_set():
            return
        with self._arm:
            if self._pump_task is not None:
                return
            queue = context.job_queue()
            poll = max(0.05, self.config.get_float("poll", DEFAULT_POLL))
            self._pump_task = context.schedule(
                poll, lambda: self._pump(queue), name="queue-pump", module=self.name
            )
            keep = self.config.get_float("keep_days", DEFAULT_KEEP_DAYS)
            if keep > 0:
                self._purge_task = context.schedule_at(
                    "daily 04:00-05:00", lambda: self._purge(queue, keep), name="queue-purge"
                )

    def stop(self) -> None:
        """Take no more tasks, let the running ones finish, then join.

        A task still running past the stop timeout stays ``running`` on disk and is
        requeued by the next start.
        """
        self._stop.set()
        context = self.context
        if context is not None:
            context.metrics.remove_collector("job_queue.tasks")
        if context is not None and context.queue is not None:
            context.queue.on_register = None
        scheduler = self.scheduler
        for attr in ("_pump_task", "_purge_task"):
            task = getattr(self, attr)
            setattr(self, attr, None)
            if task is not None and scheduler is not None:
                scheduler.unschedule(task.name)
        budget = max(0.1, self.stop_timeout)
        for thread in list(self._live):
            thread.join(timeout=budget)
        alive = [thread.name for thread in self._live if thread.is_alive()]
        if alive:
            self.logger.warning("queue tasks outlived the stop timeout", tasks=alive)
        self._live = []
        super().stop()

    def workers(self) -> dict[str, threading.Thread]:
        """Report no standing thread: tasks run on short-lived threads of their own."""
        return {}

    def health(self) -> dict[str, Any]:
        """Report queue counts beside the lifecycle block."""
        data = super().health()
        context = self.context
        data["ran"] = self._ran
        data["running_here"] = len([thread for thread in self._live if thread.is_alive()])
        if context is not None:
            data["tasks"] = context.job_queue().stats()
        return data

    def _pump(self, queue: JobQueue) -> None:
        """Start due tasks, up to the limit; cheap and thread-free when nothing is due."""
        if self._stop.is_set() or not queue.kinds() or not queue.exists:
            return
        self._live = [thread for thread in self._live if thread.is_alive()]
        while len(self._live) < self._limit:
            task = queue.claim()
            if task is None:
                return
            thread = threading.Thread(
                target=self._execute, args=(queue, task), name=f"kdx-queue-{task.id}", daemon=True
            )
            self._live.append(thread)
            thread.start()

    def _execute(self, queue: JobQueue, task: QueueTask) -> None:
        """Run one claimed task and report how it ended."""
        try:
            settled = queue.execute(task, backoff=self._backoff)
        except Exception as exc:
            self.logger.error(f"queue task {task.id} could not be settled: {exc}")
            return
        self._ran += 1
        self.logger.info(
            f"task {settled.id} ({settled.kind}) {settled.state}",
            task=settled.id,
            attempts=settled.attempts,
            error=settled.error,
        )
        self.bus.publish(f"core.queue.{settled.state}", settled.as_dict(), source=self.name)

    def _purge(self, queue: JobQueue, days: float) -> None:
        removed = queue.purge(older_than_days=days)
        if removed:
            self.logger.info("finished tasks purged", count=removed, older_than_days=days)

    def _queue_route(self, request: IpcRequest) -> Any:
        queue = self.require_context().job_queue()
        if request.method == "POST":
            return self._submit_route(queue, request)
        task_id = request.argument("id")
        if task_id is not None:
            task = queue.get(_int(task_id))
            return (404, {"error": f"no task {task_id}"}) if task is None else task.as_dict()
        state = request.argument("state")
        if state and state not in STATES:
            return (400, {"error": f"state must be one of {', '.join(STATES)}"})
        limit = _int(request.argument("limit", 50)) or 50
        return {
            "stats": queue.stats(),
            "tasks": [task.as_dict() for task in queue.tasks(state=state, limit=limit)],
        }

    def _submit_route(self, queue: JobQueue, request: IpcRequest) -> Any:
        body = request.payload if isinstance(request.payload, dict) else {}
        kind = str(body.get("kind") or "").strip()
        if not kind:
            return (400, {"error": 'a task needs a "kind"'})
        caller = request.caller
        task = queue.submit(
            kind,
            body.get("payload"),
            max_attempts=_int(body.get("max_attempts", 3)) or 3,
            delay=float(body.get("delay") or 0.0),
            user="" if caller is None else caller.name,
        )
        return (202, task.as_dict())

    def _cancel_route(self, request: IpcRequest) -> Any:
        task_id = _int(request.argument("id"))
        done = self.require_context().job_queue().cancel(task_id)
        return {"id": task_id, "cancelled": done}

    def _retry_route(self, request: IpcRequest) -> Any:
        task_id = _int(request.argument("id"))
        done = self.require_context().job_queue().retry(task_id)
        return {"id": task_id, "queued": done}


def _queue_samples(queue: JobQueue) -> list[Sample]:
    """Return the durable queue as metrics: tasks per state."""
    return [
        Sample("kdx_queue_tasks", float(count), {"state": state}, "gauge",
               "Durable queue tasks by state.")
        for state, count in queue.stats().items()
    ]


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _queue(request: CliRequest) -> JobQueue:
    return request.context.job_queue()


def _print(task: QueueTask) -> None:
    write_line(json.dumps(task.as_dict(), indent=2, sort_keys=True, default=str))


def _list(request: CliRequest) -> int:
    queue = _queue(request)
    tasks = queue.tasks(state=request.option("state"), limit=int(request.option("limit") or 50))
    if request.flag("as_json"):
        dump_json({"stats": queue.stats(), "tasks": [task.as_dict() for task in tasks]})
        return int(ExitCode.SUCCESS)
    if not tasks:
        write_line(f"{TAG_NOTE} " + tr("the queue is empty"))
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [str(t.id), t.kind, t.state, f"{t.attempts}/{t.max_attempts}", t.error or "-"]
            for t in tasks
        ],
        headers=("id", "kind", "state", "attempts", "error"),
    )
    return int(ExitCode.SUCCESS)


def _show(request: CliRequest) -> int:
    task = _queue(request).get(int(request.option("task_id")))
    if task is None:
        write_line(f"{TAG_ERROR} " + tr("no such task"))
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json(task.as_dict())
    else:
        _print(task)
    return int(ExitCode.SUCCESS)


def _submit(request: CliRequest) -> int:
    try:
        payload = json.loads(str(request.option("payload") or "null"))
    except ValueError as exc:
        write_line(f"{TAG_ERROR} " + tr("--payload is not valid JSON: {error}", error=str(exc)))
        return int(ExitCode.USAGE_ERROR)
    task = _queue(request).submit(
        str(request.option("kind")),
        payload,
        max_attempts=int(request.option("max_attempts") or 3),
        delay=float(request.option("delay") or 0.0),
    )
    if request.flag("as_json"):
        dump_json(task.as_dict())
    else:
        write_line(f"{TAG_OK} " + tr("queued task {id} ({kind})", id=task.id, kind=task.kind))
    return int(ExitCode.SUCCESS)


def _report(request: CliRequest, done: bool, message: str) -> int:
    """Print the outcome of cancel or retry; *message* is already translated."""
    if request.flag("as_json"):
        dump_json({"id": int(request.option("task_id")), "changed": done})
    else:
        write_line(f"{TAG_OK if done else TAG_NOTE} {message}")
    return int(ExitCode.SUCCESS if done else ExitCode.ERROR)


def _cancel(request: CliRequest) -> int:
    task_id = int(request.option("task_id"))
    if _queue(request).cancel(task_id):
        return _report(request, True, tr("task {id} cancelled", id=task_id))
    return _report(request, False, tr("task {id} is not queued", id=task_id))


def _retry(request: CliRequest) -> int:
    task_id = int(request.option("task_id"))
    if _queue(request).retry(task_id):
        return _report(request, True, tr("task {id} queued again", id=task_id))
    return _report(request, False, tr("task {id} is not failed or cancelled", id=task_id))


def _purge(request: CliRequest) -> int:
    removed = _queue(request).purge(older_than_days=float(request.option("days") or 0.0))
    if request.flag("as_json"):
        dump_json({"removed": removed})
    else:
        write_line(f"{TAG_OK} " + tr("{count} finished task(s) removed", count=removed))
    return int(ExitCode.SUCCESS)


MODULE = JobQueueModule()
