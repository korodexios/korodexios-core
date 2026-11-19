"""Module administration: reload one module of a running daemon without restarting it.

- ``kdx module-reload NAME [--cascade]`` - the terminal command.
- ``POST /api/v1/modules/reload`` ``{"module": "NAME", "cascade": false}`` - the same over IPC
  (admin role). ``200`` when the new code runs, ``409`` with the reason when it was refused or
  rolled back; the running module is never left half replaced.

The new code is imported and checked first; the old module is only stopped when that
succeeded, and it is started again if the new one cannot come up (``kernel/module_reload.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...cli.render import dump_json, tr, write_line, write_status
from ...constants import TAG_ERROR, TAG_OK, ExitCode
from ...kernel.cli_contract import CliArgument, CliCommand, CliRequest, CliSurface
from ...kernel.contract import Module
from ...kernel.ipc import DISCOVERY_NAME, IpcDiscovery, IpcRequest, IpcRoute
from ...kernel.module_reload import ReloadReport, reload_module

RELOAD_PATH = "/api/v1/modules/reload"
CLIENT_TIMEOUT = 120.0


class ModuleAdminModule(Module):
    """Serves module reload over IPC and from the terminal."""

    name = "module_admin"
    description = "Reload one module of a running daemon (kdx module-reload)."
    version = "0.1.0"

    def ipc_routes(self) -> tuple[IpcRoute, ...]:
        """Serve the reload route (admin only; it stops and starts code)."""
        return (
            IpcRoute(
                path=RELOAD_PATH,
                handler=self._route,
                methods=("POST",),
                auth=True,
                module=self.name,
                role="admin",
                summary="Reload one module: {'module': name, 'cascade': bool}",
            ),
        )

    def _reload(self, name: str, cascade: bool) -> ReloadReport:
        chassis = self.require_context().chassis
        if chassis is None:
            return ReloadReport(module=name).refuse("this process has no chassis to reload in")
        return reload_module(chassis, name, cascade=cascade)

    def _route(self, request: IpcRequest) -> Any:
        name = str(request.argument("module") or "").strip()
        if not name:
            return 400, {"ok": False, "error": "the body needs {'module': '<name>'}"}
        cascade = str(request.argument("cascade") or "").lower() in ("1", "true", "yes")
        report = self._reload(name, cascade)
        return (200 if report.reloaded else 409), report.as_dict()

    def register_cli(self, surface: CliSurface) -> None:
        """Contribute 'kdx module-reload'."""
        surface.add_command(
            CliCommand(
                name="module-reload",
                help="Reload one module of the running daemon without restarting it.",
                module_name=self.name,
                handler=self._command,
                danger=True,
                args=(
                    CliArgument(("target_module",), metavar="NAME", help="module to reload"),
                    CliArgument(
                        ("--cascade",),
                        dest="cascade",
                        action="store_true",
                        help="also restart the running modules that depend on it",
                    ),
                    CliArgument(
                        ("--json",), dest="as_json", action="store_true", help="Emit JSON."
                    ),
                ),
            )
        )

    def _command(self, request: CliRequest) -> int:
        name = str(request.option("target_module") or "").strip()
        cascade = request.flag("cascade")
        if request.options.get("rpc"):
            view = self._reload(name, cascade).as_dict()
        else:
            view = self._ask_daemon(request, name, cascade)
        if request.flag("as_json"):
            dump_json(view)
        else:
            _print(view)
        return int(ExitCode.SUCCESS if view.get("reloaded") else ExitCode.ERROR)

    def _ask_daemon(self, request: CliRequest, name: str, cascade: bool) -> dict[str, Any]:
        found = IpcDiscovery.load(Path(request.context.paths.state_dir) / DISCOVERY_NAME)
        if found is None or not found.is_process_alive():
            return {
                "module": name,
                "reloaded": False,
                "error": "no daemon is running",
                "hint": "A new 'kdx' process always loads the current code (kdx run).",
            }
        try:
            _, body = found.request(
                RELOAD_PATH,
                method="POST",
                payload={"module": name, "cascade": cascade},
                timeout=CLIENT_TIMEOUT,
            )
        except OSError as exc:
            return {"module": name, "reloaded": False, "error": f"the daemon did not answer: {exc}"}
        return dict(body) if isinstance(body, dict) else {
            "module": name, "reloaded": False, "error": "the daemon sent an unreadable answer"}


def _print(view: dict[str, Any]) -> None:
    name = str(view.get("module"))
    if view.get("reloaded"):
        write_status(
            tr("module {name} reloaded ({before} -> {after})", name=name,
               before=view.get("version_before"), after=view.get("version_after")),
            tag=TAG_OK,
        )
        if view.get("restarted"):
            write_line(tr("restarted with it: {names}", names=", ".join(view["restarted"])))
        if view.get("failed"):
            write_status(tr("did not restart: {names}", names=", ".join(view["failed"])),
                         tag=TAG_ERROR)
        return
    write_status(tr("module {name} was not reloaded: {error}", name=name,
                    error=view.get("error", "")), tag=TAG_ERROR)
    if view.get("rolled_back"):
        write_line(tr("The previous version is running again."))
    if view.get("hint"):
        write_line(str(view["hint"]))


MODULE = ModuleAdminModule
