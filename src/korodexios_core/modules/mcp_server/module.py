"""MCP server module: every module command as a tool for AI agents.

Two transports, one tool set:

- ``kdx mcp serve`` speaks MCP over stdio - what Claude Code, Cursor, VS Code, Windsurf
  and other desktop agents launch. With a daemon running it delegates each call to it
  (one process owns the work); without one it runs the call in its own chassis.
- ``POST /api/v1/mcp`` speaks MCP Streamable HTTP on the control plane - what a remote
  agent, an n8n "MCP Client Tool" node, or any HTTP-capable MCP client dials, with the
  same token ('X-Kdx-Token' or 'Authorization: Bearer') every other route takes.

A tool is listed and callable only up to the caller's role, and runs through the same
delegation path as 'kdx rpc', so it is parsed, refused, and audited exactly the same.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...cli.conventions import conventions_text
from ...cli.playbook import agent_playbook
from ...cli.proposal import write_proposal
from ...cli.render import dump_json, print_table, tr, write_line
from ...config.schema import ConfigField, ConfigKind, ConfigSchema
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ...fsio import atomic_write_text
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.ipc import (
    DEFAULT_PORT,
    DISCOVERY_NAME,
    STATUS_PATH,
    IpcDiscovery,
    IpcRequest,
    IpcResponse,
    IpcRoute,
)
from ...kernel.mcp import McpResource, McpServer, McpTool, ToolResult, command_tools
from ...kernel.rpc_output import _RoutedStream
from ...kernel.users import ROLES, Caller, acting_as, current_caller
from ...logging.engine import tail_lines
from ...version import __version__

MCP_PATH = "/api/v1/mcp"
COMMAND_NAME = "mcp"
DEFAULT_ROLE = "operator"
DEFAULT_TIMEOUT = 120.0
MAX_LOG_LINES = 2000
CLIENTS = ("claude", "cursor", "vscode", "windsurf", "n8n")
AGENT_CALLER = "mcp-agent"

INSTRUCTIONS = (
    "This server exposes a korodexios chassis project. Every tool is one 'kdx' command; "
    "its description names the command, the module, and the role it needs. Read the "
    "resource kdx://playbook before changing the project and kdx://conventions for its "
    "rules. Call kdx_health first to see what is loaded and whether a daemon runs. A tool "
    "whose description says 'changes state' acts on the running system: confirm with the "
    "user before calling it. A failing tool returns isError with the command's own output. "
    "Project knowledge lives in the wiki: read kdx://wiki/public (and kdx://wiki/internal) "
    "and use wiki_search before asking. When the korodexios core itself lacks something "
    "many projects would need, call kdx_proposal_draft instead of editing the core; it only "
    "saves a local note for the owner."
)


class McpServerModule(Module):
    """Publishes the MCP endpoint on the control plane and 'kdx mcp' in the terminal."""

    name = "mcp"
    description = "MCP server for AI agents: stdio (kdx mcp serve) and HTTP (/api/v1/mcp)."
    version = "0.1.0"

    def config_schema(self) -> ConfigSchema:
        """Declare the keys this module reads."""
        return ConfigSchema(
            module=self.name,
            fields=(
                ConfigField(
                    "http",
                    kind=ConfigKind.BOOL,
                    default=True,
                    description="Serve MCP Streamable HTTP on /api/v1/mcp.",
                ),
                ConfigField(
                    "role",
                    kind=ConfigKind.STR,
                    default=DEFAULT_ROLE,
                    choices=ROLES,
                    description="Highest role 'kdx mcp serve' acts with (admin opens danger).",
                ),
                ConfigField(
                    "timeout",
                    kind=ConfigKind.FLOAT,
                    default=DEFAULT_TIMEOUT,
                    description="Seconds a delegated tool call may take on the daemon.",
                ),
            ),
        )

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Serve MCP Streamable HTTP; a viewer may connect, tools follow the caller's role."""
        if not self.config.get_bool("http", True):
            return ()
        return (
            IpcRoute(
                path=MCP_PATH,
                handler=self._http_route,
                methods=("POST", "GET", "DELETE"),
                module=self.name,
                role="viewer",
                summary="Model Context Protocol (Streamable HTTP): tools for AI agents",
            ),
        )

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx mcp serve|tools|config'."""
        role_arg = CliArgument(
            ("--role",),
            choices=ROLES,
            help="Highest role the agent acts with [modules.mcp.role, operator].",
        )
        surface.add_command(
            CliCommand(
                name=COMMAND_NAME,
                help="Serve this project to AI agents over the Model Context Protocol.",
                module_name=self.name,
                show_in_wizard=False,
                remote=False,
                subcommands=(
                    CliCommand(
                        name="serve",
                        help="Speak MCP on stdin/stdout (what an agent launches).",
                        module_name=self.name,
                        handler=self._serve_command,
                        remote=False,
                        show_in_wizard=False,
                        args=(
                            role_arg,
                            CliArgument(
                                ("--in-process",),
                                dest="in_process",
                                action="store_true",
                                help="Run tools here even when a daemon is running.",
                            ),
                        ),
                    ),
                    CliCommand(
                        name="tools",
                        help="List the tools an agent would see.",
                        module_name=self.name,
                        handler=self._tools_command,
                        remote=False,
                        show_in_wizard=False,
                        args=(
                            role_arg,
                            CliArgument(
                                ("--json",), dest="as_json", action="store_true",
                                help="Emit JSON.",
                            ),
                        ),
                    ),
                    CliCommand(
                        name="config",
                        help="Print (or write) the MCP client configuration for this project.",
                        module_name=self.name,
                        handler=self._config_command,
                        remote=False,
                        show_in_wizard=False,
                        args=(
                            CliArgument(
                                ("--client",),
                                choices=(*CLIENTS, "all"),
                                default="all",
                                help="Which client to configure [all].",
                            ),
                            CliArgument(
                                ("--write",),
                                dest="write",
                                action="store_true",
                                help="Write .mcp.json, .cursor/mcp.json and .vscode/mcp.json.",
                            ),
                        ),
                    ),
                ),
            )
        )

    # -- tool sets -------------------------------------------------------------------

    def build_server(
        self,
        run: Callable[[str, list[str]], ToolResult],
        *,
        role: Callable[[], str],
        health: Callable[[], Any],
        metrics: Callable[[], Any] | None = None,
    ) -> McpServer:
        """Return an MCP server whose tools run through *run* and whose role is *role*."""
        reader = metrics or (lambda: self.require_context().metrics.as_dict())
        return McpServer(
            tools=lambda: self.tools(run, health, reader),
            resources=self.resources,
            name="korodexios",
            version=__version__,
            instructions=INSTRUCTIONS,
            role=role,
        )

    def tools(
        self,
        run: Callable[[str, list[str]], ToolResult],
        health: Callable[[], Any],
        metrics: Callable[[], Any] | None = None,
    ) -> list[McpTool]:
        """Return the core tools plus one tool per module command."""
        context = self.require_context()
        registry = context.registry
        commands = registry.commands() if registry is not None else []
        core = [
            McpTool(
                name="kdx_health",
                title="Project health",
                description=(
                    "Chassis report: version, loaded modules and their state, configuration "
                    "findings, and whether a daemon is running. Call it first."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                call=lambda _values: _json_result(health()),
                role="viewer",
                read_only=True,
            ),
            McpTool(
                name="kdx_logs",
                title="Recent log lines",
                description="The newest lines of the chassis log file (kdx logs -n N).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "lines": {
                            "type": "integer",
                            "description": "How many lines, newest last [100].",
                            "default": 100,
                        }
                    },
                    "additionalProperties": False,
                },
                call=self._logs_tool,
                role="viewer",
                read_only=True,
            ),
            McpTool(
                name="kdx_config",
                title="Resolved configuration",
                description=(
                    "Every configuration key with its value and the layer it came from "
                    "(secrets masked), like 'kdx config show'."
                ),
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                call=self._config_tool,
                role="viewer",
                read_only=True,
            ),
        ]
        if metrics is not None:
            reader = metrics
            core.append(
                McpTool(
                    name="kdx_metrics",
                    title="Measurements",
                    description=(
                        "Every metric the chassis and its modules record (host CPU, memory and "
                        "disk, queue, jobs, module counters), as on GET /metrics."
                    ),
                    input_schema=_NO_ARGUMENTS,
                    call=lambda _values: _json_result(reader()),
                    role="viewer",
                    read_only=True,
                )
            )
        core.append(
            McpTool(
                name="kdx_proposal_draft",
                title="Draft a core proposal",
                description=(
                    "Write a numbered note in docs/core-proposals/ when the korodexios core "
                    "lacks something many projects would need. Saved on this machine only: "
                    "nothing is sent. Tell the owner; they review it and may run "
                    "'kdx proposal send NNN'. A need of this project alone belongs in a module."
                ),
                input_schema=_PROPOSAL_SCHEMA,
                call=self._proposal_tool,
            )
        )
        return core + command_tools(commands, run, skip=(COMMAND_NAME, "ui"))

    def resources(self) -> list[McpResource]:
        """Return the documents an agent should read before it changes anything."""
        context = self.require_context()
        root = Path(context.paths.root)
        items = [
            McpResource(
                uri="kdx://playbook",
                name="Agent playbook",
                description="How an AI agent works in a project built on the chassis.",
                read=agent_playbook,
            ),
            McpResource(
                uri="kdx://conventions",
                name="Conventions",
                description="The rules every project on the chassis follows.",
                read=conventions_text,
            ),
        ]
        for uri, file_name, label in (
            ("kdx://project/agents", "AGENTS.md", "Project rules for agents (AGENTS.md)"),
            ("kdx://project/brief", "docs/BRIEF.md", "What the project must do (docs/BRIEF.md)"),
        ):
            path = root / file_name
            if path.is_file():
                items.append(
                    McpResource(uri=uri, name=file_name, description=label, read=_reader(path))
                )
        registry = context.registry
        for record in registry.ordered() if registry is not None else []:
            hook = getattr(record.module, "mcp_resources", None)
            if record.module is self or not callable(hook):
                continue
            try:
                offered = hook()
            except Exception as exc:  # one module's hook never hides the others
                self.logger.warning("mcp_resources failed", module=record.spec.name,
                                    error=str(exc))
                continue
            items.extend(item for item in offered or () if isinstance(item, McpResource))
        return items

    def _proposal_tool(self, values: Mapping[str, Any]) -> ToolResult:
        root = Path(self.require_context().paths.root)
        sections = {key: str(values.get(key) or "") for key in PROPOSAL_FIELDS}
        number, path = write_proposal(root, str(values.get("title") or ""), sections)
        text = (
            f"Proposal {number} saved to {path}. Nothing was sent. Tell the owner: they can "
            f"read it with 'kdx proposal show {number}' and send it with "
            f"'kdx proposal send {number}'."
        )
        return (text, {"number": number, "file": str(path), "sent": False}, False)

    def _logs_tool(self, values: Mapping[str, Any]) -> ToolResult:
        try:
            count = int(values.get("lines") or 100)
        except (TypeError, ValueError):
            count = 100
        count = max(1, min(MAX_LOG_LINES, count))
        logs = self.require_context().logs
        log_file = logs.log_file if logs is not None else None
        if log_file is None:
            return ("file logging is disabled (core.log.file_enabled: false)", None, False)
        lines = tail_lines(Path(log_file), count)
        return ("\n".join(lines) or "(the log is empty)", {"file": str(log_file)}, False)

    def _config_tool(self, _values: Mapping[str, Any]) -> ToolResult:
        config = self.require_context().config
        rows = config.describe_rows(redact=True)
        entries = [{"key": key, "value": value, "source": source} for key, value, source in rows]
        text = "\n".join(f"{key} = {value}  ({source})" for key, value, source in rows)
        return (text, {"entries": entries}, False)

    # -- HTTP transport ----------------------------------------------------------------

    def _http_route(self, request: IpcRequest) -> Any:
        """Answer one MCP message posted to the control plane (Streamable HTTP)."""
        if request.method != "POST":
            # No server-initiated stream and no sessions: the spec's answer is 405.
            return IpcResponse.text(
                "this MCP endpoint is stateless; POST JSON-RPC messages",
                status=405,
                headers=(("Allow", "POST"),),
            )
        if request.payload is None or isinstance(request.payload, str):
            return (400, {"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32700, "message": "expected a JSON-RPC body"}})
        chassis = self.require_context().chassis
        caller = request.caller

        def role() -> str:
            return caller.role if caller is not None else "viewer"

        def run(path: str, argv: list[str]) -> ToolResult:
            return self._run_in_chassis(chassis, path, argv, caller)

        server = self.build_server(run, role=role, health=lambda: _status_of(chassis))
        reply = server.handle(request.payload)
        if reply is None:
            return IpcResponse(b"", status=202)
        return reply

    def _run_in_chassis(
        self, chassis: Any, path: str, argv: list[str], caller: Caller | None
    ) -> ToolResult:
        """Run one tool through the chassis' own delegation path, audited like 'kdx rpc'."""
        if chassis is None:
            return ("this process has no chassis to run commands in", None, True)
        outcome = "ok"
        try:
            with acting_as(caller if caller is not None else current_caller()):
                answer = chassis.dispatch_rpc(path, {"argv": argv})
        except (LookupError, PermissionError, TypeError) as exc:
            outcome = f"refused: {exc}"
            return (str(exc), {"error": str(exc)}, True)
        finally:
            audit = getattr(chassis, "audit", None)
            if audit is not None:
                audit.record("mcp", caller=caller, outcome=outcome, command=path, detail=argv)
        return _rpc_result(answer)

    # -- CLI -------------------------------------------------------------------------

    def _role(self, request: CliRequest) -> str:
        chosen = str(request.option("role") or self.config.get_str("role", DEFAULT_ROLE))
        return chosen if chosen in ROLES else DEFAULT_ROLE

    def _stdio_server(self, request: CliRequest) -> McpServer:
        """Return the server 'kdx mcp serve' runs: delegating to a daemon when one lives."""
        context = request.context
        chassis = request.options.get("chassis") or context.chassis
        role = self._role(request)
        agent = Caller(AGENT_CALLER, role, "mcp")
        discovery = None
        if not request.flag("in_process"):
            found = IpcDiscovery.load(Path(context.paths.state_dir) / DISCOVERY_NAME)
            if found is not None and found.is_process_alive():
                discovery = found
        timeout = max(1.0, self.config.get_float("timeout", DEFAULT_TIMEOUT))
        if discovery is not None:
            live = discovery

            def delegated(path: str, argv: list[str]) -> ToolResult:
                try:
                    status, payload = live.call(path, {"argv": argv}, timeout=timeout)
                except OSError as exc:
                    return (f"the daemon did not answer: {exc}", None, True)
                if status == 200 and isinstance(payload, Mapping):
                    return _rpc_result(payload.get("result") or {})
                error = payload.get("error") if isinstance(payload, Mapping) else payload
                return (f"refused ({status}): {error}", {"status": status, "error": error}, True)

            def daemon_metrics() -> Any:
                try:
                    status, payload = live.request("/metrics?format=json", timeout=10.0)
                except OSError as exc:
                    return {"daemon": "unreachable", "error": str(exc)}
                return payload if status == 200 else {"daemon": status, "error": payload}

            def daemon_health() -> Any:
                try:
                    status, payload = live.request(STATUS_PATH, timeout=10.0)
                except OSError as exc:
                    return {"daemon": "unreachable", "error": str(exc)}
                return payload if status == 200 else {"daemon": status, "error": payload}

            return self.build_server(delegated, role=lambda: role, health=daemon_health,
                                     metrics=daemon_metrics)

        def local(path: str, argv: list[str]) -> ToolResult:
            return self._run_in_chassis(chassis, path, argv, agent)

        return self.build_server(
            local, role=lambda: role, health=lambda: _status_of(chassis)
        )

    def _serve_command(self, request: CliRequest) -> int:
        """Serve MCP on stdio until the client closes stdin."""
        server = self._stdio_server(request)
        protocol_out = sys.stdout.inner if isinstance(sys.stdout, _RoutedStream) else sys.stdout
        # Anything else that prints from here on - a stray print, a module - would corrupt
        # the protocol stream, so plain stdout is pointed at stderr for the session.
        previous = sys.stdout
        sys.stdout = sys.stderr
        source = getattr(sys.stdin, "buffer", None)
        self.logger.info("mcp stdio server ready", role=self._role(request))
        try:
            while True:
                raw = source.readline() if source is not None else sys.stdin.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
                if not line.strip():
                    continue
                reply = server.handle_text(line.strip())
                if reply is not None:
                    protocol_out.write(reply + "\n")
                    protocol_out.flush()
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            return int(ExitCode.ABORTED)
        finally:
            sys.stdout = previous
        return int(ExitCode.SUCCESS)

    def _tools_command(self, request: CliRequest) -> int:
        server = self._stdio_server(request)
        listed = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = listed["result"]["tools"]
        if request.flag("as_json"):
            dump_json({"role": self._role(request), "tools": tools})
            return int(ExitCode.SUCCESS)
        print_table(
            [[tool["name"], tool["description"][:90]] for tool in tools],
            headers=("tool", "description"),
        )
        write_line(f"{TAG_NOTE} " + tr("{count} tools for the {role} role", count=len(tools),
                                       role=self._role(request)))
        return int(ExitCode.SUCCESS)

    def _config_command(self, request: CliRequest) -> int:
        context = request.context
        root = Path(context.paths.root).resolve()
        port = int(context.config.get_int("core.ipc.port", DEFAULT_PORT)) or DEFAULT_PORT
        snippets = client_configs(root, port)
        chosen = str(request.option("client") or "all")
        if request.flag("write"):
            written: list[str] = []
            for relative, key in ((".mcp.json", "mcpServers"),
                                  (".cursor/mcp.json", "mcpServers"),
                                  (".vscode/mcp.json", "servers")):
                path = root / relative
                try:
                    merge_server_entry(path, key, "kdx", snippets["entry"][key])
                except (OSError, ValueError) as exc:
                    write_line(f"{TAG_ERROR} " + tr("{path} could not be written: {reason}",
                                                    path=path, reason=exc))
                    return int(ExitCode.ERROR)
                written.append(relative)
            for item in written:
                write_line(f"{TAG_OK} " + tr("{path} created", path=item))
            write_line(f"{TAG_NOTE} " + tr("restart the agent so it picks up the kdx server"))
            return int(ExitCode.SUCCESS)
        for client in CLIENTS if chosen == "all" else (chosen,):
            write_line(f"# {client}")
            write_line(snippets[client])
            write_line()
        return int(ExitCode.SUCCESS)


_NO_ARGUMENTS: dict[str, Any] = {"type": "object", "properties": {},
                                 "additionalProperties": False}
PROPOSAL_FIELDS = ("problem", "where", "change", "why", "impact", "workaround", "implementation")
_PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short title of the proposal."},
        "problem": {"type": "string", "description": "What fails or is missing."},
        "where": {"type": "string", "description": "Command, module or file, and the output."},
        "change": {"type": "string", "description": "What the core should do (contract)."},
        "why": {"type": "string", "description": "Which kinds of projects need it."},
        "impact": {"type": "string", "description": "API, config keys, compatibility."},
        "workaround": {"type": "string", "description": "What the project does meanwhile."},
        "implementation": {"type": "string", "description": "none, or a branch/fork link."},
    },
    "required": ["title", "problem", "change", "why"],
    "additionalProperties": False,
}


