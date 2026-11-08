"""The quality files ``kdx init`` seeds: rules measured by tests instead of hoped for.

Three small files, all standard library, so a new project starts honest:

* ``tests/test_size_ratchet.py`` - the size triggers of conventions section 2; the excess is
  recorded and may only shrink.
* ``tests/test_rules.py`` - modules never import each other, and no stubs or TODO markers.
* ``.pre-commit-config.yaml`` - the same checks before a commit and a push.

The core runs the very same two tests against itself (``tests/``), so what it asks of a
project it also asks of its own code.
"""

from __future__ import annotations

_SIZE_RATCHET = '''\
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

SOURCE = Path(__file__).resolve().parents[1] / "src" / "__PACKAGE__"
FILE_LINES = 400
FUNCTION_LINES = 40
ARGUMENTS = 5

FILES: dict[str, int] = {}
"""File (relative to the package) -> its line count, for files over FILE_LINES."""
FUNCTIONS: dict[str, int] = {}
"""'file::function' -> its line count, for functions over FUNCTION_LINES."""
SIGNATURES: dict[str, int] = {}
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
    assert not problems, "\\n".join(problems)
'''

_RULES = '''\
"""Rules an agent or a person can break without noticing, measured instead of hoped for.

1. Modules never import each other (conventions section 2): a module talks through the event
   bus, the registry or the control plane, so any module can be removed or replaced alone.
2. No stubs (conventions section 2): no function whose whole body is ``pass``, ``...`` or
   ``raise NotImplementedError``, and no TODO marker in a comment under ``src/``.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

PACKAGE = "__PACKAGE__"
SOURCE = Path(__file__).resolve().parents[1] / "src" / PACKAGE
MODULES = SOURCE / "modules"
MARKER = re.compile(r"\\bTODO\\b")
EXEMPT_DECORATORS = {"abstractmethod", "overload"}


def _imported(path: Path, tree: ast.AST) -> list[str]:
    """Dotted names the file imports, with relative imports resolved against its package."""
    package = [PACKAGE, *path.relative_to(SOURCE).with_suffix("").parts][:-1]
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module.split(".") if node.module else []
            if node.level:
                module = [*package[: len(package) - (node.level - 1)], *module]
            names.append(".".join(module))
            names += [".".join([*module, alias.name]) for alias in node.names]
    return names


def test_modules_never_import_each_other() -> None:
    """A module may import the core and plain libraries, never a sibling module."""
    problems: set[str] = set()
    for path in sorted(MODULES.rglob("*.py")) if MODULES.is_dir() else []:
        relative = path.relative_to(MODULES)
        if len(relative.parts) < 2:
            continue
        owner = relative.parts[0]
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for name in _imported(path, tree):
            parts = name.split(".")
            if parts[:2] == [PACKAGE, "modules"] and len(parts) > 2 and parts[2] != owner:
                problems.add(f"{relative.as_posix()} imports {name}")
    assert not problems, "\\n".join(sorted(problems))


def _is_protocol(node: ast.ClassDef) -> bool:
    for base in node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        if isinstance(target, ast.Name) and target.id == "Protocol":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "Protocol":
            return True
    return False


def _decorated_exempt(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
        if name in EXEMPT_DECORATORS:
            return True
    return False


def _is_placeholder(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [
        statement
        for statement in node.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and isinstance(statement.value.value, str)
        )
    ]
    if len(body) != 1:
        # A body of only a docstring is a documented no-op (a null object), not a stub.
        return False
    only = body[0]
    if isinstance(only, ast.Pass):
        return True
    if isinstance(only, ast.Expr):
        return isinstance(only.value, ast.Constant) and only.value.value is Ellipsis
    if isinstance(only, ast.Raise) and only.exc is not None:
        raised = only.exc.func if isinstance(only.exc, ast.Call) else only.exc
        return isinstance(raised, ast.Name) and raised.id == "NotImplementedError"
    return False


def test_no_stubs_and_no_todo_markers() -> None:
    """Every branch that exists runs: no placeholder bodies, no TODO comments."""
    problems: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        name = path.relative_to(SOURCE).as_posix()
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        protocols = {
            id(member)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and _is_protocol(node)
            for member in node.body
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if id(node) in protocols or _decorated_exempt(node):
                continue
            if _is_placeholder(node):
                problems.append(f"{name}:{node.lineno} {node.name}() is a placeholder")
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT and MARKER.search(token.string):
                problems.append(f"{name}:{token.start[0]} TODO marker")
    assert not problems, "\\n".join(problems)
'''

_PRE_COMMIT = """\
# Optional: `make hooks` installs these. They run the same checks as `make check`,
# so a broken change is stopped before it is committed or pushed.
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff check
        entry: uv run ruff check src tests
        language: system
        pass_filenames: false
      - id: kdx-docs
        name: seeded documents and agent rule files are current
        entry: uv run kdx docs check
        language: system
        pass_filenames: false
      - id: kdx-agents
        name: agent rule files match AGENTS.md
        entry: uv run kdx agents sync --check
        language: system
        pass_filenames: false
      - id: make-check
        name: make check
        entry: uv run make check
        language: system
        pass_filenames: false
        stages: [pre-push]
"""


def size_ratchet_test(package: str) -> str:
    """Return ``tests/test_size_ratchet.py`` for *package* with every baseline at zero."""
    return _SIZE_RATCHET.replace("__PACKAGE__", package)


def rules_test(package: str) -> str:
    """Return ``tests/test_rules.py`` for *package*."""
    return _RULES.replace("__PACKAGE__", package)


def coverage_config(package: str, floor: int) -> list[str]:
    """Return the ``[tool.coverage.*]`` lines of the seeded ``pyproject.toml``."""
    return [
        "[tool.coverage.run]",
        f'source = ["{package}"]',
        "",
        "[tool.coverage.report]",
        "# A ratchet, not a report: after adding tests, raise it to one point under the",
        "# measured total. Never lower it to get a change through.",
        f"fail_under = {floor}",
        "",
    ]


def pre_commit_config() -> str:
    """Return ``.pre-commit-config.yaml``."""
    return _PRE_COMMIT
