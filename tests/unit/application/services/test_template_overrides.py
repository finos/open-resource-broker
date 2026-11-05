"""Unit tests for per-request template overrides."""

from __future__ import annotations

import pytest

from orb.application.dto.template import TemplateDTO
from orb.application.services.template_overrides import (
    OVERRIDABLE_FIELDS,
    apply_template_overrides,
)
from orb.domain.request.exceptions import RequestValidationError


def _template() -> TemplateDTO:
    return TemplateDTO(
        template_id="tmpl-1",
        subnet_ids=["subnet-a"],
        security_group_ids=["sg-a"],
        machine_types={"t3.small": 1},
        instance_profile="role-a",
        key_name="key-a",
        user_data="#!/bin/sh",
        tags={"team": "x"},
        provider_type="aws",
        provider_api="EC2Fleet",
    )


@pytest.mark.unit
class TestApplyTemplateOverrides:
    def test_no_overrides_returns_same_object(self):
        tmpl = _template()
        assert apply_template_overrides(tmpl, {}) is tmpl

    def test_allowed_fields_are_replaced_and_original_untouched(self):
        tmpl = _template()
        out = apply_template_overrides(
            tmpl,
            {
                "subnet_ids": ["subnet-b"],
                "security_group_ids": ["sg-b"],
                "machine_types": {"m5.large": 2},
            },
        )
        assert out.subnet_ids == ["subnet-b"]
        assert out.security_group_ids == ["sg-b"]
        assert out.machine_types == {"m5.large": 2}
        assert tmpl.subnet_ids == ["subnet-a"]
        assert out.instance_profile == "role-a"
        assert out.provider_api == "EC2Fleet"

    def test_tags_are_merged_over_template_tags(self):
        out = apply_template_overrides(_template(), {"tags": {"env": "dev"}})
        assert out.tags == {"team": "x", "env": "dev"}

    def test_reserved_tag_prefix_rejected(self):
        with pytest.raises(RequestValidationError, match="reserved"):
            apply_template_overrides(_template(), {"tags": {"orb:request-id": "x"}})

    @pytest.mark.parametrize("key", ["subnet_ids", "security_group_ids", "machine_types"])
    @pytest.mark.parametrize("empty", [[], {}, None])
    def test_empty_networking_and_machine_type_overrides_rejected(self, key, empty):
        with pytest.raises(RequestValidationError, match="may not be empty"):
            apply_template_overrides(_template(), {key: empty})

    def test_unknown_price_type_rejected(self):
        with pytest.raises(RequestValidationError, match="price_type"):
            apply_template_overrides(_template(), {"price_type": "bogus"})

    @pytest.mark.parametrize("price_type", [["spot"], {"a": 1}, 3, None])
    def test_non_string_price_type_rejected(self, price_type):
        with pytest.raises(RequestValidationError, match="price_type"):
            apply_template_overrides(_template(), {"price_type": price_type})

    @pytest.mark.parametrize("price_type", ["ondemand", "spot", "heterogeneous"])
    def test_known_price_types_accepted(self, price_type):
        out = apply_template_overrides(_template(), {"price_type": price_type})
        assert out.price_type == price_type

    @pytest.mark.parametrize("key", ["aws:cloudformation:stack-name", "AWS:x", "ORB:request-id"])
    def test_aws_and_case_variant_reserved_tag_prefixes_rejected(self, key):
        with pytest.raises(RequestValidationError, match="reserved"):
            apply_template_overrides(_template(), {"tags": {key: "x"}})

    @pytest.mark.parametrize("tags", [{"env": 1}, {"env": None}, {1: "x"}, {"env": ["a"]}])
    def test_non_string_tag_keys_and_values_rejected(self, tags):
        with pytest.raises(RequestValidationError, match="must be strings"):
            apply_template_overrides(_template(), {"tags": tags})

    @pytest.mark.parametrize(
        "key",
        [
            "machine_role",
            "instance_profile",
            "machine_ssh_key",
            "key_name",
            "machine_bootstrap",
            "user_data",
            "template_id",
            "provider_type",
            "provider_name",
            "provider_api",
        ],
    )
    def test_protected_fields_and_their_aliases_rejected(self, key):
        with pytest.raises(RequestValidationError, match="cannot change"):
            apply_template_overrides(_template(), {key: "x"})

    @pytest.mark.parametrize("key", ["image_id", "machine_image", "instance_type", "bogus"])
    def test_non_allowlisted_and_unknown_keys_rejected(self, key):
        with pytest.raises(RequestValidationError, match="Unsupported"):
            apply_template_overrides(_template(), {key: "x"})

    def test_alias_and_canonical_together_rejected(self):
        with pytest.raises(RequestValidationError, match="duplicates"):
            apply_template_overrides(_template(), {"instance_profile": "a", "machine_role": "b"})

    def test_invalid_value_rejected(self):
        with pytest.raises(RequestValidationError, match="Invalid template override"):
            apply_template_overrides(_template(), {"subnet_ids": "not-a-list"})

    def test_allowlist_has_no_identity_or_credential_fields(self):
        assert not OVERRIDABLE_FIELDS & {
            "machine_role",
            "machine_ssh_key",
            "machine_bootstrap",
            "template_id",
            "provider_type",
            "provider_name",
            "provider_api",
            "encryption_key",
        }
