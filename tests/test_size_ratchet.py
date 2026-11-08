"""Size ratchet: conventions section 2 sizes are triggers, and the excess may only shrink.

Every file over 400 lines, function over 40 lines and signature over 5 arguments is recorded
below with its measured size. The test fails when a new one appears, when a recorded one
grows, or when a recorded number is left higher than reality: clean something, lower the
number; never raise it (the same ratchet the coverage floor uses). Splitting a module is a
decision about jobs, never about lines; nothing here asks permission or stops anyone.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "korodexios_core"
FILE_LINES = 400
FUNCTION_LINES = 40
ARGUMENTS = 5

FILES: dict[str, int] = {
    "cli/commands.py": 1739,
    "cli/entry.py": 725,
    "cli/init_project.py": 1921,
    "cli/language.py": 518,
    "cli/proposal.py": 439,
    "cli/service.py": 463,
    "cli/ui_react.py": 1124,
    "config/layered.py": 966,
    "kernel/chassis.py": 1548,
    "kernel/contract.py": 413,
    "kernel/discovery.py": 665,
    "kernel/instance_lock.py": 419,
    "kernel/ipc.py": 2191,
    "kernel/limiter.py": 441,
    "kernel/locks.py": 558,
    "kernel/mcp.py": 532,
    "kernel/queue.py": 464,
    "kernel/registry.py": 878,
    "kernel/scheduler.py": 678,
    "kernel/txn.py": 452,
    "kernel/ui_manifest.py": 594,
    "kernel/ui_tiers.py": 407,
    "logging/engine.py": 613,
    "modules/backup_engine/engine.py": 404,
    "modules/backup_engine/module.py": 1279,
    "modules/job_queue/module.py": 457,
    "modules/mcp_server/module.py": 659,
    "modules/system_info/module.py": 506,
    "modules/web_console/module.py": 1158,
    "modules/webhooks/module.py": 489,
    "modules/wiki/module.py": 614,
    "modules/wiki/pages.py": 556,
    "paths.py": 551,
}
"""File (relative to the package) -> its line count, for files over FILE_LINES."""
FUNCTIONS: dict[str, int] = {
    "cli/agents.py::_sync": 43,
    "cli/brief.py::questions": 52,
    "cli/commands.py::_config_check_command": 72,
    "cli/commands.py::_contracts_check_command": 85,
    "cli/commands.py::_doctor_command": 226,
    "cli/commands.py::_init_command": 60,
    "cli/commands.py::_logs_command": 112,
    "cli/commands.py::_modules_create_command": 67,
    "cli/commands.py::_modules_list_command": 62,
    "cli/commands.py::_modules_show_command": 42,
    "cli/commands.py::_reload_command": 42,
    "cli/commands.py::_rpc_command": 48,
    "cli/commands.py::_status_command": 123,
    "cli/commands.py::core_commands": 470,
    "cli/config_edit.py::apply_edits": 49,
    "cli/container.py::_dockerfile": 73,
    "cli/container_doctor.py::_doctor": 44,
    "cli/container_doctor.py::_fix": 52,
    "cli/container_doctor.py::container_command": 42,
    "cli/db.py::db_command": 42,
    "cli/entry.py::_add_global_flags": 264,
    "cli/entry.py::_cli_overrides": 92,
    "cli/entry.py::main": 116,
    "cli/init_project.py::_agents": 87,
    "cli/init_project.py::_app_module": 97,
    "cli/init_project.py::_cli_source": 45,
    "cli/init_project.py::_config_yml": 58,
    "cli/init_project.py::_docs_agents_guide": 211,
    "cli/init_project.py::_docs_architecture": 132,
    "cli/init_project.py::_i18n_source": 69,
    "cli/init_project.py::_pyproject": 55,
    "cli/init_project.py::_readme": 116,
    "cli/init_project.py::_smoke_test": 113,
    "cli/init_project.py::build_files": 52,
    "cli/init_project.py::project_from_inputs": 75,
    "cli/init_project.py::prompt_project": 51,
    "cli/jobs.py::_events": 41,
    "cli/language.py::_check": 43,
    "cli/language.py::_extract": 42,
    "cli/language.py::_set": 45,
    "cli/language.py::locale_command": 106,
    "cli/limiter.py::limiter_command": 44,
    "cli/proposal.py::_send": 72,
    "cli/proposal.py::proposal_command": 64,
    "cli/reset.py::_reset": 69,
    "cli/scaffold.py::_module_source": 77,
    "cli/scaffold.py::scaffold_module": 43,
    "cli/service.py::_task_xml": 43,
    "cli/service.py::build_plan": 60,
    "cli/txn.py::txn_command": 42,
    "cli/ui_react.py::react_files": 57,
    "cli/ui_test.py::run_gui_tests": 64,
    "cli/users.py::users_command": 54,
    "cli/ux.py::menu": 45,
    "config/layered.py::__init__": 50,
    "config/layered.py::load": 45,
    "config/schema.py::validate": 45,
    "events/bus.py::publish": 72,
    "kernel/chassis.py::_chassis_diagnostics": 138,
    "kernel/chassis.py::_rpc_namespace": 43,
    "kernel/chassis.py::create": 154,
    "kernel/chassis.py::health_report": 69,
    "kernel/chassis.py::prepare_rpc": 103,
    "kernel/chassis.py::reload_config": 47,
    "kernel/chassis.py::start_ipc": 88,
    "kernel/chassis.py::submit_job": 51,
    "kernel/cli_contract.py::add_command_arguments": 44,
    "kernel/container_probe.py::_advice": 42,
    "kernel/container_probe.py::probe_container_host": 83,
    "kernel/database.py::migrate": 45,
    "kernel/discovery.py::_build_spec": 57,
    "kernel/discovery.py::_import": 56,
    "kernel/discovery.py::_load_candidate": 76,
    "kernel/discovery.py::scan": 43,
    "kernel/i18n_extract.py::compose_po": 57,
    "kernel/ipc.py::__init__": 46,
    "kernel/ipc.py::_route_as": 99,
    "kernel/ipc.py::_serve_module_route": 53,
    "kernel/ipc.py::_serve_static": 58,
    "kernel/ipc.py::_websocket": 59,
    "kernel/ipc.py::api_contract": 90,
    "kernel/ipc.py::cross_origin_refusal": 41,
    "kernel/ipc.py::start": 74,
    "kernel/jobs.py::submit": 43,
    "kernel/locks.py::acquire": 60,
    "kernel/mcp.py::command_tools": 55,
    "kernel/process.py::run": 121,
    "kernel/process.py::run_json": 44,
    "kernel/registry.py::_run": 41,
    "kernel/reset.py::plan_reset": 67,
    "kernel/scheduler.py::run_task": 51,
    "kernel/scheduler.py::schedule": 72,
    "kernel/scheduler.py::schedule_at": 41,
    "kernel/ui_manifest.py::_module_entry": 44,
    "kernel/ui_prefs.py::_layout": 58,
    "kernel/ui_tiers.py::probe_environment": 48,
    "kernel/ui_tree.py::node": 48,
    "kernel/websocket.py::receive": 42,
    "logging/engine.py::build_logger": 62,
    "modules/backup_engine/engine.py::build_plan": 64,
    "modules/backup_engine/engine.py::execute_plan": 50,
    "modules/backup_engine/module.py::_configure_command": 44,
    "modules/backup_engine/module.py::_prune_check": 80,
    "modules/backup_engine/module.py::_prune_dest": 42,
    "modules/backup_engine/module.py::_run_command": 48,
    "modules/backup_engine/module.py::_sweep_check": 41,
    "modules/backup_engine/module.py::config_schema": 72,
    "modules/backup_engine/module.py::diagnostics": 43,
    "modules/backup_engine/module.py::health": 41,
    "modules/backup_engine/module.py::init": 46,
    "modules/backup_engine/module.py::register_cli": 55,
    "modules/backup_engine/module.py::retention_report": 52,
    "modules/heartbeat/module.py::diagnostics": 50,
    "modules/job_queue/module.py::register_cli": 84,
    "modules/mcp_server/module.py::_stdio_server": 48,
    "modules/mcp_server/module.py::register_cli": 72,
    "modules/mcp_server/module.py::tools": 86,
    "modules/metrics/module.py::chassis_samples": 43,
    "modules/system_info/module.py::diagnostics": 81,
    "modules/system_info/module.py::register_cli": 63,
    "modules/web_console/module.py::_doctor_command": 43,
    "modules/web_console/module.py::ipc_routes": 80,
    "modules/web_console/module.py::register_cli": 252,
    "modules/webhooks/module.py::deliver": 51,
    "modules/wiki/export.py::apply_export": 42,
    "modules/wiki/export.py::plan_export": 66,
    "modules/wiki/module.py::_export": 45,
    "modules/wiki/module.py::register_cli": 107,
    "modules/wiki/pages.py::check_pages": 85,
    "modules/wiki/pages.py::schema_text": 42,
    "paths.py::discover": 63,
}
"""'file::function' -> its line count, for functions over FUNCTION_LINES."""
SIGNATURES: dict[str, int] = {
    "cli/config_edit.py::apply_edits": 6,
    "cli/init_project.py::project_from_inputs": 9,
    "cli/init_project.py::prompt_project": 9,
    "cli/ui_test.py::_run": 6,
    "cli/ui_test.py::run_gui_tests": 9,
    "config/layered.py::__init__": 10,
    "config/layered.py::load": 7,
    "kernel/audit.py::record": 6,
    "kernel/container_probe.py::probe_container_host": 7,
    "kernel/context.py::run_json": 6,
    "kernel/context.py::run_process": 7,
    "kernel/discovery.py::__init__": 6,
    "kernel/discovery.py::_build_spec": 6,
    "kernel/discovery.py::_load_candidate": 7,
    "kernel/ipc.py::__init__": 14,
    "kernel/ipc.py::audit_event": 6,
    "kernel/ipc.py::register_static_dir": 6,
    "kernel/limiter.py::__init__": 12,
    "kernel/process.py::run": 8,
    "kernel/process.py::run_json": 6,
    "kernel/scheduler.py::__init__": 8,
    "kernel/scheduler.py::schedule": 10,
    "kernel/ui_tiers.py::probe_environment": 6,
    "kernel/ui_tree.py::node": 8,
    "logging/engine.py::__init__": 6,
    "modules/web_console/module.py::visit": 6,
    "modules/webhooks/module.py::verify": 6,
    "modules/wiki/export.py::plan_export": 8,
    "modules/wiki/pages.py::render_page": 8,
}
"""'file::function' -> its argument count, for signatures over ARGUMENTS."""


def _measure() -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    files: dict[str, int] = {}
    functions: dict[str, int] = {}
    signatures: dict[str, int] = {}
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        text = path.read_text(encoding="utf-8")
        if len(text.splitlines()) > FILE_LINES:
            files[name] = len(text.splitlines())
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            key = f"{name}::{node.name}"
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length > FUNCTION_LINES:
                functions[key] = max(functions.get(key, 0), length)
            args = node.args
            named = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
            count = len([n for n in named if n not in ("self", "cls")])
            count += int(args.vararg is not None) + int(args.kwarg is not None)
            if count > ARGUMENTS:
                signatures[key] = max(signatures.get(key, 0), count)
    return files, functions, signatures


def _problems(kind: str, actual: dict[str, int], recorded: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for name, size in sorted(actual.items()):
        if name not in recorded:
            problems.append(f"{kind} {name}: {size} is over the trigger and not recorded")
        elif size > recorded[name]:
            problems.append(f"{kind} {name}: grew from {recorded[name]} to {size}")
    for name, size in sorted(recorded.items()):
        now = actual.get(name, 0)
        if now < size:
            problems.append(f"{kind} {name}: recorded {size}, now {now}; lower the entry")
    return problems


def test_size_debt_only_shrinks() -> None:
    """No new excess, no growth, and no recorded number left higher than reality."""
    files, functions, signatures = _measure()
    problems = [
        *_problems("file", files, FILES),
        *_problems("function", functions, FUNCTIONS),
        *_problems("signature", signatures, SIGNATURES),
    ]
    assert not problems, "\n".join(problems)
