# job_queue

Task runner, routes and a CLI for the durable work queue (`context.job_queue()`,
`korodexios_core.kernel.queue`). A task is a row in `data/jobs/queue.sqlite3` with a
state (`queued`, `running`, `done`, `failed`, `cancelled`), an attempt count and a
retry with exponential backoff, so long work survives a restart.

## Use from a module

```python
queue = context.job_queue()
queue.handle("render", render_handler)      # handler(task) -> JSON-able result
queue.submit("render", {"id": 7}, max_attempts=3)
```

A handler that raises is retried (30 s, 60 s, 120 s ... capped at an hour) until
`max_attempts`; `PermanentJobError` fails the task at once. The module costs no thread while the queue is idle: the scheduler (`core.scheduler.enabled`)
wakes it every `poll` seconds and each started task gets a short-lived thread. A process claims only the
kinds it has a handler for, so the daemon that registered `render` runs it even when the
task was submitted by `kdx queue submit` or over the control plane.

## Control plane (roles: viewer reads, operator writes)

| Route | Method | Purpose |
| --- | --- | --- |
| `/api/v1/queue` | GET | list (`?state=queued&limit=50`), or one task (`?id=7`) |
| `/api/v1/queue` | POST | submit `{"kind", "payload", "max_attempts", "delay"}` |
| `/api/v1/queue/cancel` | POST | cancel a queued task `{"id": 7}` |
| `/api/v1/queue/retry` | POST | queue a failed or cancelled task again `{"id": 7}` |

## CLI

`kdx queue list|show|submit|cancel|retry|purge` (all accept `--json`).

## Configuration keys (`modules.job_queue.*`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | gate registration at discovery |
| `workers` | `1` | most tasks this process runs at once |
| `poll` | `1.0` | seconds between looks for due work (scheduler task `queue-pump`) |
| `backoff` | `30.0` | first retry delay in seconds, doubled per attempt |
| `keep_days` | `30` | finished tasks older than this are purged daily (0: keep) |
