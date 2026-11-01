"""'kdx txn': inspect, recover, and undo file transactions from the terminal.

The journals live under the durable directory (``data/txn``), so they survive ``kdx reset``.
A module that runs transactions builds its own :class:`TransactionManager`; this command
reads the same journal directory, so an operator can see what a crash left behind and settle
it without writing code.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.txn import Receipt, TransactionManager, TxnError
from .render import dump_json, print_key_values, print_table, tr, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

JOURNAL_DIR_NAME = "txn"
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
ID_ARG = CliArgument(("txn_id",), metavar="ID", help="Transaction id (see 'kdx txn list').")


def txn_command() -> CliCommand:
    """Return the 'kdx txn' command group."""
    return CliCommand(
        name="txn",
        help="List, recover, and undo file transactions.",
        group="core",
        remote=False,
        subcommands=(
            CliCommand(
                name="list",
                help="List transactions, newest first; unfinished ones are marked.",
                handler=_list,
                group="core",
                remote=False,
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="show",
                help="Show one transaction and what each file hashed to.",
                handler=_show,
                group="core",
                remote=False,
                args=(ID_ARG, JSON_ARG),
            ),
            CliCommand(
                name="recover",
                help="Finish or roll back every transaction a crash left in flight.",
                handler=_recover,
                group="core",
                remote=False,
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="undo",
                help="Take back a finished transaction (refused if its files changed since).",
                handler=_undo,
                group="core",
                remote=False,
                args=(ID_ARG, JSON_ARG),
            ),
        ),
    )


def _manager(request: CliRequest) -> TransactionManager:
    chassis = cast("Chassis", request.options.get("chassis") or request.context.chassis)
    paths = chassis.paths
    return TransactionManager(Path(paths.root), Path(paths.durable_dir) / JOURNAL_DIR_NAME)


def _when(stamp: float) -> str:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M:%S") if stamp else "-"


def _fail(exc: TxnError) -> int:
    write_line(f"{TAG_ERROR} [{exc.code}] {exc}")
    return int(ExitCode.ERROR)


def _list(request: CliRequest) -> int:
    receipts = _manager(request).receipts()
    if request.flag("as_json"):
        dump_json({"transactions": [item.as_dict() for item in receipts]})
        return int(ExitCode.SUCCESS)
    if not receipts:
        write_line(f"{TAG_NOTE} " + tr("no transactions recorded"))
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [item.txn_id, item.phase, str(len(item.ops)), _when(item.created_at), item.label]
            for item in receipts
        ],
        headers=("id", "phase", "files", "started", "label"),
    )
    unfinished = [
        item for item in receipts if item.phase in ("prepared", "backed_up", "applied", "committed")
    ]
    if unfinished:
        write_line(
            f"{TAG_NOTE} " + tr("{count} unfinished; run: kdx txn recover", count=len(unfinished))
        )
    return int(ExitCode.SUCCESS)


def _show(request: CliRequest) -> int:
    try:
        receipt = _manager(request).receipt(str(request.option("txn_id")))
    except TxnError as exc:
        return _fail(exc)
    if request.flag("as_json"):
        dump_json(receipt.as_dict())
        return int(ExitCode.SUCCESS)
    _print(receipt)
    return int(ExitCode.SUCCESS)


def _print(receipt: Receipt) -> None:
    print_key_values(
        [
            ("id", receipt.txn_id),
            ("phase", receipt.phase),
            ("label", receipt.label or "-"),
            ("started", _when(receipt.created_at)),
        ]
    )
    print_table(
        [
            [
                str(op["path"]),
                str(op["action"]),
                str(op["before"] or "-")[:12],
                str(op["after"] or "-")[:12],
            ]
            for op in receipt.ops
        ],
        headers=("path", "action", "before", "after"),
    )


def _recover(request: CliRequest) -> int:
    outcomes = _manager(request).recover()
    if request.flag("as_json"):
        dump_json({"outcomes": [item.as_dict() for item in outcomes]})
    elif not outcomes:
        write_line(f"{TAG_OK} " + tr("nothing to recover"))
    else:
        for item in outcomes:
            tag = TAG_ERROR if item.action == "failed" else TAG_OK
            write_line(f"{tag} {item.txn_id}: {item.action} {item.detail}".rstrip())
    return (
        int(ExitCode.ERROR)
        if any(item.action == "failed" for item in outcomes)
        else int(ExitCode.SUCCESS)
    )


def _undo(request: CliRequest) -> int:
    try:
        receipt = _manager(request).undo(str(request.option("txn_id")))
    except TxnError as exc:
        return _fail(exc)
    if request.flag("as_json"):
        dump_json(receipt.as_dict())
    else:
        write_line(f"{TAG_OK} " + tr("{id} undone", id=receipt.txn_id))
    return int(ExitCode.SUCCESS)
