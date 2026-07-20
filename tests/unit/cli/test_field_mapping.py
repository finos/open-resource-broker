"""Unit tests for orb.cli.field_mapping.

Covers get_field_value, get_template_field_mapping, get_request_field_mapping,
and get_machine_field_mapping — including lookup order and fallback behaviour.
"""

from __future__ import annotations

import pytest

from orb.cli.field_mapping import (
    get_field_value,
    get_machine_field_mapping,
    get_request_field_mapping,
    get_template_field_mapping,
)


@pytest.mark.unit
class TestGetFieldValue:
    def test_returns_primary_key_when_present(self):
        mapping = {"id": ["template_id", "templateId"]}
        data = {"template_id": "tmpl-1"}
        assert get_field_value(data, mapping, "id") == "tmpl-1"

    def test_falls_back_to_secondary_key(self):
        mapping = {"id": ["template_id", "templateId"]}
        data = {"templateId": "tmpl-2"}
        assert get_field_value(data, mapping, "id") == "tmpl-2"

    def test_returns_default_when_no_key_found(self):
        mapping = {"id": ["template_id", "templateId"]}
        data = {"other_key": "x"}
        assert get_field_value(data, mapping, "id") == "N/A"

    def test_custom_default(self):
        mapping = {"name": ["name"]}
        data = {}
        assert get_field_value(data, mapping, "name", default="Unknown") == "Unknown"

    def test_none_value_returns_default(self):
        mapping = {"id": ["template_id"]}
        data = {"template_id": None}
        assert get_field_value(data, mapping, "id") == "N/A"

    def test_field_key_not_in_mapping_uses_key_as_name(self):
        # If field_key is absent from mapping, it defaults to [field_key]
        mapping: dict = {}
        data = {"status": "running"}
        assert get_field_value(data, mapping, "status") == "running"

    def test_numeric_value_converted_to_str(self):
        mapping = {"count": ["count"]}
        data = {"count": 42}
        assert get_field_value(data, mapping, "count") == "42"

    def test_bool_value_converted_to_str(self):
        mapping = {"active": ["active"]}
        data = {"active": True}
        assert get_field_value(data, mapping, "active") == "True"


@pytest.mark.unit
class TestGetTemplatFieldMapping:
    def test_returns_dict(self):
        result = get_template_field_mapping()
        assert isinstance(result, dict)

    def test_id_key_present(self):
        result = get_template_field_mapping()
        assert "id" in result
        assert "template_id" in result["id"]
        assert "templateId" in result["id"]

    def test_instance_type_has_camel_case(self):
        result = get_template_field_mapping()
        assert "vmType" in result["instance_type"]

    def test_snake_case_fields_have_camel_variant(self):
        result = get_template_field_mapping()
        # Fields whose logical name is snake_case carry an explicit camelCase
        # variant as their second entry. Assert the specific camelCase key.
        expected_camel = {
            "provider_api": "providerApi",
            "image_id": "imageId",
            "subnet_ids": "subnetIds",
            "security_group_ids": "securityGroupIds",
            "key_name": "keyName",
            "user_data": "userData",
            "instance_tags": "instanceTags",
            "price_type": "priceType",
            "max_spot_price": "maxSpotPrice",
            "allocation_strategy": "allocationStrategy",
            "fleet_type": "fleetType",
            "fleet_role": "fleetRole",
            "created_at": "createdAt",
            "updated_at": "updatedAt",
        }
        for field, camel in expected_camel.items():
            assert camel in result[field], f"{field} missing camelCase variant {camel}"

    def test_fleet_role_aws_specific(self):
        result = get_template_field_mapping()
        assert "fleet_role" in result
        assert "fleetRole" in result["fleet_role"]

    def test_lookup_via_get_field_value_with_template_mapping(self):
        mapping = get_template_field_mapping()
        data = {"providerApi": "EC2Fleet"}
        result = get_field_value(data, mapping, "provider_api")
        assert result == "EC2Fleet"


@pytest.mark.unit
class TestGetRequestFieldMapping:
    def test_returns_dict(self):
        assert isinstance(get_request_field_mapping(), dict)

    def test_id_has_both_variants(self):
        mapping = get_request_field_mapping()
        assert "request_id" in mapping["id"]
        assert "requestId" in mapping["id"]

    def test_num_requested_has_camel_case(self):
        mapping = get_request_field_mapping()
        assert "numRequested" in mapping["num_requested"]

    def test_lookup_camel_case_request_id(self):
        mapping = get_request_field_mapping()
        data = {"requestId": "req-xyz"}
        assert get_field_value(data, mapping, "id") == "req-xyz"

    def test_all_expected_fields_present(self):
        mapping = get_request_field_mapping()
        for field in [
            "id",
            "status",
            "template_id",
            "num_requested",
            "num_allocated",
            "created_at",
            "updated_at",
        ]:
            assert field in mapping, f"Missing expected field: {field}"


@pytest.mark.unit
class TestGetMachineFieldMapping:
    def test_returns_dict(self):
        assert isinstance(get_machine_field_mapping(), dict)

    def test_id_includes_instance_id_variants(self):
        mapping = get_machine_field_mapping()
        assert "machine_id" in mapping["id"]
        assert "instance_id" in mapping["id"]
        assert "machineId" in mapping["id"]
        assert "instanceId" in mapping["id"]

    def test_status_includes_state_alias(self):
        mapping = get_machine_field_mapping()
        assert "state" in mapping["status"]

    def test_private_ip_has_address_variant(self):
        mapping = get_machine_field_mapping()
        assert "private_ip_address" in mapping["private_ip"]

    def test_lookup_state_via_status_key(self):
        mapping = get_machine_field_mapping()
        data = {"state": "stopped"}
        assert get_field_value(data, mapping, "status") == "stopped"

    def test_lookup_instance_id_via_id_key(self):
        mapping = get_machine_field_mapping()
        data = {"instance_id": "i-0abc123"}
        assert get_field_value(data, mapping, "id") == "i-0abc123"
