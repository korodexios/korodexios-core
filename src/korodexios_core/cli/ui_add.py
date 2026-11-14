"""``kdx ui add <tier>``: give an existing project the GUI tier ``kdx init --ui`` would seed.

The files are exactly the ones ``kdx init NAME --ui <tier>`` writes (``web/``, the ``web_app``
module of an app tier, the ``web-ui`` skill), built for the project found in ``pyproject.toml``.
Nothing that exists is overwritten: when any target file is already there, nothing is written
and the clashes are listed. Missing ignore rules for the web build are appended to
``.gitignore``.
"""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..kernel.ui_tiers import DEFAULT_TIER, tier_names
from .init_project import ProjectError, ProjectTemplate, build_files, project_from_inputs
from .ui_app import APP_TIERS, PM_COMMANDS

IGNORE_RULES = ("node_modules/", "web/dist/", "web/test-results/", "web/playwright-report/")
SKILL_PATH = ".agents/skills/web-ui.md"


@dataclass(frozen=True)
class AddResult:
    """What ``add_tier`` wrote (or would write) and what the person runs next."""

    root: Path
    tier: str
    files: list[str]
    ignore_rules: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view."""
        return {
            "root": str(self.root),
            "tier": self.tier,
            "files": list(self.files),
            "ignore_rules": list(self.ignore_rules),
            "next_steps": list(self.next_steps),
            "dry_run": self.dry_run,
        }


def project_at(root: Path, tier: str, pm: str = "") -> ProjectTemplate:
    """Return the template of the project in *root*, read from its ``pyproject.toml``."""
    pyproject = root / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProjectError(
            f"no readable pyproject.toml in {root}: {exc}",
            hint="run this in a project made by 'kdx init', or pass --path",
        ) from exc
    distribution = str(data.get("project", {}).get("name") or "").strip()
    package = distribution.replace("-", "_")
    if not distribution or not (root / "src" / package).is_dir():
        raise ProjectError(
            f"src/{package or '<package>'}/ not found next to pyproject.toml",
            hint="kdx ui add works on the layout 'kdx init' creates (src/<package>/modules/)",
        )
    template = project_from_inputs(package, ui=tier, pm=pm)
    return dataclasses.replace(template, distribution=distribution)


def tier_files(project: ProjectTemplate) -> dict[str, str]:
    """Return the GUI files ``kdx init`` writes for *project*'s tier, and nothing else."""
    web_app = f"src/{project.package}/modules/web_app/"
    return {
        path: text
        for path, text in build_files(project).items()
        if path.startswith(("web/", web_app)) or path == SKILL_PATH
    }


def _next_steps(project: ProjectTemplate) -> list[str]:
    if project.ui not in APP_TIERS:
        return [
            "serve web/ with a module's static_dirs(), or 'npm --prefix web run dev'",
            f"record the tier in docs/BRIEF.md (GUI tier: {project.ui})",
        ]
    commands = PM_COMMANDS[project.pm]
    return [
        f"cd web && {commands['install']}",
        f"{commands['run']} typecheck && {commands['run']} test",
        f"{commands['run']} build   # the web_app module serves web/dist at /app/",
        f"{project.script} ui types --out web/src/kdx/schema.ts   # with the daemon running",
        f"{project.script} ui test",
        f"record the tier in docs/BRIEF.md (GUI tier: {project.ui})",
    ]


def add_tier(root: Path, tier: str, *, pm: str = "", dry_run: bool = False) -> AddResult:
    """Write *tier*'s GUI files into the project at *root*; refuse if any already exists."""
    name = tier.strip().lower()
    if name == DEFAULT_TIER or name not in tier_names():
        choices = ", ".join(item for item in tier_names() if item != DEFAULT_TIER)
        raise ProjectError(f"cannot add GUI tier {tier!r}", hint=f"choose one of: {choices}")
    project = project_at(root, name, pm)
    files = tier_files(project)
    clashes = sorted(path for path in files if (root / path).exists())
    if clashes:
        more = f" and {len(clashes) - 5} more" if len(clashes) > 5 else ""
        shown = ", ".join(clashes[:5]) + more
        raise ProjectError(
            f"refusing to overwrite {len(clashes)} existing file(s): {shown}",
            hint="move them aside (or remove web/) and run the command again",
        )
    ignore = root / ".gitignore"
    present = set(ignore.read_text(encoding="utf-8").splitlines()) if ignore.is_file() else set()
    missing = [rule for rule in IGNORE_RULES if rule not in present]
    if not dry_run:
        for path, text in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        if missing:
            existing = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
            joiner = "" if not existing or existing.endswith("\n") else "\n"
            ignore.write_text(existing + joiner + "\n".join(missing) + "\n", encoding="utf-8")
    return AddResult(
        root=root.resolve(),
        tier=name,
        files=sorted(files),
        ignore_rules=missing,
        next_steps=_next_steps(project),
        dry_run=dry_run,
    )


__all__ = ["IGNORE_RULES", "AddResult", "add_tier", "project_at", "tier_files"]
