"""The GUI-tier half of ``kdx ui``: guide, doctor, test, add, types.

These choose, check and set up a project's own GUI; the rest of ``kdx ui`` (manifest, prefs,
layout, theme) drives the built-in console. The handlers that need the module (``test`` reads
the live manifest) are passed in, so this file holds no module state.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...cli.init_project import ProjectError
from ...cli.jobs import TARGET_ARGS, _call, _failed, connect
from ...cli.render import dump_json, print_key_values, print_section, tr, write_line
from ...cli.ui_add import add_tier
from ...cli.ui_types import render_types
from ...constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ...kernel.ipc import SCHEMA_PATH
from ...kernel.ui_tiers import DEFAULT_TIER, PACKAGE_MANAGERS, guide, probe_environment, tier_names

Handler = Callable[[CliRequest], int]
_JSON = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
_PATH = CliArgument(("--path",), dest="path", metavar="DIR",
                    help="Project root [the chassis root].")


def _root(request: CliRequest) -> Path:
    text = str(request.option("path") or "").strip()
    return Path(text).expanduser() if text else Path(request.context.paths.root)


def guide_command(request: CliRequest) -> int:
    """Print the tiers, how to decide, the rules and the known limits."""
    data = guide()
    if request.flag("as_json"):
        dump_json(data)
        return int(ExitCode.SUCCESS)
    for name, tier in data["tiers"].items():
        # the data stays English for --json; the terminal shows it in the chosen language
        print_section(f"{name}: {tr(tier['summary'])}")
        for line in tier["choose_when"]:
            write_line(f"  + {tr(line)}")
        for line in tier["avoid_when"]:
            write_line(f"  - {tr(line)}")
        write_line("  " + tr("create: {command}", command=tier["init"]))
        if tier.get("add"):
            write_line("  " + tr("add to a project: {command}", command=tier["add"]))
    print_section("how to decide")
    for line in data["decide"]:
        write_line(f"  {tr(line)}")
    print_section("rules")
    for line in data["rules"]:
        write_line(f"  {tr(line)}")
    print_section("known limits")
    for note in data["notes"]:
        write_line(f"  [{note['applies_to']}] {tr(note['text'])}")
    print_section("embed protocol")
    for line in data["embed"]["rules"]:
        write_line(f"  {tr(line)}")
    return int(ExitCode.SUCCESS)


def doctor_command(request: CliRequest) -> int:
    """Report the tools, browsers and display this machine has for a GUI."""
    report = probe_environment()
    if request.flag("as_json"):
        dump_json(report)
        return int(ExitCode.SUCCESS)
    tools = report["tools"]
    print_key_values(
        [
            (name, tools[name]["version"] or ("found" if tools[name]["found"] else "missing"))
            for name in tools
        ]
        + [
            ("browsers", ", ".join(b["name"] for b in report["browsers"]) or "none found"),
            ("playwright browsers", "yes" if report["playwright_browsers"] else "no"),
            ("display", f"{'yes' if report['display']['available'] else 'no'} "
             f"({report['display']['reason']})"),
            ("container engines", ", ".join(report["container"]["engines"]) or "none found"),
            ("running inside", report["container"]["inside"] or "no container"),
        ]
    )
    _doctor_verdicts(report)
    return int(ExitCode.SUCCESS)


def _doctor_verdicts(report: dict[str, Any]) -> None:
    testing = report["testing"]
    write_line(
        f"{TAG_OK if testing['headless_browser'] else TAG_NOTE} "
        + tr("headless browser tests: {state}",
             state="yes" if testing["headless_browser"] else "no (API and HTML tests only)")
    )
    write_line(
        f"{TAG_OK if testing['headed_browser'] else TAG_NOTE} "
        + tr("headed browser tests: {state}", state="yes" if testing["headed_browser"] else "no")
    )
    for setting, value in testing["recommended"].items():
        write_line(f"{TAG_NOTE} " + tr("for browser tests: {setting}={value}", setting=setting,
                                       value=value))
    for name, tier in report["tiers"].items():
        write_line(
            f"{TAG_OK if tier['ready'] else TAG_ERROR} "
            + tr("tier {name}: {state}", name=name,
                 state="ready" if tier["ready"] else "missing " + ", ".join(tier["missing"]))
        )


def add_command(request: CliRequest) -> int:
    """Write a GUI tier into an existing project, refusing to overwrite anything."""
    try:
        result = add_tier(
            _root(request),
            str(request.option("tier") or ""),
            pm=str(request.option("pm") or "").strip().lower(),
            dry_run=request.flag("dry_run"),
        )
    except ProjectError as exc:
        if request.flag("as_json"):
            dump_json({"ok": False, "error": str(exc), "hint": exc.hint})
        else:
            write_line(f"{TAG_ERROR} {exc}")
            if exc.hint:
                write_line(f"{TAG_NOTE} {exc.hint}")
        return int(ExitCode.ERROR)
    if request.flag("as_json"):
        dump_json({"ok": True, **result.as_dict()})
        return int(ExitCode.SUCCESS)
    if result.dry_run:
        line = tr("tier {tier}: would write {count} files", tier=result.tier,
                  count=len(result.files))
    else:
        line = tr("tier {tier}: wrote {count} files", tier=result.tier, count=len(result.files))
    write_line(f"{TAG_OK} {line}")
    for path in result.files:
        write_line(f"    {path}")
    if result.ignore_rules:
        write_line(f"{TAG_NOTE} " + tr(".gitignore gains: {rules}",
                                       rules=", ".join(result.ignore_rules)))
    print_section("next")
    for step in result.next_steps:
        write_line(f"  {step}")
    return int(ExitCode.SUCCESS)


def _contract(request: CliRequest) -> dict[str, Any] | None:
    source = str(request.option("source") or "").strip()
    if source:
        try:
            loaded = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            write_line(f"{TAG_ERROR} " + tr("cannot read {path}: {exc}", path=source, exc=exc))
            return None
        return loaded if isinstance(loaded, dict) else None
    discovery = connect(request)
    reply = None if discovery is None else _call(discovery, SCHEMA_PATH)
    if reply is None:
        return None
    code, payload = reply
    if code != 200 or not isinstance(payload, dict):
        _failed(code, payload)
        return None
    return payload


def types_command(request: CliRequest) -> int:
    """Write (or check) the TypeScript route types of a daemon's API contract."""
    contract = _contract(request)
    if contract is None:
        return int(ExitCode.ERROR)
    text = render_types(contract)
    out = str(request.option("out") or "").strip()
    if not out:
        write_line(text.rstrip("\n"))
        return int(ExitCode.SUCCESS)
    target = Path(out).expanduser()
    current = target.read_text(encoding="utf-8") if target.is_file() else None
    if request.flag("check"):
        if current == text:
            write_line(f"{TAG_OK} " + tr("{path}: up to date", path=out))
            return int(ExitCode.SUCCESS)
        write_line(f"{TAG_ERROR} " + tr("{path}: stale; run without --check to write it",
                                        path=out))
        return int(ExitCode.ERROR)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    write_line(f"{TAG_OK} " + tr("wrote {path}", path=out))
    return int(ExitCode.SUCCESS)


