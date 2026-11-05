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

from .. import jsonio
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
    parse_po,
)
from ..kernel.i18n_extract import (
    compose_po,
    new_po,
    parse_obsolete,
    render_pot,
    scan_tree,
    tidy_po,
    todo_items,
)
from .render import dump_json, print_key_values, print_table, write_line

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..kernel.chassis import Chassis

LOCAL_FILE_NAME = "kdx.local.yml"
JSON_ARG = CliArgument(("--json",), dest="as_json", action="store_true", help="Emit JSON.")
DOMAIN_ARG = CliArgument(("--domain",), metavar="DOMAIN", help="Only this message domain.")
LOCALE_ARG = CliArgument(("locale",), metavar="LOCALE", help="Language code, such as de or pt_BR.")


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
                name="tidy",
                help="Remove obsolete translations (strings no longer in the template).",
                handler=_tidy,
                group="core",
                remote=False,
                args=(DOMAIN_ARG,),
            ),
            CliCommand(
                name="add",
                help="Start a new language: an empty catalogue built from the template.",
                handler=_add,
                group="core",
                remote=False,
                args=(LOCALE_ARG, DOMAIN_ARG),
            ),
            CliCommand(
                name="todo",
                help="List the strings a language still needs, for a translator or an agent.",
                handler=_todo,
                group="core",
                args=(
                    LOCALE_ARG,
                    DOMAIN_ARG,
                    CliArgument(("--limit",), type="int", metavar="N",
                                help="Only the first N strings [all]."),
                    JSON_ARG,
                ),
            ),
            CliCommand(
                name="apply",
                help="Merge translations from a JSON file {source: translation}, checked.",
                handler=_apply,
                group="core",
                remote=False,
                args=(
                    LOCALE_ARG,
                    CliArgument(("file",), metavar="FILE", type="path",
                                help="JSON object mapping each source string to its translation."),
                    DOMAIN_ARG,
                    CliArgument(("--dry-run",), dest="dry_run", action="store_true",
                                help="Check the file and report; write nothing."),
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
    """Return the language service after modules had the chance to register their domains."""
    _chassis(request).ensure_initialized()
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
    translations = [loc for loc in service.locales(domain) if loc != DEFAULT_LOCALE]
    if not translations:
        write_line(f"{TAG_NOTE} " + _t(request, "no translation of {domain} found under {path}",
                                       domain=domain, path=directory))
    for locale in service.locales(domain):
        catalog = service.catalog(domain, locale)
        if catalog is None or catalog.path is None:
            continue
        before = catalog.path.read_text(encoding="utf-8")
        text, _, _ = compose_po(domain, locale, wanted, before, {})
        if text != before:
            catalog.path.write_text(text, encoding="utf-8")
        had = {entry.msgid for entry in parse_po(before)}
        added = len(messages.keys() - had)
        stale = len(had - messages.keys())
        write_line(
            f"{TAG_NOTE} "
            + _t(request, "{locale}: {added} new, {stale} obsolete", locale=locale,
                 added=added, stale=stale)
        )
    return int(ExitCode.SUCCESS)


def _tidy(request: CliRequest) -> int:
    service = _service(request)
    domain = str(request.option("domain", CORE_DOMAIN))
    if domain not in service.domains():
        known = ", ".join(sorted(service.domains()))
        message = _t(request, "no message domain named {name}; known: {known}",
                     name=repr(domain), known=known)
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.USAGE_ERROR)
    total = 0
    for locale in service.locales(domain):
        catalog = service.catalog(domain, locale)
        if catalog is None or catalog.path is None:
            continue
        template = service.domains()[domain] / f"{domain}.pot"
        before = catalog.path.read_text(encoding="utf-8")
        text = before
        if template.is_file():
            text, _, _ = compose_po(domain, locale, template.read_text(encoding="utf-8"),
                                    before, {})
        text, _ = tidy_po(text)
        removed = max(0, len(parse_po(before)) - len(parse_po(text))) + len(parse_obsolete(before))
        if text != before:
            catalog.path.write_text(text, encoding="utf-8")
        total += removed
        write_line(f"{TAG_NOTE} " + _t(request, "{locale}: {count} obsolete removed",
                                       locale=locale, count=removed))
    write_line(f"{TAG_OK} " + _t(request, "{count} obsolete translation(s) removed", count=total))
    return int(ExitCode.SUCCESS)


def _target(request: CliRequest) -> tuple[str, str, Path] | int:
    """Return (domain, locale, catalogue path) or an exit code after saying what is wrong."""
    service = _service(request)
    domain = str(request.option("domain", CORE_DOMAIN))
    directory = service.domains().get(domain)
    locale = normalize_locale(str(request.option("locale", "")))
    if directory is None:
        known = ", ".join(sorted(service.domains()))
        message = _t(request, "no message domain named {name}; known: {known}",
                     name=repr(domain), known=known)
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.USAGE_ERROR)
    if not locale or locale == DEFAULT_LOCALE:
        write_line(f"{TAG_ERROR} " + _t(request, "{value} is not a language to translate into",
                                        value=repr(str(request.option("locale", "")))))
        return int(ExitCode.USAGE_ERROR)
    return domain, locale, directory / locale / f"{domain}.po"


