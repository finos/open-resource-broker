"""Tests for the shared ProviderError exception hierarchy."""

import json

import pytest

from orb.providers.base.exceptions import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderPermanentError,
    ProviderQuotaError,
    ProviderTransientError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_LEAF_CLASSES = [
    ProviderConfigError,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderTransientError,
    ProviderPermanentError,
]


# ---------------------------------------------------------------------------
# Inheritance / isinstance checks
# ---------------------------------------------------------------------------


class TestInheritance:
    def test_all_leaves_are_provider_errors(self):
        for cls in ALL_LEAF_CLASSES:
            exc = cls("msg", provider_type="test")
            assert isinstance(exc, ProviderError), f"{cls.__name__} must be a ProviderError"

    def test_all_leaves_are_exceptions(self):
        for cls in ALL_LEAF_CLASSES:
            exc = cls("msg", provider_type="test")
            assert isinstance(exc, Exception)

    def test_provider_error_is_exception(self):
        exc = ProviderError("base msg", provider_type="aws")
        assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# provider_type field
# ---------------------------------------------------------------------------


class TestProviderTypeField:
    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_provider_type_is_stored(self, cls):
        exc = cls("some error", provider_type="mycloud")
        assert exc.provider_type == "mycloud"

    def test_base_stores_provider_type(self):
        exc = ProviderError("base", provider_type="azure")
        assert exc.provider_type == "azure"

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_provider_name_defaults_to_none(self, cls):
        exc = cls("msg", provider_type="gcp")
        assert exc.provider_name is None

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_provider_name_is_stored(self, cls):
        exc = cls("msg", provider_type="aws", provider_name="prod-us-east-1")
        assert exc.provider_name == "prod-us-east-1"


# ---------------------------------------------------------------------------
# underlying_exception field
# ---------------------------------------------------------------------------


class TestUnderlyingException:
    def test_defaults_to_none(self):
        exc = ProviderError("msg", provider_type="aws")
        assert exc.underlying_exception is None

    def test_stores_underlying(self):
        cause = ValueError("original cause")
        exc = ProviderAuthError("auth failed", provider_type="aws", underlying_exception=cause)
        assert exc.underlying_exception is cause

    def test_does_not_become_cause_by_default(self):
        # We store the underlying exception explicitly; the raise-from chain
        # is the caller's responsibility — we don't set __cause__ automatically.
        cause = RuntimeError("root cause")
        exc = ProviderTransientError("retry me", provider_type="k8s", underlying_exception=cause)
        assert exc.__cause__ is None
        assert exc.underlying_exception is cause


# ---------------------------------------------------------------------------
# is_retryable attribute — leaf class defaults
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_base_class_default_is_false(self):
        exc = ProviderError("base error", provider_type="aws")
        assert exc.is_retryable is False

    def test_config_error_is_not_retryable(self):
        exc = ProviderConfigError("bad config", provider_type="aws")
        assert exc.is_retryable is False

    def test_auth_error_is_not_retryable(self):
        exc = ProviderAuthError("unauthorized", provider_type="aws")
        assert exc.is_retryable is False

    def test_quota_error_is_retryable(self):
        exc = ProviderQuotaError("throttled", provider_type="aws")
        assert exc.is_retryable is True

    def test_transient_error_is_retryable(self):
        exc = ProviderTransientError("503 unavailable", provider_type="k8s")
        assert exc.is_retryable is True

    def test_permanent_error_is_not_retryable(self):
        exc = ProviderPermanentError("404 not found", provider_type="azure")
        assert exc.is_retryable is False

    def test_is_retryable_override_true(self):
        # A ProviderAuthError for short-lived token expiry can be marked retryable.
        exc = ProviderAuthError("token expired", provider_type="aws", is_retryable=True)
        assert exc.is_retryable is True

    def test_is_retryable_override_false(self):
        # A hard quota ceiling is not retryable without operator action.
        exc = ProviderQuotaError("hard limit", provider_type="aws", is_retryable=False)
        assert exc.is_retryable is False

    def test_is_retryable_present_in_to_dict(self):
        exc = ProviderQuotaError("throttled", provider_type="aws")
        d = exc.to_dict()
        assert "is_retryable" in d
        assert d["is_retryable"] is True

    def test_retry_logic_pattern_via_attribute(self):
        """Demonstrate the intended retry-logic pattern."""
        retryable_errors = [
            ProviderTransientError("transient", provider_type="k8s"),
            ProviderQuotaError("throttled", provider_type="aws"),
        ]
        non_retryable_errors = [
            ProviderConfigError("bad config", provider_type="aws"),
            ProviderAuthError("access denied", provider_type="aws"),
            ProviderPermanentError("not found", provider_type="azure"),
        ]

        for exc in retryable_errors:
            assert isinstance(exc, ProviderError) and exc.is_retryable, (
                f"{type(exc).__name__} should be retryable"
            )

        for exc in non_retryable_errors:
            assert isinstance(exc, ProviderError) and not exc.is_retryable, (
                f"{type(exc).__name__} should not be retryable"
            )


