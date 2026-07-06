"""Tests for AuditLogMiddleware — correlation-ID sanitization and log-injection prevention."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orb.api.middleware._utils import get_or_generate_correlation_id, sanitize_header_value
from orb.api.middleware.audit_log_middleware import AuditLogMiddleware

# ---------------------------------------------------------------------------
# Unit tests for the _utils helpers used by AuditLogMiddleware
# ---------------------------------------------------------------------------


class TestSanitizeHeaderValue:
    """sanitize_header_value strips C0 control characters and enforces length cap."""

    def test_clean_value_unchanged(self):
        assert sanitize_header_value("abc-123") == "abc-123"

    def test_cr_stripped(self):
        assert sanitize_header_value("id\r\ninjected") == "idinjected"

    def test_lf_stripped(self):
        assert sanitize_header_value("id\ninjected") == "idinjected"

    def test_null_byte_stripped(self):
        assert sanitize_header_value("id\x00bad") == "idbad"

    def test_del_stripped(self):
        assert sanitize_header_value("id\x7fbad") == "idbad"

    def test_all_c0_controls_stripped(self):
        # Build a string with all C0 controls (0x00–0x1f)
        controls = "".join(chr(i) for i in range(0x20))
        assert sanitize_header_value(f"a{controls}b") == "ab"

    def test_truncated_to_128_chars(self):
        long_value = "x" * 200
        result = sanitize_header_value(long_value)
        assert len(result) == 128

    def test_empty_string_returned_unchanged(self):
        assert sanitize_header_value("") == ""

    def test_unicode_not_stripped(self):
        # Non-ASCII printable characters should not be removed
        value = "corr-id-éà"
        assert sanitize_header_value(value) == value

    def test_mixed_attack_payload(self):
        """Typical log-injection payload is neutralized."""
        payload = "legit\r\nfake-field: injected"
        result = sanitize_header_value(payload)
        assert "\r" not in result
        assert "\n" not in result
        assert result == "legitfake-field: injected"[:128]

    def test_c1_control_chars_stripped(self):
        """C1 control characters (U+0080–U+009F) are stripped."""
        # Build a string with the full C1 range
        c1_chars = "".join(chr(i) for i in range(0x80, 0xA0))
        result = sanitize_header_value(f"a{c1_chars}b")
        assert result == "ab"

    def test_unicode_line_separator_stripped(self):
        """U+2028 LINE SEPARATOR is stripped (log-injection risk in some parsers)."""
        value = "id\u2028injected"
        result = sanitize_header_value(value)
        assert "\u2028" not in result
        assert result == "idinjected"

    def test_unicode_paragraph_separator_stripped(self):
        """U+2029 PARAGRAPH SEPARATOR is stripped."""
        value = "id\u2029injected"
        result = sanitize_header_value(value)
        assert "\u2029" not in result
        assert result == "idinjected"

    def test_length_cap_preserved_after_c1_strip(self):
        """128-char cap still applies after C1/separator stripping."""
        # A long string with C1 chars interspersed — after stripping, result should cap at 128
        value = "x\x85" * 200  # \x85 is C1 NEL (next line)
        result = sanitize_header_value(value)
        assert "\x85" not in result
        assert len(result) <= 128


class TestGetOrGenerateCorrelationId:
    """get_or_generate_correlation_id returns sanitized value or a uuid4 fallback."""

    def _req(self, header_value=None):
        from unittest.mock import MagicMock

        r = MagicMock()
        r.headers.get = lambda k, d=None: header_value if k == "x-correlation-id" else d
        return r

    def test_returns_sanitized_header_when_present(self):
        req = self._req("corr-abc")
        result = get_or_generate_correlation_id(req)
        assert result == "corr-abc"

    def test_strips_control_chars(self):
        req = self._req("corr\r\ninjected")
        result = get_or_generate_correlation_id(req)
        assert "\r" not in result
        assert "\n" not in result

    def test_generates_uuid4_when_header_absent(self):
        req = self._req(None)
        result = get_or_generate_correlation_id(req)
        # Should look like a UUID4 (36 chars with hyphens)
        import re

        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            result,
        ), f"Expected UUID4, got {result!r}"

    def test_generates_uuid4_when_header_only_controls(self):
        req = self._req("\r\n\x00")
        result = get_or_generate_correlation_id(req)
        import re

        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            result,
        )

    def test_uses_fallback_when_header_empty_and_fallback_given(self):
        req = self._req("")
        result = get_or_generate_correlation_id(req, fallback="req-xyz")
        assert result == "req-xyz"

    def test_uuid4_generated_when_fallback_also_empty(self):
        req = self._req("")
        result = get_or_generate_correlation_id(req, fallback="")
        import re

        assert re.match(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            result,
        )


# ---------------------------------------------------------------------------
# Integration tests: AuditLogMiddleware records sanitized correlation_id
# ---------------------------------------------------------------------------


class TestAuditLogMiddlewareIntegration:
    """Audit-log middleware sanitizes correlation ID before writing to log."""

    def _make_app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(AuditLogMiddleware)

        @app.post("/api/v1/machines/request")
        def create():
            return {"ok": True}

        return app

    def test_audit_log_written_for_post(self, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client = TestClient(self._make_app())
            resp = client.post(
                "/api/v1/machines/request",
                headers={"x-correlation-id": "clean-id-123"},
            )
        assert resp.status_code == 200
        # Verify an audit record was emitted
        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) >= 1

    def test_injected_correlation_id_stripped(self, caplog):
        """A CR/LF in X-Correlation-ID must not reach the audit log field."""
        import logging

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            client = TestClient(self._make_app())
            client.post(
                "/api/v1/machines/request",
                headers={"x-correlation-id": "legit\r\nfake-log-field: injected"},
            )

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) >= 1
        record = audit_records[0]
        corr = getattr(record, "correlation_id", None)
        if corr:
            assert "\r" not in corr
            assert "\n" not in corr

    def test_get_request_not_audited(self, caplog):
        """Safe verbs on non-sensitive paths are not logged."""
        import logging

        with caplog.at_level(logging.INFO, logger="orb.audit"):
            # Create a GET endpoint specifically for this test
            app = FastAPI()
            app.add_middleware(AuditLogMiddleware)

            @app.get("/api/data")
            def get_data():
                return {}

            tc = TestClient(app)
            tc.get("/api/data")

        audit_records = [r for r in caplog.records if r.name == "orb.audit"]
        assert len(audit_records) == 0
