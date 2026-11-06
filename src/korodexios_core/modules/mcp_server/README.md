# mcp

Serves the project to AI agents over the [Model Context Protocol](https://modelcontextprotocol.io).
Every module command becomes a tool: its name, help, arguments, choices and required role
come from the declarative CLI contract, so a new module command is a new tool with no
extra code. Tools run through the same delegation path as `kdx rpc`, so they are parsed,
refused and audited exactly like a terminal call.

## Transports

| Transport | How | For |
| --- | --- | --- |
| stdio | `kdx mcp serve` | Claude Code, Cursor, VS Code, Windsurf, any desktop agent |
| Streamable HTTP | `POST /api/v1/mcp` on the control plane | n8n "MCP Client Tool", remote agents |

`kdx mcp serve` delegates every call to a running daemon (`kdx run`) when one answers, so
one process owns the work; with no daemon (or `--in-process`) it runs the call in its own
chassis. The HTTP endpoint needs a token (`X-Kdx-Token` or `Authorization: Bearer`).

## Tools and resources

- `kdx_health`, `kdx_logs`, `kdx_config`, `kdx_metrics` - read-only, for the viewer role.
- `kdx_proposal_draft` - writes a core-proposal note locally (never sends anything).
- One tool per runnable module command (`heartbeat status` -> `heartbeat_status`).
  `remote=False` commands are never tools; `danger` commands need the admin role.
- Resources: `kdx://playbook`, `kdx://conventions`, and, when the files exist,
  `kdx://project/agents` (AGENTS.md) and `kdx://project/brief` (docs/BRIEF.md), plus what
  modules offer through an optional `mcp_resources()` hook (the wiki's `kdx://wiki/public`).

The agent acts with `--role` (default `modules.mcp.role`, `operator`). Over HTTP the role is
the token's: a viewer token sees read-only tools only.

## CLI

```bash
kdx mcp serve [--role ROLE] [--in-process]   # what the agent launches
kdx mcp tools [--role ROLE] [--json]    # what the agent would see
kdx mcp config [--client NAME] [--write]  # client configuration (.mcp.json, ...)
```

## Configuration keys (`modules.mcp.*`)

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | gate registration at discovery |
| `http` | `true` | serve `/api/v1/mcp` |
| `role` | `operator` | highest role `kdx mcp serve` acts with |
| `timeout` | `120` | seconds a delegated tool call may take |
