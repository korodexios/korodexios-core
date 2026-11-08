# Integrations: AI agents, automation tools, live clients

A chassis project talks to the outside world through its control plane (`kdx run` starts
it; it binds `127.0.0.1:18123` by default). Every integration below uses the same token,
the same roles, and the same audit log as the CLI and the web console - nothing opens a
second door.

| You want | Use | Route / command | Least role |
| --- | --- | --- | --- |
| An AI agent (Claude Code, Cursor, VS Code, Windsurf, ...) to use the project | MCP over stdio | `kdx mcp serve` | operator (configurable) |
| A remote agent or an n8n AI node to use it | MCP over HTTP | `POST /api/v1/mcp` | viewer (tools follow the role) |
| n8n / Zapier / Make to react to what happens | outbound webhooks | `modules.webhooks.targets` | - |
| n8n / Zapier / Make to tell the project something | inbound webhook | `POST /api/v1/webhooks/inbound` | contributor |
| n8n / a script to run a command | RPC | `POST /api/v1/rpc` | operator |
| A dashboard or bot with live events and commands | WebSocket | `GET /api/v1/ws` | viewer |
| A read-only live feed | server-sent events | `GET /api/v1/ui/events` | viewer |
| Prometheus / Grafana / VictoriaMetrics to scrape measurements | metrics | `GET /metrics` | viewer |
| Home Assistant / a script to read measurements | metrics as JSON | `GET /metrics?format=json` | viewer |

## Tokens and roles

Hand every tool its own named token, with the least role it needs, so the audit log says
who did what and a leaked token can be rotated alone:

```bash
kdx users add n8n-inbound --role contributor   # may only drop events in
kdx users add n8n-ops --role operator          # may run commands
kdx users add grafana --role viewer            # may only read
kdx users rotate n8n-ops                # replace a token; the old one stops at once
```

A request presents its token as `Authorization: Bearer <token>` (what n8n, Zapier, Make and
MCP clients send by default) or `X-Kdx-Token: <token>`. Never put a token in a URL. A
browser WebSocket, which cannot set headers, offers it as the subprotocol
`kdx.token.<token>` next to `kdx`.

To reach the daemon from another machine (an n8n container, a VPS), bind it to the LAN and
list the names clients dial:

```yaml
# kdx.local.yml
core:
  ipc:
    host: 0.0.0.0
    allowed_hosts: [n8n-host.lan, box.tailnet.ts.net]   # IP addresses always work
    tls: true            # 'kdx tls init' creates the certificate
```

## MCP (Model Context Protocol)

Every module command is a tool: its name (`backup plan` -> `backup_plan`), description,
arguments, choices and required role come from the declarative CLI contract, so a module
you add is a tool with no extra code. Three read-only tools are always there:
`kdx_health`, `kdx_logs`, `kdx_config` (secrets masked). Resources: `kdx://playbook`,
`kdx://conventions`, and `kdx://project/agents` / `kdx://project/brief` when the project
has them. A command marked `danger` is a tool only for the admin role and is annotated
`destructiveHint`; a command marked `remote=False` is never a tool.

```bash
kdx mcp tools                 # what an agent would see (--role admin to include danger)
kdx mcp config --write        # .mcp.json (Claude Code), .cursor/mcp.json, .vscode/mcp.json
kdx mcp config --client n8n   # what to paste into n8n
claude mcp add kdx -- kdx --home /path/to/project mcp serve
```

`kdx mcp serve` delegates each call to the running daemon when there is one, so work is
done by one process; without a daemon (or with `--in-process`) it runs the call itself.

**n8n:** add an *MCP Client Tool* node to an AI Agent, endpoint
`http://HOST:18123/api/v1/mcp`, transport *HTTP Streamable*, authentication *Bearer* with a
`kdx users add` token. The agent then sees exactly the tools that token's role allows.

## Outbound webhooks (the project tells n8n)

```yaml
# kdx.local.yml - targets are secret (the URL is the key), keep them out of kdx.yml
modules:
  webhooks:
    secret: "a-long-random-string"          # signs every delivery
    targets:
      - "https://n8n.example.com/webhook/7f3c"                   # every event
      - "core.job.** https://hooks.zapier.com/hooks/catch/1/abc"  # only jobs
      - "backup.** https://ntfy.sh/my-backups"
```

Each matching bus event is POSTed as JSON from a background worker with a bounded queue
and up to `retries` attempts (backoff 1 s, 2 s, 4 s ...; a 4xx other than 408/429 is not
retried):

```json
{"event": "core.job.finished", "source": "chassis", "at": 1762430000.123,
 "sequence": 42, "delivery": "0c9e...", "payload": {"id": "job-3", "exit_code": 0}}
```

Headers: `X-Kdx-Event`, `X-Kdx-Delivery`, `X-Kdx-Timestamp`, and with a secret
`X-Kdx-Signature: sha256=<hex>` = HMAC-SHA256 of `<timestamp>.<raw body>`. Verify it in an
n8n *Code* node (set the Webhook node to keep the raw body):

