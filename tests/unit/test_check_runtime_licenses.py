"""Tests for the runtime license gate's SPDX expression evaluator.

Covers:
- The self-test cases the gate is required to pass (compound SPDX
  expressions, classifier strings, UNKNOWN)
- pip-licenses (JSON array) and license-checker-rseidelsohn (JSON object)
  input formats are both recognised
- Package exclusion by name
"""

import sys
from pathlib import Path

import pytest

# Ensure dev-tools/ci is importable
_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from check_runtime_licenses import check, is_allowed, iter_packages  # type: ignore[import]


@pytest.mark.unit
class TestIsAllowed:
    """Each case the gate's --self-test suite is required to cover."""

    @pytest.mark.parametrize(
        ("license_str", "expected"),
        [
            ("MIT AND PSF-2.0", True),
            ("MIT AND GPL-3.0-or-later", False),
            ("(MIT OR CC0-1.0)", True),
            ("GPL-2.0-only OR MIT", True),
            ("GNU Lesser General Public License v2 or later (LGPLv2+)", False),
            ("UNKNOWN", False),
            ("Apache Software License; BSD License", True),
            ("3-Clause BSD License", True),
            ("New BSD License", True),
            ("Simplified BSD License", True),
            ("Apache Software License; 3-Clause BSD License", True),
            ("LGPL-2.1-or-later", False),
        ],
    )
    def test_required_cases(self, license_str, expected):
        assert is_allowed(license_str) is expected

    def test_bare_spdx_id_is_allowed(self):
        assert is_allowed("MIT") is True
        assert is_allowed("Apache-2.0") is True

    def test_bare_disallowed_spdx_id_fails(self):
        assert is_allowed("GPL-3.0-or-later") is False

    def test_none_license_fails(self):
        assert is_allowed(None) is False

    def test_empty_string_fails(self):
        assert is_allowed("") is False

    def test_with_exception_not_explicitly_listed_fails(self):
        assert is_allowed("GPL-2.0-or-later WITH Bison-exception-2.2") is False

    def test_nested_parentheses(self):
        assert is_allowed("(MIT AND (Apache-2.0 OR BSD-3-Clause))") is True

    def test_classifier_with_parens_matches_flat_not_expression(self):
        # "ISC License (ISCL)" must match the classifier allowlist directly,
        # not be mistaken for an SPDX expression just because it has parens.
        assert is_allowed("ISC License (ISCL)") is True


@pytest.mark.unit
class TestBsdAliasNormalisation:
    """Non-SPDX BSD spellings that packaging metadata emits in practice."""

    @pytest.mark.parametrize(
        "license_str",
        [
            "3-Clause BSD License",
            "3-clause bsd license",
            "BSD 3-Clause",
            "BSD-3-Clause License",
            "BSD 3-Clause License",
            "New BSD License",
            "Modified BSD License",
            "Revised BSD License",
        ],
    )
    def test_bsd_3_clause_aliases_allowed(self, license_str):
        assert is_allowed(license_str) is True

    @pytest.mark.parametrize(
        "license_str",
        [
            "2-Clause BSD License",
            "BSD 2-Clause",
            "Simplified BSD License",
            "FreeBSD License",
        ],
    )
    def test_bsd_2_clause_aliases_allowed(self, license_str):
        assert is_allowed(license_str) is True

    def test_alias_inside_classifier_set_is_normalised(self):
        assert is_allowed("Apache Software License; 3-Clause BSD License") is True

    def test_unknown_license_string_still_fails(self):
        # A string that merely contains "BSD" but isn't a recognised BSD
        # alias (or allowlisted outright) must not be allowed just because
        # it resembles one.
        assert is_allowed("GNU General Public License v3 (GPLv3)") is False


@pytest.mark.unit
class TestIterPackages:
    def test_pip_licenses_format(self):
        data = [
            {"Name": "requests", "Version": "2.0.0", "License": "Apache-2.0"},
            {"Name": "orb-py", "Version": "1.0.0", "License": "Apache-2.0"},
        ]
        assert iter_packages(data) == [
            ("requests", "Apache-2.0"),
            ("orb-py", "Apache-2.0"),
        ]

    def test_npm_format(self):
        data = {
            "undici@6.28.1": {"licenses": "MIT"},
            "@aws-sdk/types@3.974.2": {"licenses": "Apache-2.0"},
        }
        assert sorted(iter_packages(data)) == sorted(
            [
                ("undici", "MIT"),
                ("@aws-sdk/types", "Apache-2.0"),
            ]
        )

    def test_unrecognised_format_raises(self):
        with pytest.raises(ValueError):
            iter_packages("not a report")


@pytest.mark.unit
class TestCheck:
    def test_ignores_named_package(self):
        data = [
            {"Name": "orb-py", "Version": "1.0.0", "License": "Apache-2.0"},
            {"Name": "chardet", "Version": "5.2.0", "License": "LGPL-2.1-or-later"},
        ]
        checked, violations = check(data, ignore_packages={"chardet"})
        assert checked == 1
        assert violations == []

    def test_reports_violation(self):
        data = [{"Name": "chardet", "Version": "5.2.0", "License": "LGPL-2.1-or-later"}]
        checked, violations = check(data, ignore_packages=set())
        assert checked == 1
        assert violations == [("chardet", "LGPL-2.1-or-later")]

    def test_all_allowed_reports_no_violations(self):
        data = [{"Name": "requests", "Version": "2.0.0", "License": "Apache-2.0"}]
        checked, violations = check(data, ignore_packages=set())
        assert checked == 1
        assert violations == []
