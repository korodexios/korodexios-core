"""Describe a declared CLI argument the way the web pages render it as a form field."""

from __future__ import annotations

from typing import Any

from ...kernel.cli_contract import CliArgument


def describe_argument(argument: CliArgument) -> dict[str, Any]:
    """Describe one declared argument the way the page renders a form field."""
    flag_like = argument.type == "flag" or argument.action in ("store_true", "store_false")
    default = argument.default
    if default is not None and not isinstance(default, str | int | float | bool | list):
        default = str(default)
    return {
        "dest": argument.destination(),
        "flags": list(argument.flags),
        "positional": argument.is_positional,
        "kind": "flag" if flag_like else argument.type,
        "action": argument.action,
        "nargs": argument.nargs,
        "choices": list(argument.choices),
        "default": default,
        "required": argument.required,
        "help": argument.help,
        "metavar": argument.metavar,
        "minimum": argument.minimum,
        "maximum": argument.maximum,
        "step": argument.step,
    }
