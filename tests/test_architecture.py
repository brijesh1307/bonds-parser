"""Layering rules (docs/03_lld.md §0, docs/13_development_plan.md §1, NFR-16).

The engine and exporters are pure: they must never depend on the web framework, the database
or the service layer, so they stay testable on their own and reusable from a CLI or batch job.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

FORBIDDEN = {
    "engine": ("fastapi", "starlette", "sqlalchemy", "app.api", "app.services", "app.db", "app.export"),
    "export": ("fastapi", "starlette", "sqlalchemy", "app.api", "app.services", "app.db"),
    "db": ("fastapi", "starlette", "app.api", "app.services"),
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_package_respects_layering(package: str) -> None:
    violations = []
    for path in sorted((APP / package).rglob("*.py")):
        for name in _imports(path):
            if any(name == bad or name.startswith(bad + ".") for bad in FORBIDDEN[package]):
                violations.append(f"{path.relative_to(APP.parent)} imports {name}")
    assert not violations, "layering violations:\n" + "\n".join(violations)
