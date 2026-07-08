"""Architecture test: boto3 and botocore must not be imported outside providers/aws/.

Importing boto3 or botocore directly in core or infrastructure modules couples the
entire application to the [aws] extra even when it is not installed.  All boto3/botocore
usage must be confined to src/orb/providers/aws/.
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

_PROVIDERS_AWS_DIR = SRC_ORB / "providers" / "aws"

# All source files that live outside providers/aws/
_NON_AWS_FILES = [
    f
    for f in collect_python_files(SRC_ORB)
    if not f.is_relative_to(_PROVIDERS_AWS_DIR) and str(f) not in EXCEPTION_PATHS
]

# Top-level module names that constitute direct AWS SDK imports
_AWS_SDK_MODULES = frozenset({"boto3", "botocore"})

# Known violations — files currently allowed to import boto3/botocore outside providers/aws/.
# This set should remain empty; add entries only as a last resort with a tracking comment.
_KNOWN_VIOLATIONS: frozenset[tuple[str, str]] = frozenset()


@pytest.mark.parametrize(
    "filepath",
    _NON_AWS_FILES,
    ids=lambda p: str(p.relative_to(SRC_ORB)),
)
@pytest.mark.unit
@pytest.mark.architecture
def test_no_boto3_outside_aws_provider(filepath: Path) -> None:
    """boto3/botocore imports must not appear outside src/orb/providers/aws/."""
    rel = str(filepath.relative_to(SRC_ORB))
    imports = extract_imports(filepath)
    new_violations = [
        imp
        for imp in imports
        if (imp in _AWS_SDK_MODULES or any(imp.startswith(f"{m}.") for m in _AWS_SDK_MODULES))
        and (rel, imp) not in _KNOWN_VIOLATIONS
    ]
    assert new_violations == [], (
        f"{rel} imports boto3/botocore outside providers/aws/ — "
        f"move to providers/aws/ or guard with try/except ImportError. "
        f"Violations: {new_violations}"
    )
