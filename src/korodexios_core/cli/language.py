"""'kdx locale': see, choose, and verify the language of the chassis and its projects.

The terminal is the whole feature; the web console only reads the same service. The
choice is stored as ``core.locale`` in ``kdx.local.yml`` (machine-local, gitignored) and
follows the usual precedence: ``--locale sk``, ``KDX_CORE__LOCALE``, ``kdx.local.yml``,
``kdx.yml``, the default.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from ..config.layered import ConfigSource
from ..constants import TAG_ERROR, TAG_NOTE, TAG_OK, ExitCode
from ..kernel.cli_contract import CliArgument, CliCommand, CliRequest
from ..kernel.i18n import (
    AUTO_LOCALE,
    CORE_DOMAIN,
    DEFAULT_LOCALE,
    CatalogReport,
    I18n,
    normalize_locale,
)
from ..kernel.i18n_extract import merge_po, render_pot, scan_tree, stale_entries
from .render import dump_json, print_key_values, print_table, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

LOCAL_FILE_NAME = "kdx.local.yml"
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
DOMAIN_ARG = CliArgument(("--domain",), metavar="DOMAIN", help="Only this message domain.")


def locale_command() -> CliCommand:
    """Return the 'kdx locale' command group."""
    return CliCommand(
        name="locale",
        help="Show, choose, and verify the language (message catalogues).",
        group="core",
        subcommands=(
            CliCommand(
                name="show",
                help="Print the active language and where the choice came from.",
                handler=_show,
                group="core",
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="list",
                help="List the languages that have a catalogue, per message domain.",
                handler=_list,
                group="core",
                args=(JSON_ARG,),
            ),
            CliCommand(
                name="set",
                help="Choose the language for this machine (writes kdx.local.yml).",
                handler=_set,
                group="core",
                remote=False,
                args=(
                    CliArgument(
                        ("locale",), metavar="LOCALE",
                        help=f"A language code such as sk or sk_SK, or '{AUTO_LOCALE}'.",
                    ),
                ),
            ),
            CliCommand(
                name="extract",
                help="Collect translatable strings into the template and update translations.",
                handler=_extract,
                group="core",
                remote=False,
                args=(
                    DOMAIN_ARG,
                    CliArgument(("--source",), metavar="DIR", type="path",
                                help="Python source to scan [the chassis package]."),
                    CliArgument(("--check",), dest="check_only", action="store_true",
                                help="Change nothing; exit 3 when the template is out of date."),
                ),
            ),
            CliCommand(
                name="check",
                help="Compare catalogues with their templates; exit 3 when one is incomplete.",
                handler=_check,
                group="core",
                args=(
                    DOMAIN_ARG,
                    CliArgument(("--language",), dest="only_locale", metavar="LOCALE",
                                help="Only this language."),
                    JSON_ARG,
                ),
            ),
        ),
    )


def _chassis(request: CliRequest) -> Chassis:
    chassis = getattr(request.context, "chassis", None) or request.options.get("chassis")
    if chassis is None:
        raise RuntimeError("locale commands require a chassis bound to the request context")
    return cast("Chassis", chassis)


def _service(request: CliRequest) -> I18n:
    return request.context.i18n


def _t(request: CliRequest, message: str, **fields: Any) -> str:
    return request.context.translate(message, **fields)


def _origin(chassis: Chassis, request: CliRequest) -> str:
    source = chassis.config.source_of("core.locale")
    if source is None or source == ConfigSource.DEFAULT:
        return _t(request, "built-in default")
    return str(chassis.config.origin_of("core.locale"))


def _show(request: CliRequest) -> int:
    chassis = _chassis(request)
    service = _service(request)
    configured = chassis.config.get_str("core.locale", DEFAULT_LOCALE)
    payload: dict[str, Any] = {
        "locale": service.locale,
        "configured": configured,
        "origin": _origin(chassis, request),
        "available": list(service.locales()),
        "domains": sorted(service.domains()),
    }
    if request.flag("as_json"):
        dump_json(payload)
        return int(ExitCode.SUCCESS)
    print_key_values(
        [
            ("language", service.locale),
            ("configured", configured),
            ("chosen by", payload["origin"]),
            ("available", ", ".join(payload["available"])),
            ("message domains", ", ".join(payload["domains"])),
        ]
    )
    return int(ExitCode.SUCCESS)


def _list(request: CliRequest) -> int:
    service = _service(request)
    rows = {domain: list(service.locales(domain)) for domain in sorted(service.domains())}
    if request.flag("as_json"):
        dump_json({"active": service.locale, "domains": rows})
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [domain, ", ".join(found), "*" if service.locale in found else "-"]
            for domain, found in rows.items()
        ],
        headers=("domain", "languages", "active"),
    )
    return int(ExitCode.SUCCESS)


def _local_file(chassis: Chassis) -> Path:
    configured = chassis.config.local_config_file
    return Path(configured) if configured else Path(chassis.paths.root) / LOCAL_FILE_NAME


def _set(request: CliRequest) -> int:
    chassis = _chassis(request)
    wanted = str(request.option("locale", "")).strip()
    chosen = AUTO_LOCALE if wanted.lower() == AUTO_LOCALE else normalize_locale(wanted)
    if not chosen:
        write_line(
            f"{TAG_ERROR} "
            + _t(request, "{value} is not a language code; use a form such as sk or sk_SK, "
                 "or 'auto'", value=repr(wanted))
        )
        return int(ExitCode.USAGE_ERROR)
    path = _local_file(chassis)
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        write_line(
            f"{TAG_ERROR} "
            + _t(request, "{path} cannot be read, so it was left alone: {reason}",
                 path=path, reason=exc)
        )
        return int(ExitCode.CONFIG_ERROR)
    data: dict[str, Any] = dict(loaded) if isinstance(loaded, dict) else {}
    core = dict(data.get("core") or {}) if isinstance(data.get("core"), dict) else {}
    core["locale"] = chosen
    data["core"] = core
    try:
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    except OSError as exc:
        write_line(
            f"{TAG_ERROR} "
            + _t(request, "{path} could not be written: {reason}", path=path, reason=exc)
        )
        return int(ExitCode.ERROR)
    write_line(
        f"{TAG_OK} "
        + _t(request, "core.locale = {value} saved in {path}", value=chosen, path=path)
    )
    winner = chassis.config.source_of("core.locale")
    if winner is not None and winner > ConfigSource.LOCAL:
        write_line(
            f"{TAG_NOTE} "
            + _t(request, "{origin} still wins over kdx.local.yml; the new value applies "
                 "once it is removed", origin=_origin(chassis, request))
        )
    return int(ExitCode.SUCCESS)


def _reports(request: CliRequest) -> list[CatalogReport]:
    service = _service(request)
    domain = request.option("domain", "")
    only = request.option("only_locale", "")
    names = [str(domain)] if domain else sorted(service.domains())
    reports: list[CatalogReport] = []
    for name in names:
        reports.extend(service.check(name, normalize_locale(str(only)) or None))
    return reports


def _check(request: CliRequest) -> int:
    service = _service(request)
    domain = str(request.option("domain", ""))
    if domain and domain not in service.domains():
        known = ", ".join(sorted(service.domains()))
        message = _t(request, "no message domain named {name}; known: {known}",
                     name=repr(domain), known=known)
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.USAGE_ERROR)
    reports = _reports(request)
    incomplete = [report for report in reports if not report.complete]
    if request.flag("as_json"):
        dump_json({"reports": [report.as_dict() for report in reports],
                   "complete": not incomplete})
        return int(ExitCode.CONFIG_ERROR if incomplete else ExitCode.SUCCESS)
    if not reports:
        message = _t(request, "no translations to check; only the source language exists")
        write_line(f"{TAG_NOTE} {message}")
        return int(ExitCode.SUCCESS)
    print_table(
        [
            [
                report.domain, report.locale, f"{report.percent}%",
                len(report.untranslated), len(report.fuzzy),
                len(report.obsolete), len(report.broken_fields),
            ]
            for report in reports
        ],
        headers=("domain", "language", "done", "missing", "fuzzy", "obsolete", "broken"),
    )
    for report in incomplete:
        for label, items in (
            ("missing", report.untranslated), ("fuzzy", report.fuzzy),
            ("obsolete", report.obsolete), ("broken placeholders", report.broken_fields),
        ):
            for text in items[:8]:
                write_line(f"{TAG_NOTE} {report.domain}/{report.locale} {label}: {text!r}")
    if incomplete:
        message = _t(request, "{count} catalogue(s) need attention", count=len(incomplete))
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.CONFIG_ERROR)
    write_line(f"{TAG_OK} " + _t(request, "every catalogue is complete"))
    return int(ExitCode.SUCCESS)


def _extract(request: CliRequest) -> int:
    service = _service(request)
    domain = str(request.option("domain", CORE_DOMAIN))
    directory = service.domains().get(domain)
    if directory is None:
        known = ", ".join(sorted(service.domains()))
        message = _t(request, "no message domain named {name}; known: {known}",
                     name=repr(domain), known=known)
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.USAGE_ERROR)
    default_source = Path(__file__).resolve().parent.parent
    source = Path(str(request.option("source", default_source)))
    if not source.is_dir():
        write_line(f"{TAG_ERROR} " + _t(request, "{path} is not a directory", path=source))
        return int(ExitCode.USAGE_ERROR)
    messages = scan_tree(source)
    template = directory / f"{domain}.pot"
    wanted = render_pot(domain, messages)
    current = template.read_text(encoding="utf-8") if template.exists() else ""
    if request.flag("check_only"):
        if current == wanted:
            write_line(f"{TAG_OK} " + _t(request, "{path} is up to date", path=template))
            return int(ExitCode.SUCCESS)
        write_line(f"{TAG_ERROR} " + _t(request, "{path} is out of date; run kdx locale extract",
                                        path=template))
        return int(ExitCode.CONFIG_ERROR)
    directory.mkdir(parents=True, exist_ok=True)
    template.write_text(wanted, encoding="utf-8")
    write_line(f"{TAG_OK} " + _t(request, "{count} strings written to {path}",
                                 count=len(messages), path=template))
    for locale in service.locales(domain):
        catalog = service.catalog(domain, locale)
        if catalog is None or catalog.path is None:
            continue
        text, added = merge_po(catalog.path.read_text(encoding="utf-8"), messages)
        if added:
            catalog.path.write_text(text, encoding="utf-8")
        stale = len(stale_entries(text, messages))
        write_line(
            f"{TAG_NOTE} "
            + _t(request, "{locale}: {added} new, {stale} obsolete", locale=locale,
                 added=added, stale=stale)
        )
    return int(ExitCode.SUCCESS)