The signature and the five-minute timestamp window stop forgery and stale deliveries, not a replay inside the window: keep the `X-Kdx-Delivery` ids you have seen and drop a repeat.

```javascript
const crypto = require('crypto');
const stamp = $json.headers['x-kdx-timestamp'];
const body = $binary.data ? Buffer.from($binary.data.data, 'base64').toString() : JSON.stringify($json.body);
const expected = 'sha256=' + crypto.createHmac('sha256', 'a-long-random-string')
  .update(`${stamp}.${body}`).digest('hex');
if (expected !== $json.headers['x-kdx-signature'] || Math.abs(Date.now() / 1000 - stamp) > 300) {
  throw new Error('not a genuine delivery');
}
return $input.all();
```

In Python the same check is `korodexios_core.modules.webhooks.module.verify(secret,
timestamp, body, signature)`.

```bash
kdx webhooks list         # targets with their URL masked
kdx webhooks test         # one signed test event to every target, with the answer
kdx doctor                # flags invalid targets, plain http off this machine, no secret
```

Common event names: `core.job.started|finished`, `core.queue.done|failed`,
`core.config.reloaded`, `core.process.finished`, and whatever your modules publish.

## Inbound webhooks (n8n tells the project)

An *HTTP Request* node, method POST, Bearer token of a **contributor**:

```text
POST http://HOST:18123/api/v1/webhooks/inbound?event=order.paid
{"id": 9, "amount": 120}
```

The chassis publishes `inbound.order.paid` with `{"payload": {...}, "by": "n8n-inbound"}`
and answers `202 {"accepted": true, "event": "inbound.order.paid", "handlers": 1}`. A
module reacts by subscribing:

```python
self.bus.subscribe("inbound.order.**", self._on_order)
```

The body may also be `{"event": "order.paid", "payload": {...}}`. A contributor token can
publish events and nothing else; running commands needs `/api/v1/rpc` or MCP with an
operator token.

## Running commands (RPC)

```text
POST /api/v1/rpc
{"command": "backup plan", "args": {"argv": ["--json"]}}
{"command": "backup run", "background": true}          -> 202 with a job; GET /api/v1/jobs?id=job-1
```

## WebSocket (`/api/v1/ws`)

One connection, small JSON messages both ways:

```text
-> {"type": "subscribe", "pattern": "core.job.**", "id": 1}
<- {"type": "subscribed", "pattern": "core.job.**", "id": 1}
<- {"type": "event", "name": "core.job.finished", "payload": {...}, "sequence": 7}
-> {"type": "rpc", "id": 2, "command": "heartbeat status"}
<- {"type": "result", "id": 2, "status": 200, "ok": true, "result": {"exit_code": 0, ...}}
-> {"type": "publish", "event": "kiosk.button", "payload": {"n": 1}}   (contributor)
<- {"type": "published", "event": "inbound.kiosk.button", "handlers": 1}
-> {"type": "ping"}   <- {"type": "pong"}
```

Patterns: `a.b` exact, `a.*` one segment, `a.**` any depth, `*` everything; at most 16
subscriptions per connection. Every message is checked against the connection's role, and
every command is audited (`ws-rpc`). A browser connects with
`new WebSocket("ws://127.0.0.1:18123/api/v1/ws", ["kdx", "kdx.token." + token])`.

## Measurements (Prometheus, Grafana, Home Assistant, MQTT)

The chassis keeps one metrics registry (`context.metrics`). Built in: `kdx_info`,
`kdx_uptime_seconds`, `kdx_modules{state}`, `kdx_ipc_requests_total`, `kdx_jobs_running`,
`kdx_queue_tasks{state}`, `kdx_bus_events_published_total`, process memory and threads, and
from `system_info` the host: `kdx_host_cpu_cores`, `kdx_host_load_average{window}`,
`kdx_host_memory_total_bytes|available_bytes`, `kdx_host_disk_total|used|free_bytes{path}`,
`kdx_host_uptime_seconds` (Linux, macOS and Windows, standard library only). A module adds
its own:

```python
orders = context.metrics.counter("shop_orders_total", "Orders taken.")
orders.inc(channel="web")
context.metrics.gauge("boiler_temperature_celsius", "Boiler water.").set(61.5)
with context.metrics.summary("render_seconds", "Render time.").time():
    render()
context.metrics.collector("sensors", read_sensors)   # called at every scrape
```

```bash
kdx metrics                       # table (from the running daemon when there is one)
kdx metrics --prometheus          # the text Prometheus reads
kdx metrics --json --filter kdx_host
```

**Prometheus** (`prometheus.yml`), with a viewer token:

```yaml
scrape_configs:
  - job_name: korodexios
    metrics_path: /metrics
    authorization: {type: Bearer, credentials_file: /etc/prometheus/kdx.token}
    static_configs: [{targets: ["box.lan:18123"]}]
```

Grafana then reads Prometheus as usual. `modules.metrics.auth: false` opens `/metrics`
without a token on a trusted network.

