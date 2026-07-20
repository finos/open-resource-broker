"""Unit tests for provider interface value objects and enums."""

import pytest

from orb.domain.base.provider_interfaces import (
    ProviderInstanceState,
    ProviderLaunchTemplate,
    ProviderResourceIdentifier,
    ProviderResourceTag,
)

# ---------------------------------------------------------------------------
# ProviderInstanceState
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderInstanceState:
    def test_all_expected_values(self):
        values = {e.value for e in ProviderInstanceState}
        assert "pending" in values
        assert "running" in values
        assert "terminated" in values
        assert "failed" in values

    def test_is_string_enum(self):
        assert isinstance(ProviderInstanceState.RUNNING, str)


# ---------------------------------------------------------------------------
# ProviderResourceTag
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderResourceTag:
    def test_creates_valid_tag(self):
        tag = ProviderResourceTag(key="Env", value="prod")
        assert tag.key == "Env"
        assert tag.value == "prod"

    def test_empty_key_raises(self):
        with pytest.raises(ValueError, match="1-128 characters"):
            ProviderResourceTag(key="", value="v")

    def test_key_too_long_raises(self):
        with pytest.raises(ValueError, match="1-128 characters"):
            ProviderResourceTag(key="x" * 129, value="v")

    def test_value_too_long_raises(self):
        with pytest.raises(ValueError, match="0-256 characters"):
            ProviderResourceTag(key="k", value="v" * 257)

    def test_reserved_prefix_raises(self):
        with pytest.raises(ValueError, match="provider:"):
            ProviderResourceTag(key="provider:internal", value="v")

    def test_immutable(self):
        tag = ProviderResourceTag(key="Env", value="prod")
        with pytest.raises(Exception):
            tag.key = "Other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ProviderResourceIdentifier
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderResourceIdentifier:
    def test_creates_valid_identifier(self):
        pid = ProviderResourceIdentifier(
            provider_type="aws",
            resource_type="instance",
            identifier="i-abc123",
            region="us-east-1",
        )
        assert pid.identifier == "i-abc123"

    def test_empty_identifier_raises(self):
        with pytest.raises(ValueError, match="identifier cannot be empty"):
            ProviderResourceIdentifier(
                provider_type="aws",
                resource_type="instance",
                identifier="",
            )

    def test_empty_resource_type_raises(self):
        with pytest.raises(ValueError, match="Resource type cannot be empty"):
            ProviderResourceIdentifier(
                provider_type="aws",
                resource_type="",
                identifier="i-abc123",
            )

    def test_region_is_optional(self):
        pid = ProviderResourceIdentifier(
            provider_type="aws",
            resource_type="instance",
            identifier="i-abc123",
        )
        assert pid.region is None


# ---------------------------------------------------------------------------
# ProviderLaunchTemplate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProviderLaunchTemplate:
    def test_creates_valid_template(self):
        lt = ProviderLaunchTemplate(template_id="lt-abc123", version="1")
        assert lt.template_id == "lt-abc123"
        assert lt.version == "1"

    def test_empty_template_id_raises(self):
        with pytest.raises(ValueError, match="Template ID cannot be empty"):
            ProviderLaunchTemplate(template_id="")

    def test_version_optional(self):
        lt = ProviderLaunchTemplate(template_id="lt-abc123")
        assert lt.version is None
