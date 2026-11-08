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

PACKAGE = "korodexios_core"
SOURCE = Path(__file__).resolve().parents[1] / "src" / PACKAGE
MODULES = SOURCE / "modules"
MARKER = re.compile(r"\bTODO\b")
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
    assert not problems, "\n".join(sorted(problems))


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
    assert not problems, "\n".join(problems)
