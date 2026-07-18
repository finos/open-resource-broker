"""Unit tests for machine metadata value objects."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from orb.domain.base.value_objects import IPAddress
from orb.domain.machine.machine_metadata import (
    HealthCheck,
    HealthCheckResult,
    IPAddressRange,
    MachineConfiguration,
    MachineHistoryEvent,
    MachineMetadata,
    PriceType,
    ResourceTag,
)

# ---------------------------------------------------------------------------
# PriceType
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPriceType:
    def test_from_string_ondemand(self):
        assert PriceType.from_string("ondemand") == PriceType.ON_DEMAND

    def test_from_string_spot(self):
        assert PriceType.from_string("spot") == PriceType.SPOT

    def test_from_string_heterogeneous(self):
        assert PriceType.from_string("heterogeneous") == PriceType.HETEROGENEOUS

    def test_from_string_case_insensitive(self):
        assert PriceType.from_string("SPOT") == PriceType.SPOT

    def test_from_string_strips_whitespace(self):
        assert PriceType.from_string("  ondemand  ") == PriceType.ON_DEMAND

    def test_from_string_empty_defaults_to_ondemand(self):
        assert PriceType.from_string("") == PriceType.ON_DEMAND

    def test_from_string_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid price type"):
            PriceType.from_string("reserved")


# ---------------------------------------------------------------------------
# MachineConfiguration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineConfiguration:
    def _make(self, **kwargs):
        defaults: dict = dict(
            instance_type="m5.large",
            private_ip=IPAddress(value="10.0.1.5"),
            provider_api="EC2Fleet",
            resource_id="i-abc123",
        )
        defaults.update(kwargs)
        return MachineConfiguration(**defaults)  # type: ignore[arg-type]

    def test_creates_valid(self):
        cfg = self._make()
        assert cfg.instance_type == "m5.large"
        assert cfg.resource_id == "i-abc123"

    def test_defaults_price_type_to_ondemand(self):
        cfg = self._make()
        assert cfg.price_type == PriceType.ON_DEMAND

    def test_empty_instance_type_raises(self):
        with pytest.raises(ValidationError):
            self._make(instance_type="")

    def test_empty_provider_api_raises(self):
        with pytest.raises(ValidationError):
            self._make(provider_api="")

    def test_empty_resource_id_raises(self):
        with pytest.raises(ValidationError):
            self._make(resource_id="")

    def test_to_dict_keys(self):
        cfg = self._make()
        d = cfg.to_dict()
        assert "instanceType" in d
        assert "privateIpAddress" in d
        assert "providerApi" in d
        assert "resourceId" in d
        assert "priceType" in d

    def test_to_dict_with_public_ip(self):
        cfg = self._make(public_ip=IPAddress(value="1.2.3.4"))
        d = cfg.to_dict()
        assert d["publicIpAddress"] == "1.2.3.4"

    def test_to_dict_with_cloud_host_id(self):
        cfg = self._make(cloud_host_id="host-xyz")
        d = cfg.to_dict()
        assert d["cloudHostId"] == "host-xyz"

    def test_from_dict_round_trip(self):
        original = self._make()
        d = original.to_dict()
        restored = MachineConfiguration.from_dict(d)
        assert restored.instance_type == original.instance_type
        assert restored.resource_id == original.resource_id


# ---------------------------------------------------------------------------
# MachineHistoryEvent
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineHistoryEvent:
    def test_creates_valid_event(self):
        evt = MachineHistoryEvent(
            timestamp=datetime.now(timezone.utc),
            event_type="state_change",
            old_state="pending",
            new_state="running",
        )
        assert evt.event_type == "state_change"

    def test_empty_event_type_raises(self):
        with pytest.raises(ValidationError):
            MachineHistoryEvent(
                timestamp=datetime.now(timezone.utc),
                event_type="",
                old_state=None,
                new_state=None,
            )

    def test_to_dict_structure(self):
        evt = MachineHistoryEvent(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            event_type="launch",
            old_state=None,
            new_state="running",
            details={"reason": "scheduled"},
        )
        d = evt.to_dict()
        assert d["eventType"] == "launch"
        assert d["oldState"] is None
        assert d["newState"] == "running"
        assert d["details"] == {"reason": "scheduled"}


# ---------------------------------------------------------------------------
# HealthCheck
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheck:
    def test_creates_valid(self):
        hc = HealthCheck(
            check_type="http",
            status=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert hc.check_type == "http"
        assert hc.status is True

    def test_empty_check_type_raises(self):
        with pytest.raises(ValidationError):
            HealthCheck(check_type="", status=False, timestamp=datetime.now(timezone.utc))

    def test_to_dict_structure(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        hc = HealthCheck(check_type="tcp", status=False, timestamp=ts)
        d = hc.to_dict()
        assert d["checkType"] == "tcp"
        assert d["status"] is False
        assert "2024-06-01" in d["timestamp"]


# ---------------------------------------------------------------------------
# IPAddressRange
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIPAddressRange:
    def test_valid_cidr(self):
        r = IPAddressRange(cidr="10.0.0.0/16")
        assert str(r) == "10.0.0.0/16"

    def test_network_address_property(self):
        r = IPAddressRange(cidr="192.168.1.0/24")
        assert r.network_address == "192.168.1.0"

    def test_prefix_length_property(self):
        r = IPAddressRange(cidr="192.168.1.0/24")
        assert r.prefix_length == 24

    def test_invalid_cidr_raises(self):
        with pytest.raises(ValidationError):
            IPAddressRange(cidr="not-a-cidr")

    def test_invalid_prefix_raises(self):
        with pytest.raises(ValidationError):
            IPAddressRange(cidr="10.0.0.0/33")

    def test_invalid_ip_octet_raises(self):
        with pytest.raises(ValidationError):
            IPAddressRange(cidr="256.0.0.0/8")

    def test_valid_host_cidr(self):
        r = IPAddressRange(cidr="172.16.0.1/32")
        assert r.prefix_length == 32


# ---------------------------------------------------------------------------
# MachineMetadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineMetadata:
    def _make(self, **kwargs):
        defaults: dict = dict(
            availability_zone="us-east-1a",
            subnet_id="subnet-abc",
            vpc_id="vpc-xyz",
            ami_id="ami-12345678",
        )
        defaults.update(kwargs)
        return MachineMetadata(**defaults)  # type: ignore[arg-type]

    def test_creates_valid(self):
        meta = self._make()
        assert meta.availability_zone == "us-east-1a"
        assert meta.ebs_optimized is False
        assert meta.monitoring == "disabled"

    def test_empty_availability_zone_raises(self):
        with pytest.raises(ValidationError):
            self._make(availability_zone="")

    def test_empty_subnet_id_raises(self):
        with pytest.raises(ValidationError):
            self._make(subnet_id="")

    def test_empty_vpc_id_raises(self):
        with pytest.raises(ValidationError):
            self._make(vpc_id="")

    def test_empty_ami_id_raises(self):
        with pytest.raises(ValidationError):
            self._make(ami_id="")

    def test_to_dict_keys(self):
        meta = self._make()
        d = meta.to_dict()
        assert set(d.keys()) == {
            "availability_zone",
            "subnet_id",
            "vpc_id",
            "ami_id",
            "ebs_optimized",
            "monitoring",
            "tags",
        }

    def test_from_dict_round_trip(self):
        original = self._make(tags={"Env": "test"})
        d = original.to_dict()
        restored = MachineMetadata.from_dict(d)
        assert restored.availability_zone == original.availability_zone
        assert restored.tags == {"Env": "test"}


# ---------------------------------------------------------------------------
# HealthCheckResult
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthCheckResult:
    def test_is_healthy_when_both_true(self):
        r = HealthCheckResult(
            system_status=True,
            instance_status=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert r.is_healthy is True

    def test_not_healthy_when_system_fails(self):
        r = HealthCheckResult(
            system_status=False,
            instance_status=True,
            timestamp=datetime.now(timezone.utc),
        )
        assert r.is_healthy is False

    def test_not_healthy_when_instance_fails(self):
        r = HealthCheckResult(
            system_status=True,
            instance_status=False,
            timestamp=datetime.now(timezone.utc),
        )
        assert r.is_healthy is False

    def test_to_dict_structure(self):
        ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
        r = HealthCheckResult(system_status=True, instance_status=False, timestamp=ts)
        d = r.to_dict()
        assert d["system"]["status"] is True
        assert d["instance"]["status"] is False
        assert "2024-01-01" in d["timestamp"]

    def test_from_dict_round_trip(self):
        ts = datetime(2024, 3, 15, tzinfo=timezone.utc)
        original = HealthCheckResult(system_status=True, instance_status=True, timestamp=ts)
        d = original.to_dict()
        restored = HealthCheckResult.from_dict(d)
        assert restored.system_status is True
        assert restored.instance_status is True


# ---------------------------------------------------------------------------
# ResourceTag
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResourceTag:
    def test_creates_valid_tag(self):
        tag = ResourceTag(key="Env", value="prod")
        assert tag.key == "Env"
        assert tag.value == "prod"

    def test_empty_key_raises(self):
        with pytest.raises(ValidationError):
            ResourceTag(key="", value="prod")

    def test_key_too_long_raises(self):
        with pytest.raises(ValidationError):
            ResourceTag(key="x" * 129, value="v")

    def test_value_too_long_raises(self):
        with pytest.raises(ValidationError):
            ResourceTag(key="k", value="v" * 257)

    def test_to_dict_uppercase_keys(self):
        tag = ResourceTag(key="Env", value="prod")
        d = tag.to_dict()
        assert d == {"Key": "Env", "Value": "prod"}

    def test_from_dict_uppercase_format(self):
        tag = ResourceTag.from_dict({"Key": "App", "Value": "web"})
        assert tag.key == "App"
        assert tag.value == "web"

    def test_get_default_tags_returns_list(self):
        tags = ResourceTag.get_default_tags()
        assert len(tags) >= 1
        keys = [t.key for t in tags]
        assert "Environment" in keys
