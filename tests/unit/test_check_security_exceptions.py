"""Tests for the security-exception audit script's OSV ID validation.

Covers:
- VULN_ID_RE accepts the known OSV/vulnerability ID shapes and rejects
  anything else, in particular values that could otherwise be used to
  build an unexpected request URL
- query_osv() rejects an invalid ID before ever calling urlopen
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure dev-tools/ci is importable
_DEV_TOOLS_PATH = str(Path(__file__).parents[2] / "dev-tools" / "ci")
if _DEV_TOOLS_PATH not in sys.path:
    sys.path.insert(0, _DEV_TOOLS_PATH)

from check_security_exceptions import VULN_ID_RE, query_osv  # type: ignore[import]


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

    def test_query_osv_never_calls_urlopen_for_invalid_id(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            result = query_osv("file:///etc/passwd")
        assert result is None
        mock_urlopen.assert_not_called()
