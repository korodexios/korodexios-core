"""Downstream feedback: obsolete merge, collisions, tri-state config, typed args, run_json."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from korodexios_core.cli.scaffold import scaffold_module
from korodexios_core.config.layered import LayeredConfig
from korodexios_core.kernel.chassis import Chassis, ChassisOptions
from korodexios_core.kernel.cli_contract import CliArgument, CliCommand, add_command_arguments
from korodexios_core.kernel.i18n import parse_po
from korodexios_core.kernel.i18n_extract import compose_po, parse_obsolete, render_pot, tidy_po
from korodexios_core.kernel.process import ProcessError

PY = sys.executable


# ------------------------------------------------------------------ 1. locale merge


def test_removed_string_becomes_obsolete_and_check_ignores_it() -> None:
    po = 'msgid ""\nmsgstr ""\n\nmsgid "keep"\nmsgstr "ostan"\n\nmsgid "drop"\nmsgstr "zahod"\n'
    text, _, _ = compose_po("d", "sk", render_pot("d", {"keep": "a.py:1"}), po, {})
    assert {e.msgid for e in parse_po(text)} == {"keep"}
    assert parse_obsolete(text) == {"drop": "zahod"}
    again, _, _ = compose_po("d", "sk", render_pot("d", {"keep": "a.py:1"}), text, {})
    assert parse_obsolete(again) == {"drop": "zahod"}  # idempotent
    assert parse_obsolete(tidy_po(again)[0]) == {}


def test_obsolete_translation_is_revived_when_the_string_returns() -> None:
    po = 'msgid ""\nmsgstr ""\n\n#~ msgid "back"\n#~ msgstr "spat"\n'
    text, _, _ = compose_po("d", "sk", render_pot("d", {"back": "a.py:1"}), po, {})
    entry = parse_po(text)[0]
    assert entry.msgstr == "spat" and entry.fuzzy


# ------------------------------------------------------------------ 3. tri-state config


def test_get_bool_opt_distinguishes_unset_from_false() -> None:
    unset = LayeredConfig.load(config_file=None, env_file=None, environ={})
    assert unset.get_bool_opt("app.watermark") is None
    assert unset.get_bool("app.watermark") is False
    off = LayeredConfig.load(
        config_file=None, env_file=None, environ={"KORODEXIOS_APP__WATERMARK": "false"}
    )
    assert off.get_bool_opt("app.watermark") is False
    on = LayeredConfig.load(
        config_file=None, env_file=None, environ={"KORODEXIOS_APP__WATERMARK": "yes"}
    )
    assert on.get_bool_opt("app.watermark") is True
    assert on.get_int_opt("app.missing") is None and on.get_str_opt("app.missing") is None


# ------------------------------------------------------------------ 5. typed arguments


def test_positional_and_append_arguments_honour_their_type() -> None:
    parser = argparse.ArgumentParser()
    add_command_arguments(
        parser,
        (
            CliArgument(("count",), type="int"),
            CliArgument(("--n",), dest="n", action="append", type="int"),
            CliArgument(("--ratio",), type="float"),
        ),
    )
    ns = parser.parse_args(["7", "--n", "1", "--n", "2", "--ratio", "0.5"])
    assert ns.count == 7 and ns.n == [1, 2] and ns.ratio == 0.5


# ------------------------------------------------------------------ 6. run_json


def test_run_json_returns_an_object_and_explains_failures(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path))
    ctx = chassis.context
    big = "import json;print(json.dumps({'rows': list(range(500))}, indent=1))"
    assert len(ctx.run_json([PY, "-c", big])["rows"]) == 500  # not cut at the stderr tail
    with pytest.raises(ProcessError, match=r"exited 3.*boom") as failed:
        ctx.run_json([PY, "-c", "import sys;sys.stderr.write('boom');sys.exit(3)"])
    assert failed.value.result is not None and failed.value.result.returncode == 3
    with pytest.raises(ProcessError, match="no valid JSON"):
        ctx.run_json([PY, "-c", "print('nope')"])
    with pytest.raises(ProcessError, match="not an object"):
        ctx.run_json([PY, "-c", "print('[1]')"])
    with pytest.raises(ProcessError, match="timed out"):
        ctx.run_json([PY, "-c", "import time;time.sleep(30)"], timeout=0.3)


# ------------------------------------------------------------------ 4. module new


def test_scaffold_pins_core_and_writes_project_test(tmp_path: Path) -> None:
    modules = tmp_path / "src" / "acme" / "modules"
    result = scaffold_module(modules, "orders", project_root=tmp_path)
    manifest = (modules / "orders" / "kdx_module.toml").read_text(encoding="utf-8")
    assert 'requires_core = ">=0.7,<1.0"' in manifest
    assert "def config_schema" in (modules / "orders" / "module.py").read_text(encoding="utf-8")
    assert result.test_file is not None
    assert "from acme.modules.orders.module import MODULE" in result.test_file.read_text(
        encoding="utf-8"
    )


# ------------------------------------------------------------------ 2. collisions


def test_registry_reports_a_module_claiming_a_core_verb(tmp_path: Path) -> None:
    chassis = Chassis.create(ChassisOptions(home=tmp_path))
    chassis.ensure_initialized()
    registry = chassis.registry

    class Claimant:
        name = "claimant"

    from korodexios_core.kernel.contract import ModuleSpec
    from korodexios_core.kernel.registry import RegisteredModule

    record = RegisteredModule(spec=ModuleSpec(name="claimant"), module=Claimant())
    record.commands = [CliCommand(name="jobs", help="x", handler=lambda request: 0)]
    registry._records["claimant"] = record
    rows = registry.cli_collisions({"jobs"})
    assert rows == [{"name": "jobs", "owner": "core", "module": "claimant"}]