def _reader(path: Path) -> Callable[[], str]:
    def read() -> str:
        return path.read_text(encoding="utf-8")

    return read


def _json_result(payload: Any) -> ToolResult:
    structured = payload if isinstance(payload, Mapping) else {"value": payload}
    return (json.dumps(payload, indent=2, default=str), structured, False)


def _rpc_result(answer: Mapping[str, Any]) -> ToolResult:
    """Turn a delegation answer into a tool result: the output, and failure on non-zero."""
    exit_code = int(answer.get("exit_code") or 0)
    output = str(answer.get("output") or "").rstrip()
    structured = {
        "command": answer.get("path") or answer.get("command"),
        "exit_code": exit_code,
        "output": output,
    }
    text = output or f"'kdx {structured['command']}' finished with exit code {exit_code}"
    if exit_code and output:
        text = f"{output}\n(exit code {exit_code})"
    return (text, structured, exit_code != 0)


def _status_of(chassis: Any) -> Any:
    if chassis is None:
        return {"chassis": None}
    ipc = getattr(chassis, "ipc", None)
    if ipc is not None and getattr(ipc, "running", False):
        return ipc.status_snapshot()
    return chassis.health_report()


def stdio_command(root: Path) -> list[str]:
    """Return the command an agent launches: this interpreter, the chassis, this project."""
    return [sys.executable, "-m", "korodexios_core", "--home", str(root), "mcp", "serve"]


