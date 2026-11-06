"""Model Context Protocol (MCP): the chassis as a tool server for AI agents.

An agent - Claude Code, Cursor, Windsurf, an n8n AI node, any MCP client - asks a server
which tools it has, then calls them with JSON arguments. The chassis already knows its
tools: every module command is declared through the CLI contract, with its arguments,
types, choices, and the role it needs. This module turns that contract into MCP and
speaks the protocol (JSON-RPC 2.0), with no dependency beyond the standard library.

The protocol core here is transport-free: :class:`McpServer` takes one decoded message
and returns the reply. Two transports carry it: newline-delimited JSON on stdio
(``kdx mcp serve``, what a desktop agent launches) and Streamable HTTP on the control
plane (``POST /api/v1/mcp``, what a remote agent or n8n dials). Both run a tool through
the same delegation path the terminal and the web console use, so a tool call is checked,
audited, and refused exactly like ``kdx rpc`` - an agent gets no right the operator who
issued its token does not have.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from .cli_contract import CliArgument, CliCommand
from .users import ROLES, role_rank

__all__ = [
    "JSONRPC_VERSION",
    "MCP_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "McpError",
    "McpResource",
    "McpServer",
    "McpTool",
    "argv_for",
    "command_tools",
    "input_schema",
    "required_role",
    "tool_name",
]

JSONRPC_VERSION: Final[str] = "2.0"
MCP_PROTOCOL_VERSION: Final[str] = "2025-06-18"
"""The newest MCP revision this server speaks; it is offered to clients that ask for none."""

SUPPORTED_PROTOCOL_VERSIONS: Final[tuple[str, ...]] = (
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
"""Revisions whose tool and resource methods this server answers identically."""

PARSE_ERROR: Final[int] = -32700
INVALID_REQUEST: Final[int] = -32600
METHOD_NOT_FOUND: Final[int] = -32601
INVALID_PARAMS: Final[int] = -32602
INTERNAL_ERROR: Final[int] = -32603

TOOL_NAME_LIMIT: Final[int] = 64
_TOOL_NAME_UNSAFE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_-]+")

_JSON_TYPES: Final[dict[str, str]] = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "flag": "boolean",
    "str": "string",
    "path": "string",
}


class McpError(Exception):
    """A JSON-RPC error with its code; raised by handlers, answered as an error reply."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


ToolCall = Callable[[Mapping[str, Any]], "ToolResult"]
"""Runs one tool with its validated arguments and returns (text, structured, is_error)."""

ToolResult = tuple[str, Any, bool]


@dataclass(frozen=True, slots=True)
class McpTool:
    """One tool: its MCP declaration plus the callable that runs it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    call: ToolCall
    role: str = "operator"
    """The least role that may see and call the tool."""
    read_only: bool = False
    destructive: bool = False
    title: str = ""

    def declaration(self) -> dict[str, Any]:
        """Return the entry 'tools/list' answers with."""
        entry: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "openWorldHint": False,
            },
        }
        if self.title:
            entry["title"] = self.title
            entry["annotations"]["title"] = self.title
        return entry


@dataclass(frozen=True, slots=True)
class McpResource:
    """One readable document an agent may load into its context (rules, status, ...)."""

    uri: str
    name: str
    description: str
    read: Callable[[], str]
    mime_type: str = "text/markdown"

    def declaration(self) -> dict[str, Any]:
        """Return the entry 'resources/list' answers with."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass(slots=True)
