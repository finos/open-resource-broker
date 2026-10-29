"""Tests for the security-exception audit script's OSV ID validation.

Covers:
- VULN_ID_RE accepts the known OSV/vulnerability ID shapes and rejects
  anything else, in particular values that could otherwise be used to
  build an unexpected request URL
- query_osv() rejects an invalid ID before opening any connection
"""

import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure dev-tools/ci is importable
_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from check_security_exceptions import (  # type: ignore[import]
    _OSV_OPENER,
    VULN_ID_RE,
    check_exception,
    fetch_requires_python,
    find_fixes,
    load_allow_ghsas,
    load_trivyignore_entries,
    needs_newer_python,
    project_minimum_python,
    python_lower_bound,
    query_osv,
)


@pytest.mark.unit
class TestVulnIdValidation:
    @pytest.mark.parametrize(
        "vuln_id",
        [
            "CVE-2026-53615",
            "GHSA-8mgp-746c-j5xp",
            "DEBIAN-CVE-2026-53615",
            "PYSEC-2026-2078",
        ],
    )
    def test_accepts_known_id_shapes(self, vuln_id):
        assert VULN_ID_RE.match(vuln_id)

    @pytest.mark.parametrize(
        "vuln_id",
        [
            "file:///etc/passwd",
            "../../etc/passwd",
            "CVE-2026",
            "not-an-id",
            "",
        ],
    )
    def test_rejects_unknown_id_shapes(self, vuln_id):
        assert VULN_ID_RE.match(vuln_id) is None

    def test_query_osv_never_opens_connection_for_invalid_id(self):
        with patch.object(_OSV_OPENER, "open") as mock_open:
            result = query_osv("file:///etc/passwd")
        assert result is None
        mock_open.assert_not_called()

    def test_opener_refuses_non_https_schemes(self):
        for url in ("file:///etc/passwd", "ftp://example.invalid/x", "http://example.invalid/"):
            with pytest.raises(urllib.error.URLError):
                _OSV_OPENER.open(url)


def _workflow(tmp_path, body):
    path = tmp_path / "security-code.yml"
    path.write_text(body)
    return path


@pytest.mark.unit
class TestLoadAllowGhsas:
    IDS = ["GHSA-aaaa-bbbb-cccc", "GHSA-dddd-eeee-ffff", "GHSA-gggg-hhhh-jjjj"]

    @pytest.mark.parametrize("indicator", [">-", ">", "|", "|-"])
    def test_block_scalar_spanning_several_lines(self, tmp_path, indicator):
        path = _workflow(
            tmp_path,
            "jobs:\n  review:\n    with:\n"
            f"      allow-ghsas: {indicator}\n"
            "        GHSA-aaaa-bbbb-cccc, GHSA-dddd-eeee-ffff,\n"
            "        GHSA-gggg-hhhh-jjjj\n"
            "      other-input: value\n",
        )
        assert load_allow_ghsas(path) == self.IDS

    def test_inline_value(self, tmp_path):
        path = _workflow(
            tmp_path, "with:\n  allow-ghsas: 'GHSA-aaaa-bbbb-cccc, GHSA-dddd-eeee-ffff'\n"
        )
        assert load_allow_ghsas(path) == self.IDS[:2]

    def test_plain_multiline_value(self, tmp_path):
        path = _workflow(
            tmp_path,
            "with:\n  allow-ghsas:\n    GHSA-aaaa-bbbb-cccc\n    GHSA-dddd-eeee-ffff\nnext: 1\n",
        )
        assert load_allow_ghsas(path) == self.IDS[:2]

    def test_missing_key_or_file(self, tmp_path):
        assert load_allow_ghsas(_workflow(tmp_path, "with:\n  other: 1\n")) == []
        assert load_allow_ghsas(tmp_path / "absent.yml") == []


@pytest.mark.unit
def test_trivyignore_entries_carry_their_purls(tmp_path):
    path = tmp_path / ".trivyignore.yaml"
    path.write_text(
        "vulnerabilities:\n"
        "  - id: CVE-2026-1\n"
        "    purls:\n"
        "      - pkg:deb/debian/libacl1\n"
        "      - pkg:deb/debian/login\n"
        "    statement: x\n"
        "  - id: CVE-2026-2\n"
        "    purls:\n"
        "      - pkg:pypi/Some_Pkg\n"
        "  - id: CVE-2026-3\n"
        "    statement: no purls\n"
    )
    assert load_trivyignore_entries(path) == [
        ("CVE-2026-1", ["pkg:deb/debian/libacl1", "pkg:deb/debian/login"]),
        ("CVE-2026-2", ["pkg:pypi/Some_Pkg"]),
        ("CVE-2026-3", []),
    ]


def _record(ecosystem, name, fixed="1.2.3", aliases=()):
    return {
        "aliases": list(aliases),
        "affected": [
            {
                "package": {"ecosystem": ecosystem, "name": name},
                "ranges": [{"events": [{"introduced": "0"}, {"fixed": fixed}]}],
            }
        ],
    }