_TEST_ARGS = (
    CliArgument(("--headed",), dest="headed", action="store_true",
                help="Also run the e2e tests with a visible browser window (needs a display)."),
    CliArgument(("--install",), dest="install", action="store_true",
                help="Run the package manager's install if node_modules is missing."),
    CliArgument(("--pm",), dest="pm", metavar="MANAGER",
                help="npm | bun | pnpm [from the lockfile, else npm]."),
    _PATH, _JSON,
)
_ADD_ARGS = (
    CliArgument(("tier",), choices=tuple(name for name in tier_names() if name != DEFAULT_TIER),
                help="The GUI tier to add."),
    CliArgument(("--pm",), dest="pm", choices=PACKAGE_MANAGERS,
                help="Package manager of an app tier's web/ [npm]."),
    CliArgument(("--dry-run",), dest="dry_run", action="store_true",
                help="List the files without writing them."),
    _PATH, _JSON,
)
_TYPES_ARGS = (
    CliArgument(("--out",), dest="out", metavar="FILE",
                help="Write here, e.g. web/src/kdx/schema.ts [print to stdout]."),
    CliArgument(("--check",), dest="check", action="store_true",
                help="Exit 1 when --out differs from the daemon's contract."),
    CliArgument(("--from",), dest="source", metavar="FILE",
                help="Read the contract from a file ('kdx api schema --json') instead of a "
                "daemon."),
    *TARGET_ARGS,
)


def tier_commands(module: str, test: Handler) -> tuple[CliCommand, ...]:
    """Return the 'kdx ui' subcommands about a project's own GUI tier."""
    common: dict[str, Any] = {"module_name": module, "show_in_wizard": False}
    return (
        CliCommand(name="doctor", handler=doctor_command, args=(_JSON,), **common,
                   help="Report what this machine can run for a GUI: Node, Bun, browsers, a "
                   "display, headless testing."),
        CliCommand(name="test", handler=test, remote=False, args=_TEST_ARGS, **common,
                   help="Run the GUI tests this machine can run: manifest, then the web app's "
                   "type check, component tests and headless e2e (--headed adds a window)."),
        CliCommand(name="guide", handler=guide_command, args=(_JSON,), **common,
                   help="Explain the GUI tiers a project can choose and when to pick each."),
        CliCommand(name="add", handler=add_command, remote=False, args=_ADD_ARGS, **common,
                   help="Give an existing project a GUI tier (the files 'kdx init --ui' "
                   "writes); never overwrites a file."),
        CliCommand(name="types", handler=types_command, remote=False, args=_TYPES_ARGS,
                   **common, help="Generate TypeScript route types from a daemon's "
                   "/api/v1/schema."),
    )


__all__ = ["add_command", "doctor_command", "guide_command", "tier_commands", "types_command"]