# ---------------------------------------------------------------------------
# to_dict() round-trip
# ---------------------------------------------------------------------------


class TestToDict:
    def test_required_keys_present(self):
        exc = ProviderError("something broke", provider_type="aws")
        d = exc.to_dict()
        assert d["error_type"] == "ProviderError"
        assert d["message"] == "something broke"
        assert d["provider_type"] == "aws"

    def test_is_retryable_present(self):
        exc = ProviderError("msg", provider_type="aws")
        assert "is_retryable" in exc.to_dict()

    def test_provider_name_absent_when_none(self):
        exc = ProviderError("msg", provider_type="aws")
        assert "provider_name" not in exc.to_dict()

    def test_provider_name_present_when_set(self):
        exc = ProviderError("msg", provider_type="aws", provider_name="prod")
        assert exc.to_dict()["provider_name"] == "prod"

    def test_underlying_exception_absent_when_none(self):
        exc = ProviderError("msg", provider_type="aws")
        assert "underlying_exception" not in exc.to_dict()

    def test_underlying_exception_repr_when_set(self):
        cause = ValueError("root cause")
        exc = ProviderConfigError("bad config", provider_type="azure", underlying_exception=cause)
        d = exc.to_dict()
        assert "underlying_exception" in d
        assert "ValueError" in d["underlying_exception"]
        assert "root cause" in d["underlying_exception"]

    def test_details_absent_when_empty(self):
        exc = ProviderError("msg", provider_type="aws")
        assert "details" not in exc.to_dict()

    def test_details_present_when_set(self):
        exc = ProviderQuotaError(
            "quota hit",
            provider_type="aws",
            details={"quota_name": "vCPU", "limit": 100},
        )
        d = exc.to_dict()
        assert d["details"]["quota_name"] == "vCPU"
        assert d["details"]["limit"] == 100

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_error_type_matches_classname(self, cls):
        exc = cls("msg", provider_type="test")
        assert exc.to_dict()["error_type"] == cls.__name__

    def test_to_dict_is_json_serialisable(self):
        cause = OSError("network unreachable")
        exc = ProviderTransientError(
            "transient failure",
            provider_type="gcp",
            provider_name="gcp-europe",
            underlying_exception=cause,
            details={"retry_after": 5},
        )
        # Should not raise
        payload = json.dumps(exc.to_dict())
        recovered = json.loads(payload)
        assert recovered["provider_type"] == "gcp"
        assert recovered["provider_name"] == "gcp-europe"

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_is_retryable_round_trips_in_to_dict(self, cls):
        exc = cls("msg", provider_type="test")
        d = exc.to_dict()
        assert d["is_retryable"] == exc.is_retryable


# ---------------------------------------------------------------------------
# safe_to_dict() — no secret leakage
# ---------------------------------------------------------------------------


