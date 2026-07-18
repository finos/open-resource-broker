"""Unit tests for DiscoveryContext value object and factory function."""

import pytest

from orb.domain.base.discovery_context import DiscoveryContext, discovery_context_from_dict


@pytest.mark.unit
class TestDiscoveryContext:
    def test_creates_with_required_fields(self):
        ctx = DiscoveryContext(provider_type="k8s")
        assert ctx.provider_type == "k8s"
        assert ctx.provider_config == {}

    def test_creates_with_provider_config(self):
        ctx = DiscoveryContext(provider_type="aws", provider_config={"region": "us-east-1"})
        assert ctx.provider_type == "aws"
        assert ctx.provider_config["region"] == "us-east-1"

    def test_is_frozen(self):
        ctx = DiscoveryContext(provider_type="aws")
        with pytest.raises(Exception):
            ctx.provider_type = "k8s"  # type: ignore[misc]

    def test_equality(self):
        a = DiscoveryContext(provider_type="aws", provider_config={"region": "us-east-1"})
        b = DiscoveryContext(provider_type="aws", provider_config={"region": "us-east-1"})
        assert a == b

    def test_inequality_on_type(self):
        a = DiscoveryContext(provider_type="aws")
        b = DiscoveryContext(provider_type="k8s")
        assert a != b


@pytest.mark.unit
class TestDiscoveryContextFromDict:
    def test_extracts_type_key(self):
        raw = {"type": "aws", "region": "us-west-2"}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_type == "aws"
        assert ctx.provider_config["region"] == "us-west-2"

    def test_extracts_provider_type_key(self):
        raw = {"provider_type": "k8s", "cluster": "prod"}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_type == "k8s"
        assert ctx.provider_config["cluster"] == "prod"

    def test_type_takes_precedence_over_provider_type(self):
        raw = {"type": "aws", "provider_type": "ignored", "extra": "val"}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_type == "aws"

    def test_nested_config_section_is_merged(self):
        raw = {"type": "aws", "config": {"region": "eu-west-1", "profile": "default"}}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_config["region"] == "eu-west-1"
        assert ctx.provider_config["profile"] == "default"

    def test_top_level_keys_merged_with_config_section(self):
        raw = {"type": "aws", "account_id": "123", "config": {"region": "us-east-1"}}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_config["account_id"] == "123"
        assert ctx.provider_config["region"] == "us-east-1"

    def test_config_section_overwrites_top_level_on_collision(self):
        # config section has higher priority (update overwrites)
        raw = {"type": "aws", "region": "top-level", "config": {"region": "from-config"}}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_config["region"] == "from-config"

    def test_missing_type_gives_empty_string(self):
        raw = {"region": "us-east-1"}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_type == ""

    def test_type_and_provider_type_excluded_from_provider_config(self):
        raw = {"type": "aws", "provider_type": "also-aws", "region": "us-east-1"}
        ctx = discovery_context_from_dict(raw)
        assert "type" not in ctx.provider_config
        assert "provider_type" not in ctx.provider_config

    def test_none_config_section_treated_as_empty(self):
        raw = {"type": "aws", "config": None}
        ctx = discovery_context_from_dict(raw)
        assert ctx.provider_type == "aws"
