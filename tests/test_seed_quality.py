"""The quality files 'kdx init' seeds: they pass on a fresh project and catch the real breaks."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

from korodexios_core.cli.init_project import COVERAGE_FLOOR, build_files, project_from_inputs


def seeded(tmp_path: Path, name: str) -> Path:
    files = build_files(project_from_inputs(name))  # type: ignore[arg-type]
    for relative in ("tests/test_rules.py", f"src/{name}/__init__.py"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[relative], encoding="utf-8")
    return tmp_path


def write(root: Path, name: str, relative: str, text: str) -> None:
    target = root / "src" / name / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def run_rules(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         str(root / "tests" / "test_rules.py")],
        cwd=root, capture_output=True, text=True, check=False,
    )


def test_a_clean_project_passes_the_rules(tmp_path: Path) -> None:
    root = seeded(tmp_path, "clean_one")
    write(root, "clean_one", "modules/alpha/module.py", "from clean_one import __version__\n")
    write(root, "clean_one", "modules/alpha/helper.py", "from .module import __version__\n")
    write(root, "clean_one", "modules/beta/module.py", "import json\n\nVALUE = json\n")
    assert run_rules(root).returncode == 0


def test_a_module_importing_a_sibling_module_fails(tmp_path: Path) -> None:
    root = seeded(tmp_path, "cross_one")
    write(root, "cross_one", "modules/beta/module.py", "VALUE = 1\n")
    write(root, "cross_one", "modules/alpha/module.py",
          "from cross_one.modules.beta import module\n")
    write(root, "cross_one", "modules/alpha/other.py", "from ..beta import module\n")
    write(root, "cross_one", "modules/alpha/third.py", "from .. import beta\n")
    result = run_rules(root)
    assert result.returncode != 0
    assert "alpha/module.py imports cross_one.modules.beta" in result.stdout
    assert "alpha/other.py imports cross_one.modules.beta" in result.stdout
    assert "alpha/third.py imports cross_one.modules.beta" in result.stdout


def test_stubs_and_todo_comments_fail_but_documented_no_ops_do_not(tmp_path: Path) -> None:
    root = seeded(tmp_path, "stub_one")
    write(root, "stub_one", "logic.py", '''\
from typing import Protocol, overload


def later() -> None:
    pass


def never() -> int:
    raise NotImplementedError("soon")


def dots() -> None:
    ...


def fine() -> None:
    """Documented on purpose: this surface renders nothing."""


class Shape(Protocol):
    def area(self) -> float: ...


@overload
def pick(value: int) -> int: ...
@overload
def pick(value: str) -> str: ...
def pick(value: int | str) -> int | str:
    return value


def noted() -> str:
    return "a TODO inside a string is only text"  # TODO: but this comment is a marker
''')
    result = run_rules(root)
    assert result.returncode != 0
    for expected in ("later() is a placeholder", "never() is a placeholder",
                     "dots() is a placeholder", "TODO marker"):
        assert expected in result.stdout, expected
    for allowed in ("fine()", "area()", "pick()"):
        assert allowed not in result.stdout, allowed


def test_the_seed_measures_coverage_audits_and_offers_hooks() -> None:
    files = build_files(project_from_inputs("quality_one"))  # type: ignore[arg-type]
    pyproject = tomllib.loads(files["pyproject.toml"])
    dev = " ".join(pyproject["project"]["optional-dependencies"]["dev"])
    for tool in ("pytest-cov", "pip-audit", "pre-commit"):
        assert tool in dev
    assert pyproject["tool"]["coverage"]["report"]["fail_under"] == COVERAGE_FLOOR
    assert pyproject["tool"]["coverage"]["run"]["source"] == ["quality_one"]
    makefile = files["Makefile"]
    assert "pytest -q --cov" in makefile
    assert "pip_audit" in makefile and "pre_commit install" in makefile
    workflow = files[".github/workflows/ci.yml"]
    assert "pip-audit" in workflow and "agents sync --check" in workflow
    assert "--cov" in workflow
    assert "make check" in files[".pre-commit-config.yaml"]
    assert "Size is a trigger, not a cap" in files["AGENTS.md"]