**Home Assistant** (`configuration.yaml`), a REST sensor on the JSON form:

```yaml
rest:
  - resource: http://box.lan:18123/metrics?format=json
    headers: {Authorization: !secret kdx_bearer}      # "Bearer <viewer token>"
    scan_interval: 60
    sensor:
      - name: Box free disk
        value_template: "{{ (value_json['values']['kdx_host_disk_free_bytes{path=.}'] / 1e9) | round(1) }}"
        unit_of_measurement: GB
```

**MQTT** and other push targets: set `modules.metrics.snapshot_interval: 60` and the chassis
publishes `core.metrics.snapshot` (every sample) on the bus each minute. Forward it with a
webhook target (`core.metrics.snapshot https://n8n.example/webhook/metrics`, then an n8n
MQTT node), or with a small project module that subscribes to it and publishes with the
MQTT client the project already uses. The core ships no MQTT client: most projects do not
need one, and the bus event is the one hook a bridge needs.

## Project wiki (LLM-wiki layout)

`docs/wiki/` (public, committed) and `docs-dev/wiki/` (internal, git-ignored) each follow the
LLM-wiki pattern - immutable raw sources, a wiki an agent maintains, and a schema - so either
can be opened as an Obsidian vault, maintained the LLM-wiki way (ingest, query, lint),
pointed at an LLM-wiki you already have (`modules.wiki.public_dir`), or merged into one:

```text
docs/wiki/
  SCHEMA.md      conventions (written once, then yours)     raw/        immutable sources
  index.md       generated catalog of [[wikilinks]]           sources/    one page per source
  log.md         "## [YYYY-MM-DD] <op> | <description>"       concepts/ entities/ synthesis/
  home.md        a page in the default language               decisions/ guides/
  home.sk.md     the same page in Slovak
```

A page header is the LLM-wiki one - `type`, `title`, `tags: [a, b]`, `sources: [name]`,
`summary`, `created`, `updated` (a source page: `raw`, `ingested`) - and pages link each other
as `[[name]]`: the file name without `.md` and without the language suffix.

```bash
kdx wiki init                          # SCHEMA.md, raw/, log.md; internal wiki git-ignored
kdx wiki ingest article.pdf --tags ml  # raw/article.pdf + sources/article.md to fill in
kdx wiki new attention --type concept --sources article --tags ml
kdx wiki new attention --lang sk --type concept --title "Pozornosť"
kdx wiki log update "linked attention from home"
kdx wiki search attention              # also the MCP tool wiki_search
kdx wiki index                         # index.md: what an agent reads first
kdx wiki check [--record]              # links, sources, orphans, raw files, translations, size
```

`modules.wiki.languages: [en, sk, de]` lists the languages a page should have (a source summary
stays in its source's language). Agents read `kdx://wiki/public`, `kdx://wiki/public/schema`
and the internal pair as MCP resources.

### Merging into a wiki you already run

```bash
kdx wiki export ~/brain/wiki --namespace myapp --dry-run   # the plan, nothing written
kdx wiki export ~/brain/wiki --namespace myapp --prefix myapp
kdx wiki export --format json > wiki.json                  # one bundle for an API push
```

The pages land in `<wiki>/myapp/`; raw sources go where the target keeps its own (beside
`wiki/` in an LLM-wiki: `<base>/raw/myapp/`, and each source page's `raw:` points there). The
target's `index.md` gets one `[[myapp-index]]` link and its `log.md` one `import` line - each
written once. Because wikilinks are global by name, a page name the target already uses stops
the export; `--prefix` renames every exported page (`deploy` -> `myapp-deploy`) and rewrites
links and `sources`. A manifest (`.kdx-export.json`) makes the next export update what
changed, remove what the project deleted, and keep any file edited in the target since.
`--internal` exports the internal wiki and says so. Wikis behind an API (Wiki.js, BookStack,
Outline, Confluence, MediaWiki) take the JSON bundle through an n8n flow or a small script.

## Improving the core from a project

A project never edits its installed core. When an agent finds a gap that many projects
share, it drafts a proposal (`kdx proposal new`, or the MCP tool `kdx_proposal_draft`); the
note stays in `docs/core-proposals/`. The owner reads it and decides:

```bash
kdx proposal export 001            # the exact text (the project name is left out)
kdx proposal send 001              # lists similar issues, asks, creates it with gh
```

The maintainers' side - one issue per idea, accepted proposals, pull requests with tests -
is in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Security checklist

- One named token per tool, least role, rotate on suspicion (`kdx users rotate`).
- Keep `allowed_hosts` and TLS on for anything off `127.0.0.1`.
- Webhook targets and secrets in `kdx.local.yml` or `KDX_MODULES__WEBHOOKS__*`, never in
  `kdx.yml` or git; `kdx config show` and the web console mask them.
- Inbound payloads are data: validate them in the module that subscribes.
- `kdx audit` shows every command, job, MCP call and WebSocket command with its caller.
