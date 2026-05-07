"""Architecture boundary test: metadata vs provider_data ownership.

Rule (see docs/root/architecture/metadata-vs-provider-data.md):
  - provider_data: only provider adapters (src/orb/providers/) write to it.
    Domain aggregate methods that own the field definition are also allowed.
  - metadata: the application layer writes to it.
    Provider adapters must NOT write to aggregate metadata.

Three tests enforce this:

  A. test_provider_data_writes_restricted_to_providers
     Every write to .provider_data on an object must originate inside
     src/orb/providers/ or inside a domain aggregate file (aggregate.py).

  B. test_metadata_writes_not_in_providers
     No file under src/orb/providers/ may write to aggregate .metadata.

  C. test_violations_inventory_is_known
     The current violation set must exactly equal the frozen baseline in
     metadata_provider_data_violations.json.  New violations fail loudly;
     fixed violations also fail so the baseline is kept tight.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.unit.architecture.conftest import SRC_ORB

_PROVIDERS_DIR = SRC_ORB / "providers"
_VIOLATIONS_FILE = Path(__file__).parent / "metadata_provider_data_violations.json"

# Functions whose bodies are exempt from the metadata-write rule because they
# perform on-read migration of legacy data.
_NORMALIZE_EXEMPT_FUNCS = frozenset({"_normalize_on_read"})


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


class _WriteCollector(ast.NodeVisitor):
    """Collect attribute-write sites for a named field within a source file."""

    def __init__(self, field: str) -> None:
        self._field = field
        self._func_stack: list[str] = []
        self.writes: list[tuple[int, list[str]]] = []  # (lineno, enclosing_funcs)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def _record(self, lineno: int) -> None:
        self.writes.append((lineno, list(self._func_stack)))

    def _is_target(self, node: ast.expr) -> bool:
        """Return True if *node* is a write target for self._field."""
        # obj.field = ...
        if isinstance(node, ast.Attribute) and node.attr == self._field:
            return True
        # obj.field["key"] = ...
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == self._field
        ):
            return True
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if self._is_target(t):
                self._record(node.lineno)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if self._is_target(node.target):
            self._record(node.lineno)
        self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        # obj.field.update(...) / .pop() / .clear() / .setdefault()
        if isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in {"update", "setdefault", "pop", "clear"}
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == self._field
            ):
                self._record(node.lineno)
        self.generic_visit(node)


def _collect_writes(filepath: Path, field: str) -> list[tuple[int, list[str]]]:
    """Return all write sites for *field* in *filepath* as (lineno, funcs) pairs."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []
    collector = _WriteCollector(field)
    collector.visit(tree)
    return collector.writes


def _in_normalize_exempt(funcs: list[str]) -> bool:
    return bool(_NORMALIZE_EXEMPT_FUNCS.intersection(funcs))


# ---------------------------------------------------------------------------
# Violation scanners (return sorted lists of "rel/path.py:lineno" strings)
# ---------------------------------------------------------------------------


def _scan_provider_data_violations() -> list[str]:
    """Find provider_data writes outside providers/ and outside aggregate.py."""
    violations: list[str] = []
    for py_file in sorted(SRC_ORB.rglob("*.py")):
        in_providers = py_file.is_relative_to(_PROVIDERS_DIR)
        in_aggregate = py_file.name == "aggregate.py"
        if in_providers or in_aggregate:
            continue
        for lineno, funcs in _collect_writes(py_file, "provider_data"):
            if _in_normalize_exempt(funcs):
                continue
            violations.append(f"{py_file.relative_to(SRC_ORB)}:{lineno}")
    return sorted(violations)


def _scan_metadata_violations() -> list[str]:
    """Find metadata writes inside providers/ (excluding _normalize_on_read)."""
    violations: list[str] = []
    for py_file in sorted(_PROVIDERS_DIR.rglob("*.py")):
        for lineno, funcs in _collect_writes(py_file, "metadata"):
            if _in_normalize_exempt(funcs):
                continue
            violations.append(f"{py_file.relative_to(SRC_ORB)}:{lineno}")
    return sorted(violations)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.architecture
class TestMetadataProviderDataBoundary:
    """Enforce the metadata / provider_data ownership rule."""

    def test_provider_data_writes_restricted_to_providers(self) -> None:
        """provider_data must only be written by provider adapters or domain aggregates.

        Application, infrastructure, interface, and CLI layers must not write
        directly to aggregate.provider_data — they must call the aggregate
        method (set_provider_data / with_launch_template_info / etc.) or
        delegate to a provider.
        """
        violations = _scan_provider_data_violations()
        assert not violations, (
            f"provider_data written outside providers/ or aggregate.py "
            f"({len(violations)} site(s)):\n" + "\n".join(f"  src/orb/{v}" for v in violations)
        )

    def test_metadata_writes_not_in_providers(self) -> None:
        """Provider adapters must not write to aggregate metadata.

        Providers own provider_data. Writing provider-specific fields into
        metadata leaks cloud concepts into the generic application layer and
        makes the two dicts interchangeable, which is the problem this rule
        prevents.

        Known violations are tracked in metadata_provider_data_violations.json
        and will be fixed by a separate migration.
        """
        baseline = _load_baseline()
        known = set(baseline["metadata_writes_in_providers"])
        current = set(_scan_metadata_violations())

        new_violations = current - known
        assert not new_violations, (
            f"New metadata write(s) introduced inside providers/ "
            f"({len(new_violations)} site(s)):\n"
            + "\n".join(f"  src/orb/{v}" for v in sorted(new_violations))
            + "\nFix the violation or, if intentional, update "
            "metadata_provider_data_violations.json."
        )

    def test_violations_inventory_is_known(self) -> None:
        """Current violation set must exactly match the frozen baseline.

        Both new violations (additions) and fixed violations (removals) cause
        this test to fail.  When a violation is fixed, remove it from
        metadata_provider_data_violations.json to lock in the improvement.
        """
        baseline = _load_baseline()

        current_pd = set(_scan_provider_data_violations())
        known_pd = set(baseline["provider_data_writes_outside_providers"])

        current_md = set(_scan_metadata_violations())
        known_md = set(baseline["metadata_writes_in_providers"])

        messages: list[str] = []

        new_pd = current_pd - known_pd
        if new_pd:
            messages.append(
                f"New provider_data violations not in baseline ({len(new_pd)}):\n"
                + "\n".join(f"  src/orb/{v}" for v in sorted(new_pd))
            )

        fixed_pd = known_pd - current_pd
        if fixed_pd:
            messages.append(
                f"provider_data violations fixed but still in baseline ({len(fixed_pd)}) "
                f"— remove them from metadata_provider_data_violations.json:\n"
                + "\n".join(f"  src/orb/{v}" for v in sorted(fixed_pd))
            )

        new_md = current_md - known_md
        if new_md:
            messages.append(
                f"New metadata violations not in baseline ({len(new_md)}):\n"
                + "\n".join(f"  src/orb/{v}" for v in sorted(new_md))
            )

        fixed_md = known_md - current_md
        if fixed_md:
            messages.append(
                f"metadata violations fixed but still in baseline ({len(fixed_md)}) "
                f"— remove them from metadata_provider_data_violations.json:\n"
                + "\n".join(f"  src/orb/{v}" for v in sorted(fixed_md))
            )

        assert not messages, "\n\n".join(messages)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_baseline() -> dict:
    return json.loads(_VIOLATIONS_FILE.read_text(encoding="utf-8"))
