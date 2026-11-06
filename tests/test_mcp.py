"""MCP: the protocol core, the CLI contract as tools, and both transports."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from korodexios_core.cli.entry import main
from korodexios_core.kernel.chassis import Chassis
from korodexios_core.kernel.cli_contract import CliArgument, CliCommand
from korodexios_core.kernel.mcp import (
    INVALID_PARAMS,
    MCP_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    McpResource,
    McpServer,
    McpTool,
    argv_for,
    command_tools,
    input_schema,
    tool_name,
)
from korodexios_core.modules.mcp_server.module import (
    MCP_PATH,
    McpServerModule,
    client_configs,
    merge_server_entry,
)

from .test_ipc import base_args, ipc_chassis


def rpc(method: str, params: dict[str, Any] | None = None, ident: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
    if params is not None:
        message["params"] = params
    return message


def echo_tool(role: str = "operator", **extra: Any) -> McpTool:
    return McpTool(
        name="echo",
        description="echo",
        input_schema=input_schema(
            (
                CliArgument(("word",)),
                CliArgument(("--count",), type="int"),
                CliArgument(("--mode",), choices=("a", "b")),
            )
        ),
        call=lambda values: (json.dumps(dict(values)), dict(values), False),
        role=role,
        **extra,
    )


# -- protocol core ----------------------------------------------------------------------


def test_initialize_negotiates_a_version_and_names_the_server() -> None:
    server = McpServer(tools=lambda: [], name="x", version="9", instructions="read me")
    known = server.handle(rpc("initialize", {"protocolVersion": "2025-03-26"}))["result"]
    assert known["protocolVersion"] == "2025-03-26"
    assert known["serverInfo"] == {"name": "x", "version": "9"}
    assert known["instructions"] == "read me"
    future = server.handle(rpc("initialize", {"protocolVersion": "2099-01-01"}))["result"]
    assert future["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.initialized
    assert server.handle(rpc("ping"))["result"] == {}
    assert server.handle(rpc("prompts/list"))["result"] == {"prompts": []}
    assert server.handle(rpc("resources/templates/list"))["result"] == {"resourceTemplates": []}


def test_protocol_errors_have_their_codes() -> None:
    server = McpServer(tools=lambda: [])
    assert server.handle(rpc("nope"))["error"]["code"] == METHOD_NOT_FOUND
    assert server.handle({"id": 1, "method": "ping"})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 1})["error"]["code"] == -32600
    assert server.handle({"jsonrpc": "2.0", "id": 1, "params": [], "method": "ping"})[
        "error"
    ]["code"] == INVALID_PARAMS
    assert server.handle({"jsonrpc": "2.0", "id": 4, "result": {}}) is None  # a stray reply
    assert server.handle({"jsonrpc": "2.0", "method": "nope"}) is None  # notification: silent
    parsed = json.loads(server.handle_text("{not json") or "{}")
    assert parsed["error"]["code"] == -32700
    batch = server.handle([rpc("ping", ident=1), {"jsonrpc": "2.0", "method": "x/y"}])
    assert [item["id"] for item in batch] == [1]
    assert server.handle([{"jsonrpc": "2.0", "method": "notifications/x"}]) is None
    assert server.handle_text(json.dumps({"jsonrpc": "2.0", "method": "notifications/x"})) is None


def test_tools_are_filtered_by_role_and_checked_before_they_run() -> None:
    role = {"value": "viewer"}
    server = McpServer(tools=lambda: [echo_tool()], role=lambda: role["value"])
    assert server.handle(rpc("tools/list"))["result"]["tools"] == []
    hidden = server.handle(rpc("tools/call", {"name": "echo", "arguments": {"word": "x"}}))
    assert hidden["error"]["code"] == INVALID_PARAMS
    role["value"] = "operator"
    listed = server.handle(rpc("tools/list"))["result"]["tools"]
    assert listed[0]["name"] == "echo"
    assert listed[0]["inputSchema"]["required"] == ["word"]
    assert listed[0]["annotations"]["readOnlyHint"] is False

    def call(arguments: Any) -> dict[str, Any]:
        return server.handle(rpc("tools/call", {"name": "echo", "arguments": arguments}))[
            "result"
        ]

    ok = call({"word": "hi", "count": 2, "mode": "a"})
    assert ok["isError"] is False and ok["structuredContent"]["count"] == 2
    assert "missing required" in call({})["content"][0]["text"]
    assert "unknown argument" in call({"word": "x", "nope": 1})["content"][0]["text"]
    assert "must be integer" in call({"word": "x", "count": "2"})["content"][0]["text"]
    assert "must be one of" in call({"word": "x", "mode": "z"})["content"][0]["text"]
    assert call({"word": "x", "count": None})["isError"] is False
    not_object = server.handle(rpc("tools/call", {"name": "echo", "arguments": [1]}))
    assert not_object["error"]["code"] == INVALID_PARAMS


def test_a_failing_tool_is_an_answer_not_a_protocol_error() -> None:
    def boom(_values: Any) -> Any:
        raise RuntimeError("disk full")

    tool = McpTool(name="t", description="t", input_schema={"type": "object"}, call=boom,
                   role="viewer", title="T")
    server = McpServer(tools=lambda: [tool], role=lambda: "viewer")
    result = server.handle(rpc("tools/call", {"name": "t"}))["result"]
    assert result["isError"] is True and "disk full" in result["content"][0]["text"]
    assert tool.declaration()["title"] == "T"

    def broken_provider() -> list[McpTool]:
        raise OSError("registry gone")

    failing = McpServer(tools=broken_provider)
    assert failing.handle(rpc("tools/list"))["error"]["code"] == -32603


def test_resources_list_and_read() -> None:
    resource = McpResource(uri="kdx://a", name="a", description="A", read=lambda: "# A")
    server = McpServer(tools=lambda: [], resources=lambda: [resource])
    assert server.handle(rpc("resources/list"))["result"]["resources"][0]["uri"] == "kdx://a"
    read = server.handle(rpc("resources/read", {"uri": "kdx://a"}))["result"]
    assert read["contents"][0]["text"] == "# A"
    assert server.handle(rpc("resources/read", {"uri": "kdx://b"}))["error"]["code"] == (
        INVALID_PARAMS
    )


# -- the CLI contract as tools ----------------------------------------------------------


def test_input_schema_and_argv_follow_the_declared_arguments() -> None:
    arguments = (
        CliArgument(("target",), help="where"),
        CliArgument(("extra",), nargs="*"),
        CliArgument(("-n", "--lines"), type="int", default=5),
        CliArgument(("--json",), dest="as_json", action="store_true"),
        CliArgument(("--no-color",), dest="color", action="store_false"),
        CliArgument(("--tag",), action="append"),
        CliArgument(("--level",), choices=("low", "high"), required=True, metavar="L"),
        CliArgument(("--path",), type="path", default=Path("x")),
    )
    schema = input_schema(arguments)
    props = schema["properties"]
    assert schema["required"] == ["target", "level"]
    assert props["lines"] == {"type": "integer", "default": 5}
    assert props["as_json"] == {"type": "boolean"}
    assert props["extra"]["type"] == "array"
    assert props["level"]["enum"] == ["low", "high"] and props["level"]["description"] == "L"
    assert props["path"]["default"] == "x"
    tokens = argv_for(
        arguments,
        {
            "target": "here",
            "extra": ["a", 1],
            "lines": 3,
            "as_json": True,
            "color": False,
            "tag": ["x", "y"],
            "level": "high",
            "path": None,
        },
    )
    assert tokens == [
        "here", "a", "1", "--lines", "3", "--json", "--no-color",
        "--tag", "x", "--tag", "y", "--level", "high",
    ]
    assert argv_for(arguments, {"as_json": False, "color": True}) == []
    assert argv_for((CliArgument(("--on",)),), {"on": True}) == ["--on", "true"]


def test_command_tools_respect_remote_danger_role_and_visibility() -> None:
    def handler(_request: Any) -> int:
        return 0

    commands = [
        CliCommand(
            name="backup",
            module_name="backup_engine",
            subcommands=(
                CliCommand(name="run", handler=handler, danger=True),
                CliCommand(name="plan", handler=handler, help="Show the plan."),
                CliCommand(name="local", handler=handler, remote=False),
                CliCommand(name="hidden", handler=handler, show_in_wizard=False),
            ),
        ),
        CliCommand(name="drop", handler=handler, role="contributor"),
        CliCommand(name="mcp", handler=handler),
        CliCommand(name="drop", handler=handler),
    ]
    calls: list[tuple[str, list[str]]] = []

    def run(path: str, argv: list[str]) -> Any:
        calls.append((path, argv))
        return ("ok", None, False)

    tools = {tool.name: tool for tool in command_tools(commands, run, skip=("mcp",))}
    assert set(tools) == {"backup_run", "backup_plan", "drop"}
    assert tools["backup_run"].role == "admin" and tools["backup_run"].destructive
    assert "changes state" in tools["backup_run"].description
    assert tools["backup_plan"].role == "operator"
    assert tools["drop"].role == "operator"  # operator floor: a command is never below it
    tools["backup_plan"].call({})
    assert calls == [("backup plan", [])]
    safe = command_tools(commands, run, allow_danger=False)
    assert "backup_run" not in {tool.name for tool in safe}
    assert tool_name("a b/c") == "a_b_c" and tool_name("  ") == "command"


# -- HTTP transport on a live chassis ---------------------------------------------------


def post(base: str, body: Any, token: str, *, bearer: bool = True) -> tuple[int, Any]:
    headers = {"Content-Type": "application/json"}
    headers.update({"Authorization": f"Bearer {token}"} if bearer else {"X-Kdx-Token": token})
    request = urllib.request.Request(
        base + MCP_PATH, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return int(response.status), json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return int(exc.code), json.loads(raw) if raw else None
        except ValueError:
            return int(exc.code), raw.decode("utf-8", "replace")


@pytest.fixture()
def live(project_root: Path) -> Any:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    chassis.start(ipc=True)
    try:
        yield chassis
    finally:
        chassis.shutdown()


def test_http_mcp_lists_and_calls_tools_with_a_bearer_token(live: Chassis) -> None:
    base = live.ipc.discovery.base_url()  # type: ignore[union-attr]
    token = live.ipc.discovery_token  # type: ignore[union-attr]
    code, reply = post(base, rpc("initialize", {"protocolVersion": "2025-06-18"}), token)
    assert code == 200 and reply["result"]["serverInfo"]["name"] == "korodexios"
    code, _ = post(base, {"jsonrpc": "2.0", "method": "notifications/initialized"}, token)
    assert code == 202
    code, reply = post(base, rpc("tools/list"), token, bearer=False)
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert {"kdx_health", "kdx_logs", "kdx_config", "heartbeat_status"} <= names
    assert not any(name.startswith("mcp") for name in names)
    code, reply = post(base, rpc("tools/call", {"name": "heartbeat_status"}), token)
    result = reply["result"]
    assert result["isError"] is False and "heartbeat" in result["content"][0]["text"]
    code, reply = post(base, rpc("tools/call", {"name": "kdx_health"}), token)
    assert "modules" in reply["result"]["structuredContent"]
    code, reply = post(base, rpc("tools/call", {"name": "kdx_config"}), token)
    assert any(row["key"] == "core.ipc.token" and row["value"] != token
               for row in reply["result"]["structuredContent"]["entries"])
    code, reply = post(base, rpc("tools/call", {"name": "kdx_logs", "arguments": {}}), token)
    assert "disabled" in reply["result"]["content"][0]["text"]
    code, reply = post(base, rpc("resources/list"), token)
    assert {"kdx://playbook", "kdx://conventions"} <= {
        item["uri"] for item in reply["result"]["resources"]
    }
    assert post(base, rpc("tools/list"), "wrong")[0] == 401
    records = live.audit.path.read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert '"action": "mcp"' in records and "heartbeat status" in records


def test_http_mcp_gives_a_viewer_read_only_tools(live: Chassis) -> None:
    base = live.ipc.discovery.base_url()  # type: ignore[union-attr]
    viewer = live.users.add("watcher", "viewer")  # type: ignore[union-attr]
    _, reply = post(base, rpc("tools/list"), viewer)
    names = {tool["name"] for tool in reply["result"]["tools"]}
    assert names == {"kdx_health", "kdx_logs", "kdx_config", "kdx_metrics"}


def test_http_mcp_refuses_get_and_a_body_that_is_not_json(live: Chassis) -> None:
    base = live.ipc.discovery.base_url()  # type: ignore[union-attr]
    token = live.ipc.discovery_token  # type: ignore[union-attr]
    request = urllib.request.Request(base + MCP_PATH, headers={"X-Kdx-Token": token})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 405
    raw = urllib.request.Request(
        base + MCP_PATH, data=b"not json", method="POST",
        headers={"X-Kdx-Token": token, "Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(raw, timeout=5)
    assert caught.value.code == 400


def test_a_refused_tool_reports_why(live: Chassis) -> None:
    module = live.registry.get("mcp").module  # type: ignore[union-attr]
    assert isinstance(module, McpServerModule)
    text, structured, failed = module._run_in_chassis(live, "nosuch", [], None)
    assert failed and "nosuch" in text and structured["error"]
    assert module._run_in_chassis(None, "x", [], None)[2] is True


# -- stdio transport --------------------------------------------------------------------


def serve(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], root: Path,
          messages: list[dict[str, Any]], *extra: str) -> list[dict[str, Any]]:
    data = "".join(json.dumps(message) + "\n" for message in messages) + "\n"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data.encode("utf-8"))))
    code = main([*base_args(root), "mcp", "serve", *extra])
    assert code == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_stdio_serves_tools_in_process(monkeypatch, capsys, project_root: Path) -> None:
    replies = serve(
        monkeypatch, capsys, project_root,
        [
            rpc("initialize", {"protocolVersion": "2024-11-05"}, 1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc("tools/list", None, 2),
            rpc("tools/call", {"name": "heartbeat_status", "arguments": {}}, 3),
            rpc("tools/call", {"name": "kdx_health"}, 4),
        ],
        "--in-process",
    )
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["protocolVersion"] == "2024-11-05"
    names = {tool["name"] for tool in replies[1]["result"]["tools"]}
    assert "heartbeat_status" in names and "backup_run" not in names  # danger needs admin
    assert replies[2]["result"]["isError"] is False
    assert replies[3]["result"]["structuredContent"]["schema"]


def test_stdio_role_admin_opens_danger_tools(monkeypatch, capsys, project_root: Path) -> None:
    replies = serve(monkeypatch, capsys, project_root, [rpc("tools/list")], "--role", "admin")
    assert "backup_run" in {tool["name"] for tool in replies[0]["result"]["tools"]}


def test_stdio_delegates_to_a_live_daemon(monkeypatch, capsys, live: Chassis) -> None:
    root = Path(live.paths.root)
    replies = serve(monkeypatch, capsys, root, [
        rpc("tools/call", {"name": "heartbeat_status"}, 1),
        rpc("tools/call", {"name": "kdx_health"}, 2),
        rpc("tools/call", {"name": "system_info", "arguments": {"as_json": "yes"}}, 3),
    ])
    assert replies[0]["result"]["isError"] is False
    assert "ipc" in replies[1]["result"]["structuredContent"]
    assert replies[2]["result"]["isError"] is True


def test_tools_and_config_commands(capsys, project_root: Path) -> None:
    assert main([*base_args(project_root), "mcp", "tools", "--json"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["role"] == "operator" and listed["tools"]
    assert main([*base_args(project_root), "mcp", "tools"]) == 0
    assert "heartbeat_status" in capsys.readouterr().out
    assert main([*base_args(project_root), "mcp", "config", "--client", "n8n"]) == 0
    assert "HTTP Streamable" in capsys.readouterr().out
    assert main([*base_args(project_root), "mcp", "config"]) == 0
    assert "claude mcp add kdx" in capsys.readouterr().out
    existing = project_root / ".mcp.json"
    existing.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert main([*base_args(project_root), "mcp", "config", "--write"]) == 0
    written = json.loads(existing.read_text(encoding="utf-8"))
    assert set(written["mcpServers"]) == {"other", "kdx"}
    assert written["mcpServers"]["kdx"]["args"][-2:] == ["mcp", "serve"]
    vscode = json.loads((project_root / ".vscode" / "mcp.json").read_text(encoding="utf-8"))
    assert vscode["servers"]["kdx"]["type"] == "stdio"
    assert (project_root / ".cursor" / "mcp.json").is_file()
    (project_root / ".cursor" / "mcp.json").write_text("[1]", encoding="utf-8")
    assert main([*base_args(project_root), "mcp", "config", "--write"]) == 1


def test_client_configs_and_merge(tmp_path: Path) -> None:
    configs = client_configs(tmp_path, 18123)
    assert "/api/v1/mcp" in configs["n8n"]
    target = tmp_path / "x.json"
    merge_server_entry(target, "mcpServers", "kdx", {"command": "a"})
    merge_server_entry(target, "mcpServers", "kdx", {"command": "b"})
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written == {"mcpServers": {"kdx": {"command": "b"}}}