def _template(request: CliRequest, domain: str) -> str | None:
    path = _service(request).domains()[domain] / f"{domain}.pot"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    write_line(f"{TAG_ERROR} " + _t(request, "{path} does not exist; run kdx locale extract",
                                    path=path))
    return None


def _add(request: CliRequest) -> int:
    target = _target(request)
    if isinstance(target, int):
        return target
    domain, locale, path = target
    if path.exists():
        write_line(f"{TAG_ERROR} " + _t(request, "{path} already exists", path=path))
        return int(ExitCode.USAGE_ERROR)
    template = _template(request, domain)
    if template is None:
        return int(ExitCode.CONFIG_ERROR)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_po(domain, locale, template), encoding="utf-8")
    write_line(f"{TAG_OK} " + _t(request, "{path} created", path=path))
    write_line(f"{TAG_NOTE} " + _t(request, "next: kdx locale todo {locale} --json", locale=locale))
    return int(ExitCode.SUCCESS)


def _todo(request: CliRequest) -> int:
    target = _target(request)
    if isinstance(target, int):
        return target
    domain, locale, path = target
    template = _template(request, domain)
    if template is None:
        return int(ExitCode.CONFIG_ERROR)
    if not path.is_file():
        message = _t(request, "no catalogue for {locale}; run kdx locale add {locale}",
                     locale=locale)
        write_line(f"{TAG_ERROR} {message}")
        return int(ExitCode.USAGE_ERROR)
    items = todo_items(template, path.read_text(encoding="utf-8"))
    limit = request.option("limit", 0)
    shown = items[: int(limit)] if limit else items
    if request.flag("as_json"):
        dump_json({
            "domain": domain, "locale": locale, "remaining": len(items),
            "rules": [
                "Translate msgid into the target language; keep every {placeholder} unchanged.",
                "Do not translate command names, flags, paths, config keys, or code.",
                "Reply with a JSON object {msgid: translation} and pass it to kdx locale apply.",
            ],
            "items": [item.as_dict() for item in shown],
        })
        return int(ExitCode.SUCCESS)
    print_table(
        [[item.msgid, ", ".join(item.placeholders) or "-"] for item in shown],
        headers=("source", "placeholders"),
    )
    write_line(f"{TAG_NOTE} " + _t(request, "{count} string(s) left for {locale}",
                                  count=len(items), locale=locale))
    return int(ExitCode.SUCCESS)


def _apply(request: CliRequest) -> int:
    target = _target(request)
    if isinstance(target, int):
        return target
    domain, locale, path = target
    template = _template(request, domain)
    if template is None:
        return int(ExitCode.CONFIG_ERROR)
    source = Path(str(request.option("file", "")))
    try:
        raw = jsonio.load_path(source)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        write_line(f"{TAG_ERROR} " + _t(request, "{path} cannot be read as JSON: {reason}",
                                        path=source, reason=exc))
        return int(ExitCode.USAGE_ERROR)
    if not isinstance(raw, dict):
        write_line(f"{TAG_ERROR} " + _t(request, "{path} must hold a JSON object", path=source))
        return int(ExitCode.USAGE_ERROR)
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    text, applied, rejected = compose_po(
        domain, locale, template, current, {str(k): str(v) for k, v in raw.items()}
    )
    if not request.flag("dry_run") and applied:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    write_line(f"{TAG_OK} " + _t(request, "{count} translation(s) accepted for {locale}",
                                 count=len(applied), locale=locale))
    for msgid, reason in rejected.items():
        write_line(f"{TAG_ERROR} {msgid!r}: {reason}")
    return int(ExitCode.CONFIG_ERROR if rejected else ExitCode.SUCCESS)