@pytest.mark.unit
class TestCheckExceptionEcosystems:
    @pytest.fixture(autouse=True)
    def _no_network(self):
        with patch("check_security_exceptions.fetch_requires_python", return_value=None):
            yield

    def _lookup(self, records):
        requested = []

        def fake_query(vuln_id):
            requested.append(vuln_id)
            return records.get(vuln_id)

        return requested, patch("check_security_exceptions.query_osv", side_effect=fake_query)

    def test_debian_cve_is_looked_up_as_debian_record(self):
        requested, patcher = self._lookup(
            {"DEBIAN-CVE-2026-1": _record("Debian:13", "acl", "2.3.2-3")}
        )
        with patcher:
            fixes = check_exception("CVE-2026-1", ["pkg:deb/debian/libacl1"])
        assert requested == ["DEBIAN-CVE-2026-1"]
        assert fixes == [("acl", "2.3.2-3")]

    def test_pypi_cve_is_never_looked_up_as_debian(self):
        requested, patcher = self._lookup(
            {
                "CVE-2026-2": {"aliases": ["GHSA-cccc-ffff-gggg"], "affected": []},
                "GHSA-cccc-ffff-gggg": _record("PyPI", "Some-Pkg", "4.2.1"),
            }
        )
        with patcher:
            fixes = check_exception("CVE-2026-2", ["pkg:pypi/some_pkg"])
        assert not any(vuln_id.startswith("DEBIAN-") for vuln_id in requested)
        assert requested == ["CVE-2026-2", "GHSA-cccc-ffff-gggg"]
        assert fixes == [("Some-Pkg", "4.2.1")]

    def test_pypi_fix_for_another_package_is_ignored(self):
        _, patcher = self._lookup(
            {
                "CVE-2026-2": {"aliases": ["GHSA-cccc-ffff-gggg"], "affected": []},
                "GHSA-cccc-ffff-gggg": _record("PyPI", "other-package"),
            }
        )
        with patcher:
            assert check_exception("CVE-2026-2", ["pkg:pypi/some-pkg"]) == []

    def test_cve_without_purl_is_not_assumed_to_be_debian(self):
        requested, patcher = self._lookup({})
        with patcher:
            check_exception("CVE-2026-3", [])
        assert requested == ["CVE-2026-3"]

    def test_ghsa_is_looked_up_under_its_own_id(self):
        requested, patcher = self._lookup({"GHSA-cccc-ffff-gggg": _record("PyPI", "x")})
        with patcher:
            assert check_exception("GHSA-cccc-ffff-gggg") == [("x", "1.2.3")]
        assert requested == ["GHSA-cccc-ffff-gggg"]


@pytest.mark.unit
class TestPyPiFixesNeedingNewerPython:
    @pytest.fixture(autouse=True)
    def _minimum_is_310(self):
        fetch_requires_python.cache_clear()
        with patch("check_security_exceptions.project_minimum_python", return_value=(3, 10)):
            yield
        fetch_requires_python.cache_clear()

    @pytest.mark.parametrize(
        ("specifier", "expected"),
        [(">=3.11", (3, 11)), (">=3.9,<4", (3, 9)), (">=3", (3, 0)), ("<4", None), (None, None)],
    )
    def test_lower_bound(self, specifier, expected):
        assert python_lower_bound(specifier) == expected

    def test_project_minimum_is_read_from_pyproject(self, tmp_path):
        path = tmp_path / "pyproject.toml"
        path.write_text('[project]\nname = "x"\nrequires-python = ">=3.10"\n')
        assert project_minimum_python(path) == (3, 10)

    def test_fix_needing_newer_python_is_suppressed(self):
        with patch("check_security_exceptions.fetch_requires_python", return_value=">=3.11"):
            assert find_fixes(_record("PyPI", "filelock", "4.2.1")) == []

    def test_fix_without_bound_is_reported(self):
        with patch("check_security_exceptions.fetch_requires_python", return_value=None):
            assert find_fixes(_record("PyPI", "filelock", "4.2.1")) == [("filelock", "4.2.1")]
        with patch("check_security_exceptions.fetch_requires_python", return_value=">=3.8"):
            assert find_fixes(_record("PyPI", "filelock", "4.2.1")) == [("filelock", "4.2.1")]

    def test_lookup_failure_is_reported(self):
        with patch.object(_OSV_OPENER, "open", side_effect=urllib.error.URLError("down")):
            assert fetch_requires_python("filelock", "4.2.1") is None
            assert find_fixes(_record("PyPI", "filelock", "4.2.9")) == [("filelock", "4.2.9")]

    def test_unparseable_response_is_reported(self):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"not json"

        with patch.object(_OSV_OPENER, "open", return_value=_Response()):
            assert needs_newer_python("filelock", "4.2.2") is False

    def test_invalid_name_or_version_never_opens_connection(self):
        with patch.object(_OSV_OPENER, "open") as mock_open:
            assert fetch_requires_python("../x", "1.0") is None
            assert fetch_requires_python("pkg", "1/../../x") is None
        mock_open.assert_not_called()

    def test_non_pypi_fixes_are_not_checked(self):
        with patch("check_security_exceptions.fetch_requires_python") as fetch:
            assert find_fixes(_record("Debian:13", "acl", "2.3.2-3")) == [("acl", "2.3.2-3")]
        fetch.assert_not_called()
