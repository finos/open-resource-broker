#!/usr/bin/env python3
"""Assert that workflow fallback version strings match .project.yml python.versions.

Run from the repository root:
    python3 dev-tools/ci/check_python_version_drift.py

Exit 0 on success, 1 on drift detected.

Checked files
-------------
- .github/workflows/test-matrix.yml       — four ``|| '[...]'`` fallback strings
- makefiles/common.mk                      — fallback ``echo`` values for yq failures
- .github/workflows/package-testing.yml   — matrix fallback list + min/max include entries
"""

import json
import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not available; install it or run inside the project venv")
    sys.exit(2)

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent

# --- Load canonical version list from .project.yml ---
project_cfg = yaml.safe_load((ROOT / ".project.yml").read_text())
canonical: list[str] = [str(v) for v in project_cfg["python"]["versions"]]
canonical_set = sorted(canonical)

# --- Files to check with their expected representation ---
# Each entry: (file path, description, regex that extracts individual version strings)
CHECKS = [
    (
        ROOT / ".github" / "workflows" / "test-matrix.yml",
        "test-matrix.yml fallback strings",
        # Matches patterns like: '["3.10", "3.11", "3.12", "3.13", "3.14"]'
        r'"(3\.\d+)"',
    ),
    (
        ROOT / "makefiles" / "common.mk",
        "common.mk fallback echo values",
        # Matches patterns like: echo "3.10 3.11 3.12 3.13 3.14"
        r"\b(3\.\d+)\b",
    ),
    (
        ROOT / ".github" / "workflows" / "package-testing.yml",
        "package-testing.yml matrix fallback list",
        # Matches the || '[...]' fallback on the python-version matrix axis,
        # e.g. '["3.10", "3.11", "3.12", "3.13", "3.14"]'
        r'"(3\.\d+)"',
    ),
]

drift_found = False

for fpath, description, pattern in CHECKS:
    if not fpath.exists():
        print(f"SKIP: {fpath.relative_to(ROOT)} not found")
        continue

    content = fpath.read_text()
    found_all = re.findall(pattern, content)

    # Keep only versions that are in the expected set (ignore unrelated version mentions)
    expected_as_set = set(canonical)
    found_versions = sorted(set(v for v in found_all if v in expected_as_set))

    if not found_versions:
        # No fallback strings found — nothing to assert against
        print(f"INFO: {fpath.relative_to(ROOT)} — no version fallback strings found, skipping")
        continue

    if found_versions != canonical_set:
        print(
            f"FAIL: {description} ({fpath.relative_to(ROOT)})\n"
            f"      found:    {found_versions}\n"
            f"      expected: {canonical_set}"
        )
        drift_found = True
    else:
        print(f"OK:   {description} matches .project.yml: {canonical_set}")

# --- Extra checks for package-testing.yml min/max include entries ---
# The matrix 'include' block hardcodes the oldest and newest supported versions
# for the minimal/all extras.  These must stay pinned to the boundary versions
# of .project.yml python.versions rather than being out of date.
pkg_testing = ROOT / ".github" / "workflows" / "package-testing.yml"
if pkg_testing.exists():
    pkg_content = pkg_testing.read_text()
    canonical_min = canonical_set[0]  # e.g. "3.10"
    canonical_max = canonical_set[-1]  # e.g. "3.14"

    # The include block for minimal/all uses python-version: "X.YY" lines.
    # We collect all such literals inside the include block and check that
    # the min and max of canonical appear there (we do not enforce a full-set
    # match because the include block intentionally only pins boundaries).
    # Pattern: python-version: "3.NN" lines found anywhere in the file
    # (the include block is the only place single version pins appear).
    include_versions = re.findall(r'python-version:\s+"(3\.\d+)"', pkg_content)
    include_set = sorted(set(include_versions))

    if not include_set:
        print(
            "INFO: package-testing.yml — no single-version include entries found, skipping min/max check"
        )
    else:
        min_ok = canonical_min in include_set
        max_ok = canonical_max in include_set
        if not min_ok or not max_ok:
            print(
                f"FAIL: package-testing.yml include entries min/max mismatch\n"
                f"      include pins:      {include_set}\n"
                f"      expected min/max:  {canonical_min} / {canonical_max}"
            )
            drift_found = True
        else:
            print(
                f"OK:   package-testing.yml include entries contain expected min/max: "
                f"{canonical_min} / {canonical_max}"
            )

if drift_found:
    print(
        f"\nPython version drift detected.\n"
        f"Update the fallback strings in the files above to match "
        f".project.yml python.versions = {json.dumps(canonical)}"
    )
    sys.exit(1)
else:
    print(f"\nAll fallback version strings are in sync with .project.yml: {canonical_set}")
    sys.exit(0)