def client_configs(root: Path, port: int) -> dict[str, Any]:
    """Return ready-to-paste MCP client configuration for *root*, per client."""
    command = stdio_command(root)
    stdio = {"command": command[0], "args": command[1:]}
    vscode = {"type": "stdio", **stdio}
    entry = {"mcpServers": stdio, "servers": vscode}
    url = f"http://127.0.0.1:{port}{MCP_PATH}"
    claude = json.dumps({"mcpServers": {"kdx": stdio}}, indent=2)
    return {
        "entry": entry,
        "claude": "Write it to .mcp.json (project) or run:\n  claude mcp add kdx -- "
        + " ".join(_quote(part) for part in command) + "\n" + claude,
        "cursor": ".cursor/mcp.json:\n" + json.dumps({"mcpServers": {"kdx": stdio}}, indent=2),
        "vscode": ".vscode/mcp.json:\n" + json.dumps({"servers": {"kdx": vscode}}, indent=2),
        "windsurf": "~/.codeium/windsurf/mcp_config.json:\n"
        + json.dumps({"mcpServers": {"kdx": stdio}}, indent=2),
        "n8n": "\n".join(
            [
                "MCP Client Tool node (with 'kdx run' on the daemon host):",
                f"  Endpoint:        {url}  (use the LAN address n8n can reach)",
                "  Server transport: HTTP Streamable",
                "  Authentication:   Bearer Auth -> core.ipc.token or a 'kdx users add' token",
                "Plain HTTP Request node: POST /api/v1/rpc with",
                '  {"command": "heartbeat status", "args": {}} and the same Bearer token.',
            ]
        ),
    }


def _quote(part: str) -> str:
    return f'"{part}"' if " " in part else part


def merge_server_entry(path: Path, key: str, name: str, entry: Mapping[str, Any]) -> None:
    """Add (or replace) server *name* under *key* in the JSON file *path*, keeping the rest."""
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} does not hold a JSON object")
        data = loaded
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
    servers[name] = dict(entry)
    data[key] = servers
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(data, indent=2) + "\n")


MODULE = McpServerModule()
