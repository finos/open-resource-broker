"""Task 1744: Domain purity test — no framework coupling.

Asserts that no file under src/orb/domain/ imports pydantic.

All 13 current violations are tracked as the ratchet ceiling in
violation_counts.json.  This test uses the whitelist approach so it passes
today while preventing any NEW pydantic coupling from being introduced.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_DOMAIN_DIR = SRC_ORB / "domain"

# Current known pydantic violations in the domain layer.
# Remove entries from here as they are cleaned up — the test will then enforce
# that the cleaned file stays clean.
_KNOWN_PYDANTIC_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("domain/template/template_aggregate.py", "pydantic"),
        ("domain/template/extensions.py", "pydantic"),
        ("domain/machine/aggregate.py", "pydantic"),
        ("domain/machine/machine_metadata.py", "pydantic"),
        ("domain/machine/machine_identifiers.py", "pydantic"),
        ("domain/request/aggregate.py", "pydantic"),
        ("domain/request/request_metadata.py", "pydantic"),
        ("domain/request/request_identifiers.py", "pydantic"),
        ("domain/base/value_objects.py", "pydantic"),
        ("domain/base/entity.py", "pydantic"),
        ("domain/base/events/base_events.py", "pydantic"),
        ("domain/base/events/domain_events.py", "pydantic"),
        ("domain/base/events/provider_events.py", "pydantic"),
    }
)

_DOMAIN_FILES = collect_python_files(_DOMAIN_DIR)


@pytest.mark.parametrize("filepath", _DOMAIN_FILES, ids=lambda p: str(p.relative_to(SRC_ORB)))
@pytest.mark.unit
@pytest.mark.architecture
def test_domain_file_has_no_new_pydantic_import(filepath: Path) -> None:
    """Domain file must not introduce NEW pydantic imports."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp == "pydantic" or imp.startswith("pydantic."))
        and (rel, "pydantic") not in _KNOWN_PYDANTIC_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW pydantic imports (domain should use plain dataclasses): {new_violations}"
    )
