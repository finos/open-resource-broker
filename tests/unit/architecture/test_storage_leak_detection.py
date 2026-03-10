"""Task 1743: Storage leak detection tests.

Asserts that no file OUTSIDE src/orb/infrastructure/storage/ imports
storage-backend internals (boto3.dynamodb, DynamoDB, SQLAlchemy internals).

DI registration files are whitelisted.
Known violations in providers/ (boto3 for AWS SDK usage) are also whitelisted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.architecture.conftest import (
    EXCEPTION_PATHS,
    SRC_ORB,
    collect_python_files,
    extract_imports,
)

_STORAGE_DIR = SRC_ORB / "infrastructure" / "storage"
# providers/ is allowed to use boto3/botocore — that is their job
_PROVIDERS_DIR = SRC_ORB / "providers"

_NON_STORAGE_FILES = [
    f
    for f in collect_python_files(SRC_ORB)
    if not f.is_relative_to(_STORAGE_DIR)
    and not f.is_relative_to(_PROVIDERS_DIR)
    and str(f) not in EXCEPTION_PATHS
]

# Import fragments that indicate storage-backend internals leaking out of the
# storage layer into non-storage, non-provider code.
_STORAGE_FORBIDDEN = (
    "boto3.dynamodb",
    "boto3.resources",
    "botocore.exceptions",
    "sqlalchemy",
)

# Known violations — files outside storage/ and providers/ that currently
# reference storage backends but are not yet cleaned up.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("infrastructure/utilities/factories/sql_engine_factory.py", "sqlalchemy"),
        ("infrastructure/utilities/factories/sql_engine_factory.py", "sqlalchemy.engine"),
        ("infrastructure/utilities/factories/sql_engine_factory.py", "sqlalchemy.orm"),
        ("infrastructure/utilities/factories/sql_engine_factory.py", "sqlalchemy.pool"),
    }
)


@pytest.mark.parametrize(
    "filepath", _NON_STORAGE_FILES, ids=lambda p: str(p.relative_to(SRC_ORB))
)
@pytest.mark.unit
@pytest.mark.architecture
def test_no_new_storage_leak(filepath: Path) -> None:
    """Non-storage file must not introduce NEW storage-backend imports."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if any(imp == frag or imp.startswith(frag + ".") for frag in _STORAGE_FORBIDDEN)
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} has NEW storage leaks (not in known-violations whitelist): {new_violations}"
    )
