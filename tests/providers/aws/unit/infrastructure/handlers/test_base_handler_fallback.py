"""Unit tests for AWSHandler._build_fallback_machine_payload provider_data fields."""

from unittest.mock import MagicMock

import pytest

from orb.providers.aws.infrastructure.handlers.ec2_fleet.handler import EC2FleetHandler


def _make_handler(region: str = "us-east-1") -> EC2FleetHandler:
    aws_client = MagicMock()
    aws_client.region_name = region
    logger = MagicMock()
    aws_ops = MagicMock()
    launch_template_manager = MagicMock()
    handler = EC2FleetHandler(aws_client, logger, aws_ops, launch_template_manager)
    handler._machine_adapter = None
    return handler


_INST_BASE = {
    "InstanceId": "i-fallback001",
    "InstanceType": "t3.medium",
    "State": {"Name": "running"},
    "Placement": {"AvailabilityZone": "us-east-1b"},
    "PrivateIpAddress": "10.0.1.1",
    "SecurityGroups": [],
}


@pytest.mark.unit
class TestBuildFallbackMachinePayloadProviderData:
    """_build_fallback_machine_payload must write vcpus/AZ/region to provider_data."""

    def test_vcpus_in_provider_data(self):
        handler = _make_handler()
        result = handler._build_fallback_machine_payload(_INST_BASE, "fleet-001")
        assert "vcpus" in result["provider_data"]
        assert "vcpus" not in result.get("metadata", {})

    def test_availability_zone_in_provider_data(self):
        handler = _make_handler()
        result = handler._build_fallback_machine_payload(_INST_BASE, "fleet-001")
        assert result["provider_data"]["availability_zone"] == "us-east-1b"
        assert "availability_zone" not in result.get("metadata", {})

    def test_region_derived_from_az(self):
        handler = _make_handler()
        result = handler._build_fallback_machine_payload(_INST_BASE, "fleet-001")
        assert result["provider_data"]["region"] == "us-east-1"

    def test_region_falls_back_to_client_region_when_no_az(self):
        handler = _make_handler(region="ap-southeast-2")
        inst = {k: v for k, v in _INST_BASE.items() if k != "Placement"}
        inst["Placement"] = {}
        result = handler._build_fallback_machine_payload(inst, "fleet-001")
        assert result["provider_data"]["region"] == "ap-southeast-2"
        assert "availability_zone" not in result["provider_data"]

    def test_cloud_host_id_in_provider_data(self):
        handler = _make_handler()
        result = handler._build_fallback_machine_payload(_INST_BASE, "fleet-001")
        assert result["provider_data"]["cloud_host_id"] == "i-fallback001"

    def test_metadata_is_empty(self):
        handler = _make_handler()
        result = handler._build_fallback_machine_payload(_INST_BASE, "fleet-001")
        assert result["metadata"] == {}
