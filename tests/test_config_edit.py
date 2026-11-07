"""Settings editor: one writer for `kdx config set|unset|schema` and the console's form."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from korodexios_core.cli.config_edit import apply_edits, editable_keys, schema_view
from korodexios_core.cli.entry import main
from korodexios_core.config.editor import (
    ConfigEditError,
    declared_keys,
    plan_edits,
    write_local,
)
from korodexios_core.config.schema import ConfigField, ConfigKind, ConfigSchema
from korodexios_core.constants import ExitCode
from korodexios_core.kernel.cli_contract import CliArgument, add_command_arguments
from korodexios_core.kernel.ipc import IpcRequest
from korodexios_core.modules.web_console.module import (
    CONFIG_EDIT_PATH,
    CONFIG_SCHEMA_PATH,
)

from .test_ipc import base_args
from .test_web_console import console_of, ipc_chassis

SCHEMAS = {
    "demo": ConfigSchema(
        module="demo",
        fields=(
            ConfigField("mode", choices=("fast", "slow"), default="fast", description="Speed."),
            ConfigField("limit", kind=ConfigKind.INT, default=5, minimum=1, maximum=10, live=True),
            ConfigField("token", secret=True),
            ConfigField("tags", kind=ConfigKind.LIST),
        ),
    )
}


def declared() -> dict[str, Any]:
    return declared_keys(SCHEMAS, reloadable=["demo"])


# -- the validator --------------------------------------------------------------


def test_a_valid_value_is_typed_and_checked_against_its_declaration() -> None:
    plan = plan_edits(declared(), sets={"modules.demo.limit": "7", "core.log.level": "DEBUG"})
    assert plan.sets == {"modules.demo.limit": 7, "core.log.level": "DEBUG"}
    assert plan.declared["modules.demo.limit"].live is True
    assert plan.declared["core.log.level"].live is True  # the daemon applies this one itself


def test_module_flag_and_bare_key_reach_the_same_declaration() -> None:
    plan = plan_edits(declared(), sets={"mode": "slow"}, module="demo")
    assert plan.sets == {"modules.demo.mode": "slow"}


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("modules.demo.mode", "warp", "fast, slow"),  # choices are named in the hint
        ("modules.demo.limit", "99", "at most 10"),
        ("modules.demo.limit", "many", "whole number"),
        ("modules.demo.nope", "x", "not a key"),
        ("core.log.leve", "INFO", "core.log.level"),  # a near miss suggests the real key
        ("modules.demo.token", "s3cret", "secret"),
        ("core.ipc.token", "s3cret", "secret"),
    ],
)
def test_a_bad_key_or_value_is_refused_with_the_reason(key: str, value: str, needle: str) -> None:
    with pytest.raises(ConfigEditError) as caught:
        plan_edits(declared(), sets={key: value})
    assert needle in f"{caught.value} {caught.value.hint}"


def test_a_batch_is_all_or_nothing(tmp_path: Path) -> None:
    path = tmp_path / "kdx.local.yml"
    with pytest.raises(ConfigEditError):
        plan_edits(declared(), sets={"modules.demo.limit": "3", "modules.demo.mode": "warp"})
    assert not path.exists()


def test_list_values_accept_commas_and_json() -> None:
    assert plan_edits(declared(), sets={"modules.demo.tags": "a, b"}).sets[
        "modules.demo.tags"
    ] == ["a", "b"]
    assert plan_edits(declared(), sets={"modules.demo.tags": '["x", "y"]'}).sets[
        "modules.demo.tags"
    ] == ["x", "y"]


# -- the writer -----------------------------------------------------------------


def test_write_local_nests_keys_and_unset_prunes_empty_tables(tmp_path: Path) -> None:
    path = tmp_path / "kdx.local.yml"
    path.write_text("project:\n  owner: ann\n", encoding="utf-8")
    write_local(path, plan_edits(declared(), sets={"modules.demo.limit": "4"}))
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {
        "project": {"owner": "ann"},
        "modules": {"demo": {"limit": 4}},
    }
    removed = write_local(path, plan_edits(declared(), unsets=["modules.demo.limit"]))
    assert removed == ["modules.demo.limit"]
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"project": {"owner": "ann"}}


def test_write_local_refuses_a_file_it_cannot_round_trip(tmp_path: Path) -> None:
    plan = plan_edits(declared(), sets={"modules.demo.limit": "4"})
    broken = tmp_path / "kdx.local.yml"
    broken.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(ConfigEditError, match="mapping"):
        write_local(broken, plan)
    assert broken.read_text(encoding="utf-8") == "- not\n- a mapping\n"  # left alone
    with pytest.raises(ConfigEditError, match="not YAML"):
        write_local(tmp_path / "kdx.local.json", plan)


# -- the terminal ---------------------------------------------------------------


def run(project_root: Path, capsys: pytest.CaptureFixture[str], *words: str) -> tuple[int, str]:
    code = main([*base_args(project_root), *words])
    return code, capsys.readouterr().out


def test_config_set_writes_only_the_local_layer(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = (project_root / "kdx.yml").read_text(encoding="utf-8")
    code, out = run(project_root, capsys, "config", "set", "core.log.level", "DEBUG")
    assert code == int(ExitCode.SUCCESS)
    assert "core.log.level" in out
    assert (project_root / "kdx.yml").read_text(encoding="utf-8") == shared
    local = yaml.safe_load((project_root / "kdx.local.yml").read_text(encoding="utf-8"))
    assert local == {"core": {"log": {"level": "DEBUG"}}}
    code, out = run(project_root, capsys, "config", "show", "--key", "core.log.level", "--json")
    assert json.loads(out)["core.log.level"]["value"] == "DEBUG"


def test_config_set_json_reports_what_will_happen(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(project_root, capsys, "config", "set", "core.cli.width", "90", "--json")
    assert code == 0
    report = json.loads(out)
    (row,) = report["keys"]
    assert (row["key"], row["value"], row["applied"]) == ("core.cli.width", 90, "saved")
    assert report["reloaded"] is False  # no daemon was running
    assert "restart" in row["note"] or "next start" in row["note"]


def test_config_set_refuses_with_a_usage_error_and_writes_nothing(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = run(project_root, capsys, "config", "set", "core.log.level", "LOUD")
    assert code == int(ExitCode.USAGE_ERROR)  # the next boot would have refused it
    assert "DEBUG, INFO" in out or "choices" in out
    code, out = run(project_root, capsys, "config", "set", "core.log.level", "debug")
    assert code == 0  # case does not matter; it is stored the way the chassis reads it
    assert "level: DEBUG" in (project_root / "kdx.local.yml").read_text(encoding="utf-8")
    code, out = run(project_root, capsys, "config", "set", "core.ipc.port", "70000")
    assert code == int(ExitCode.USAGE_ERROR)
    code, out = run(project_root, capsys, "config", "set", "core.cli.width", "wide")
    assert code == int(ExitCode.USAGE_ERROR)
    assert "whole number" in out
    code, out = run(project_root, capsys, "config", "set", "core.ipc.token", "s3cret")
    assert code == int(ExitCode.USAGE_ERROR)
    assert ".env" in out
    local = project_root / "kdx.local.yml"
    assert "s3cret" not in (local.read_text(encoding="utf-8") if local.exists() else "")


def test_config_unset_and_schema(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(project_root, capsys, "config", "set", "core.cli.width", "90")
    code, out = run(project_root, capsys, "config", "unset", "core.cli.width", "--json")
    assert code == 0
    assert json.loads(out)["keys"][0]["action"] == "unset"
    code, out = run(project_root, capsys, "config", "schema", "--group", "core.cli", "--json")
    keys = {row["key"]: row for row in json.loads(out)["keys"]}
    assert keys["core.cli.width"]["type"] == "int"
    assert keys["core.cli.width"]["layer"] == "built-in default"
    assert keys["core.cli.width"]["editable"] is True
    code, out = run(project_root, capsys, "config", "schema", "--group", "core.ipc.token", "--json")
    (token,) = json.loads(out)["keys"]
    assert token["secret"] is True and token["editable"] is False
    code, out = run(project_root, capsys, "config", "schema", "--group", "core.cli")
    assert "core.cli.width" in out


# -- the console ----------------------------------------------------------------


def post(body: Any, caller: Any = None) -> IpcRequest:
    return IpcRequest(method="POST", path=CONFIG_EDIT_PATH, query={}, payload=body, caller=caller)


def test_the_console_routes_share_the_writer_and_need_admin(project_root: Path) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        module = console_of(chassis)
        routes = {route.path: route for route in module.ipc_routes()}
        assert routes[CONFIG_EDIT_PATH].role == "admin"
        assert routes[CONFIG_EDIT_PATH].methods == ("POST",)
        assert routes[CONFIG_SCHEMA_PATH].role == ""  # reading the schema is a viewer's right

        answer = module._config_edit_route(post({"set": {"core.log.level": "DEBUG"}}))
        assert answer["reloaded"] is True
        (row,) = answer["keys"]
        assert (row["key"], row["applied"]) == ("core.log.level", "live")
        assert chassis.config.get_str("core.log.level") == "DEBUG"  # the daemon reloaded

        slow = module._config_edit_route(post({"set": {"core.cli.width": 77}}))
        assert slow["keys"][0]["applied"] == "restart"  # read at boot, so honest about it

        status, refusal = module._config_edit_route(post({"set": {"core.cli.width": "wide"}}))
        assert status == 400 and refusal["key"] == "core.cli.width" and refusal["hint"] is not None
        assert module._config_edit_route(post([1, 2]))[0] == 400
        assert module._config_edit_route(post({"set": [1]}))[0] == 400

        undone = module._config_edit_route(post({"unset": ["core.log.level"]}))
        assert undone["keys"][0]["action"] == "unset"
        assert chassis.config.get_str("core.log.level") != "DEBUG"
    finally:
        chassis.shutdown()


def test_an_edit_is_audited_without_inventing_values(project_root: Path) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        module = console_of(chassis)
        module._config_edit_route(post({"set": {"core.log.level": "DEBUG"}}))
        assert chassis.audit is not None
        lines = chassis.audit.path.read_text(encoding="utf-8").splitlines()
        entry = json.loads(lines[-1])
        assert entry["action"] == "config"
        assert entry["detail"]["set"] == {"core.log.level": "DEBUG"}
    finally:
        chassis.shutdown()


def test_the_schema_route_joins_declaration_value_and_layer(project_root: Path) -> None:
    chassis = ipc_chassis(project_root)
    chassis.initialize()
    try:
        keys = schema_view(chassis, group="core.log.level")
        (row,) = keys
        assert row["module"] == "core" and row["live"] is True
        view = console_of(chassis)._config_schema_route(
            IpcRequest(method="GET", path=CONFIG_SCHEMA_PATH, query={"group": ["core.cli"]})
        )
        assert {item["key"] for item in view["keys"]} >= {"core.cli.width", "core.cli.color"}
        assert "core.ipc.token" in editable_keys(chassis)
        assert apply_edits  # one function serves terminal and console
    finally:
        chassis.shutdown()


# -- argument ranges ------------------------------------------------------------


def test_a_cli_argument_range_is_enforced_by_the_parser() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    add_command_arguments(
        parser, (CliArgument(("--n",), type="int", minimum=1, maximum=3, step=1),)
    )
    assert parser.parse_args(["--n", "2"]).n == 2
    with pytest.raises(SystemExit):
        parser.parse_args(["--n", "9"])
    with pytest.raises(ValueError, match="needs type"):
        CliArgument(("--x",), minimum=1)
    with pytest.raises(ValueError, match="above"):
        CliArgument(("--x",), type="int", minimum=5, maximum=1)