class McpServer:
    """The transport-free MCP server: one decoded JSON-RPC message in, one reply out.

    *tools* and *resources* are providers, asked on every list and call, so a module
    loaded or a role changed since the last request is seen at once. *role* is the
    caller's role; a tool above it is neither listed nor callable.
    """

    tools: Callable[[], Sequence[McpTool]]
    resources: Callable[[], Sequence[McpResource]] = lambda: ()
    name: str = "korodexios"
    version: str = "0"
    instructions: str = ""
    role: Callable[[], str] = lambda: "admin"
    initialized: bool = field(default=False)
    protocol_version: str = MCP_PROTOCOL_VERSION

    def handle(self, message: Any) -> Any:
        """Answer one message (or a batch); None for a notification, which gets no reply."""
        if isinstance(message, list):
            replies = [reply for item in message if (reply := self._one(item)) is not None]
            return replies or None
        return self._one(message)

    def handle_text(self, text: str) -> str | None:
        """Decode *text*, answer it, and encode the reply; None when there is nothing to say."""
        try:
            message = json.loads(text)
        except ValueError as exc:
            return json.dumps(_error_reply(None, McpError(PARSE_ERROR, f"parse error: {exc}")))
        reply = self.handle(message)
        return None if reply is None else json.dumps(reply, default=str)

    def _one(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, Mapping) or message.get("jsonrpc") != JSONRPC_VERSION:
            return _error_reply(
                message.get("id") if isinstance(message, Mapping) else None,
                McpError(INVALID_REQUEST, "expected a JSON-RPC 2.0 object"),
            )
        method = message.get("method")
        has_id = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            if has_id and ("result" in message or "error" in message):
                return None  # a reply to something we never asked: nothing to do
            return _error_reply(request_id, McpError(INVALID_REQUEST, "no method"))
        params = message.get("params")
        params = {} if params is None else params
        if not isinstance(params, Mapping):
            return _error_reply(request_id, McpError(INVALID_PARAMS, "params must be an object"))
        try:
            result = self._dispatch(method, params)
        except McpError as exc:
            return _error_reply(request_id, exc) if has_id else None
        except Exception as exc:  # a tool provider failed: the client still gets an answer
            error = McpError(INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
            return _error_reply(request_id, error) if has_id else None
        if not has_id:
            return None
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    def _dispatch(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method.startswith("notifications/"):
            if method == "notifications/initialized":
                self.initialized = True
            return {}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [tool.declaration() for tool in self._visible_tools()]}
        if method == "tools/call":
            return self._call_tool(params)
        if method == "resources/list":
            return {"resources": [item.declaration() for item in self.resources()]}
        if method == "resources/templates/list":
            return {"resourceTemplates": []}
        if method == "resources/read":
            return self._read_resource(params)
        if method == "prompts/list":
            return {"prompts": []}
        raise McpError(METHOD_NOT_FOUND, f"method {method!r} is not supported")

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        asked = str(params.get("protocolVersion") or "")
        self.protocol_version = asked if asked in SUPPORTED_PROTOCOL_VERSIONS else (
            MCP_PROTOCOL_VERSION
        )
        result: dict[str, Any] = {
            "protocolVersion": self.protocol_version,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
                "prompts": {"listChanged": False},
            },
            "serverInfo": {"name": self.name, "version": self.version},
        }
        if self.instructions:
            result["instructions"] = self.instructions
        return result

    def _visible_tools(self) -> list[McpTool]:
        role = self.role()
        return [tool for tool in self.tools() if role_rank(role) >= role_rank(tool.role) >= 0]

    def _call_tool(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            raise McpError(INVALID_PARAMS, "arguments must be an object")
        tool = next((item for item in self._visible_tools() if item.name == name), None)
        if tool is None:
            raise McpError(INVALID_PARAMS, f"unknown tool {name!r}; call tools/list")
        problem = _check_arguments(tool.input_schema, arguments)
        if problem:
            return _tool_result(problem, None, True)
        try:
            text, structured, is_error = tool.call(arguments)
        except Exception as exc:
            # A tool failure is the tool's answer, not a protocol error: the agent reads it.
            return _tool_result(f"{type(exc).__name__}: {exc}", None, True)
        return _tool_result(text, structured, is_error)

    def _read_resource(self, params: Mapping[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri") or "")
        for resource in self.resources():
            if resource.uri == uri:
                return {
                    "contents": [
                        {"uri": uri, "mimeType": resource.mime_type, "text": resource.read()}
                    ]
                }
        raise McpError(INVALID_PARAMS, f"unknown resource {uri!r}; call resources/list")


def _tool_result(text: str, structured: Any, is_error: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": text or ("error" if is_error else "ok")}],
        "isError": bool(is_error),
    }
    if isinstance(structured, Mapping):
        result["structuredContent"] = dict(structured)
    return result


def _error_reply(request_id: Any, error: McpError) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error.as_dict()}


def _check_arguments(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> str:
    """Return why *arguments* do not fit *schema*, or an empty string.

    Only what a CLI argument can express is checked - known names, required ones,
    scalar types, enums, arrays of strings - so the agent hears the mistake in words
    before anything runs, and the command's own parser still has the last word.
    """
    properties: Mapping[str, Any] = schema.get("properties") or {}
    for name in schema.get("required") or ():
        if arguments.get(name) in (None, ""):
            return f"missing required argument {name!r}"
    for name, value in arguments.items():
        spec = properties.get(name)
        if spec is None:
            known = ", ".join(sorted(properties)) or "none"
            return f"unknown argument {name!r} (this tool accepts: {known})"
        if value is None:
            continue
        expected = spec.get("type")
        if not _fits(expected, value, spec):
            return f"argument {name!r} must be {expected}, got {type(value).__name__}"
        choices = spec.get("enum")
        if choices and str(value) not in {str(choice) for choice in choices}:
            return f"argument {name!r} must be one of {', '.join(map(str, choices))}"
    return ""


def _fits(expected: Any, value: Any, spec: Mapping[str, Any]) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list) and all(
            isinstance(item, str | int | float) and not isinstance(item, bool) for item in value
        )
    return isinstance(value, str | int | float) and not isinstance(value, bool)


# -- the CLI contract as tools ---------------------------------------------------------


def tool_name(path: str) -> str:
    """Return the MCP tool name for a command path: 'backup run' -> 'backup_run'."""
    clean = _TOOL_NAME_UNSAFE.sub("_", str(path).strip()).strip("_")
    return (clean or "command")[:TOOL_NAME_LIMIT]


def required_role(command: CliCommand, inherited: str = "operator") -> str:
    """Return the role a command needs: its own, 'admin' for danger, else the inherited one."""
    own = command.role or ("admin" if command.danger else "operator")
    return max(inherited, own, key=role_rank)


def _is_flag(argument: CliArgument) -> bool:
    return argument.type == "flag" or argument.action in ("store_true", "store_false")


def _is_list(argument: CliArgument) -> bool:
    return argument.nargs in ("*", "+") or isinstance(argument.nargs, int) or (
        argument.action == "append"
    )


def _property(argument: CliArgument) -> dict[str, Any]:
    spec: dict[str, Any]
    if _is_flag(argument):
        spec = {"type": "boolean"}
    elif _is_list(argument):
        spec = {"type": "array", "items": {"type": _JSON_TYPES.get(argument.type, "string")}}
    else:
        spec = {"type": _JSON_TYPES.get(argument.type, "string")}
    if argument.choices and spec["type"] != "array":
        spec["enum"] = list(argument.choices)
    description = str(argument.help or "").strip()
    if argument.metavar and not description:
        description = str(argument.metavar)
    if description:
        spec["description"] = description
    default = argument.default
    if default is not None and not _is_flag(argument):
        spec["default"] = default if isinstance(default, str | int | float | bool) else str(default)
    return spec


def _argument_required(argument: CliArgument) -> bool:
    if _is_flag(argument):
        return False
    if argument.required:
        return True
    return argument.is_positional and argument.default is None and argument.nargs not in (
        "?", "*"
    )


def input_schema(arguments: Iterable[CliArgument]) -> dict[str, Any]:
    """Return the JSON Schema of a command's declared arguments (an MCP inputSchema)."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for argument in arguments:
        dest = argument.destination()
        properties[dest] = _property(argument)
        if _argument_required(argument):
            required.append(dest)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def argv_for(arguments: Iterable[CliArgument], values: Mapping[str, Any]) -> list[str]:
    """Turn tool arguments into the tokens an operator would type after the command.

    Going through argv rather than a namespace means the command's own parser checks
    the call - positionals in order, required options, types, and choices - exactly as
    it does in the terminal.
    """
    declared = list(arguments)
    tokens: list[str] = []
    for argument in declared:
        if not argument.is_positional:
            continue
        value = values.get(argument.destination())
        if value is None:
            continue
        tokens.extend(_strings(value))
    for argument in declared:
        if argument.is_positional:
            continue
        value = values.get(argument.destination())
        if value is None:
            continue
        flag = next(
            (item for item in argument.flags if str(item).startswith("--")), argument.flags[0]
        )
        if _is_flag(argument):
            wanted = argument.action != "store_false"
            if bool(value) == wanted:
                tokens.append(str(flag))
            continue
        if argument.action == "append":
            for item in _strings(value):
                tokens.extend([str(flag), item])
            continue
        tokens.append(str(flag))
        tokens.extend(_strings(value))
    return tokens


def _strings(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [_scalar(item) for item in value]
    return [_scalar(value)]


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


CommandRunner = Callable[[str, list[str]], ToolResult]
"""Runs a command path with argv tokens and returns the tool result."""


def _bound_call(
    run: CommandRunner, path: str, arguments: Sequence[CliArgument]
) -> ToolCall:
    """Return the tool callable for one command path (a closure per path, not per loop)."""

    def call(values: Mapping[str, Any]) -> ToolResult:
        return run(path, argv_for(arguments, values))

    return call


def command_tools(
    commands: Iterable[CliCommand],
    run: CommandRunner,
    *,
    skip: Iterable[str] = (),
    allow_danger: bool = True,
) -> list[McpTool]:
    """Return one tool per runnable, remote-allowed module command.

    A command a module kept off the control plane (``remote=False``) is never a tool,
    nor is a group, a wizard-hidden launcher, or a top-level name in *skip*. A command
    marked ``danger`` is a tool only with *allow_danger*, and needs the admin role.
    """
    skipped = set(skip)
    tools: list[McpTool] = []

    def visit(command: CliCommand, path: str, remote: bool, danger: bool, role: str) -> None:
        if not command.show_in_wizard:
            return
        remote = remote and command.remote
        danger = danger or command.danger
        role = required_role(command, role)
        if command.handler is not None and remote and (allow_danger or not danger):
            module = command.module_name or path.split(" ", 1)[0]
            summary = str(command.help or "").strip() or f"Run 'kdx {path}'."
            arguments = tuple(command.args)
            tools.append(
                McpTool(
                    name=tool_name(path),
                    title=f"kdx {path}",
                    description=(
                        f"{summary} (kdx {path}; module {module}; needs the {role} role"
                        + ("; changes state" if danger else "")
                        + ")"
                    ),
                    input_schema=input_schema(arguments),
                    call=_bound_call(run, path, arguments),
                    role=role if role in ROLES else "operator",
                    destructive=danger,
                )
            )
        for child in command.subcommands:
            visit(child, f"{path} {child.name}", remote, danger, role)

    for top in commands:
        if top.name in skipped:
            continue
        visit(top, top.name, True, False, "operator")
    seen: set[str] = set()
    unique: list[McpTool] = []
    for tool in tools:
        if tool.name not in seen:
            seen.add(tool.name)
            unique.append(tool)
    return unique