class TestSafeToDict:
    def test_safe_to_dict_omits_underlying_exception(self):
        cause = ValueError("arn:aws:iam::123456789012:role/SecretRole")
        exc = ProviderAuthError(
            "auth failed",
            provider_type="aws",
            underlying_exception=cause,
        )
        d = exc.safe_to_dict()
        assert "underlying_exception" not in d

    def test_safe_to_dict_includes_required_fields(self):
        exc = ProviderTransientError("503", provider_type="k8s")
        d = exc.safe_to_dict()
        assert d["error_type"] == "ProviderTransientError"
        assert d["message"] == "503"
        assert d["provider_type"] == "k8s"
        assert "is_retryable" in d

    def test_safe_to_dict_includes_is_retryable(self):
        exc = ProviderQuotaError("throttled", provider_type="aws")
        d = exc.safe_to_dict()
        assert d["is_retryable"] is True

    def test_safe_to_dict_includes_provider_name_when_set(self):
        exc = ProviderConfigError("bad config", provider_type="aws", provider_name="prod")
        d = exc.safe_to_dict()
        assert d["provider_name"] == "prod"

    def test_safe_to_dict_omits_provider_name_when_none(self):
        exc = ProviderConfigError("bad config", provider_type="aws")
        assert "provider_name" not in exc.safe_to_dict()

    def test_safe_to_dict_includes_details_when_set(self):
        exc = ProviderQuotaError(
            "quota hit",
            provider_type="aws",
            details={"quota_name": "vCPU"},
        )
        d = exc.safe_to_dict()
        assert d["details"]["quota_name"] == "vCPU"

    def test_safe_to_dict_omits_details_when_empty(self):
        exc = ProviderPermanentError("not found", provider_type="azure")
        assert "details" not in exc.safe_to_dict()

    def test_safe_to_dict_is_json_serialisable(self):
        cause = OSError("postgres://user:secret@db:5432/prod")
        exc = ProviderTransientError(
            "db connection failed",
            provider_type="gcp",
            provider_name="gcp-eu",
            underlying_exception=cause,
            details={"retry_after": 10},
        )
        payload = json.dumps(exc.safe_to_dict())
        recovered = json.loads(payload)
        assert recovered["provider_type"] == "gcp"
        assert "underlying_exception" not in recovered

    def test_to_dict_includes_underlying_safe_to_dict_does_not(self):
        """Explicitly document the difference between the two methods."""
        cause = ValueError("secret connection string")
        exc = ProviderConfigError("config failed", provider_type="aws", underlying_exception=cause)
        assert "underlying_exception" in exc.to_dict()
        assert "underlying_exception" not in exc.safe_to_dict()

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_safe_to_dict_error_type_matches_classname(self, cls):
        exc = cls("msg", provider_type="test")
        assert exc.safe_to_dict()["error_type"] == cls.__name__

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_safe_to_dict_is_retryable_round_trips(self, cls):
        exc = cls("msg", provider_type="test")
        d = exc.safe_to_dict()
        assert d["is_retryable"] == exc.is_retryable


# ---------------------------------------------------------------------------
# Can be raised and caught
# ---------------------------------------------------------------------------


class TestRaiseAndCatch:
    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_can_raise_and_catch_as_provider_error(self, cls):
        with pytest.raises(ProviderError):
            raise cls("raised", provider_type="test")

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_can_raise_and_catch_as_exception(self, cls):
        with pytest.raises(Exception, match="raised"):
            raise cls("raised", provider_type="test")

    @pytest.mark.parametrize("cls", ALL_LEAF_CLASSES)
    def test_catch_specific_subclass(self, cls):
        with pytest.raises(cls):
            raise cls("exact match", provider_type="test")

    def test_str_representation_contains_message(self):
        exc = ProviderPermanentError("something is very wrong", provider_type="oci")
        assert "something is very wrong" in str(exc)


# ---------------------------------------------------------------------------
# details field
# ---------------------------------------------------------------------------


class TestDetails:
    def test_details_defaults_to_empty_dict(self):
        exc = ProviderError("msg", provider_type="aws")
        assert exc.details == {}

    def test_details_is_mutable_copy(self):
        original = {"key": "value"}
        exc = ProviderError("msg", provider_type="aws", details=original)
        exc.details["extra"] = "added"
        # Should not raise; details is just stored as-is
        assert exc.details["extra"] == "added"
