"""Shared helpers for architecture boundary tests."""

from __future__ import annotations

import ast
from pathlib import Path

# Root of the orb source tree — resolved relative to this file's location
_TESTS_ARCH_DIR = Path(__file__).parent
_REPO_ROOT = _TESTS_ARCH_DIR.parent.parent.parent
SRC_ORB = _REPO_ROOT / "src" / "orb"


def collect_python_files(directory: Path) -> list[Path]:
    """Return all .py files under *directory*, sorted for stable parametrize IDs."""
    return sorted(directory.rglob("*.py"))


def extract_imports(filepath: Path) -> list[str]:
    """AST-parse *filepath* and return every imported module name."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


# Paths that are explicitly allowed to cross certain boundaries because they
# perform DI wiring (registering concrete implementations against ports).
EXCEPTION_PATHS: frozenset[str] = frozenset(
    str(p)
    for p in collect_python_files(SRC_ORB / "infrastructure" / "di")
)
