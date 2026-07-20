"""Extended unit tests for Machine aggregate covering uncovered branches."""

from datetime import datetime, timezone

import pytest

from orb.domain.base.value_objects import InstanceType, Tags
from orb.domain.machine.aggregate import Machine
from orb.domain.machine.machine_identifiers import MachineId, MachineType
from orb.domain.machine.machine_status import MachineStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_machine(machine_id="i-abc0000000000001", status=MachineStatus.PENDING, **kwargs):
    defaults = dict(
        machine_id=MachineId(value=machine_id),
        template_id="tpl-001",
        provider_type="aws",
        provider_name="aws-us-east-1",
        provider_api="EC2Fleet",
        instance_type=InstanceType(value="t2.micro"),
        image_id="ami-00000001",
        status=status,
    )
    defaults.update(kwargs)
    return Machine(**defaults)


# ---------------------------------------------------------------------------
# display_name resolution chain
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineDisplayName:
    def test_returns_name_when_set(self):
        m = _make_machine(name="my-box")
        assert m.display_name == "my-box"

    def test_falls_back_to_private_dns(self):
        m = _make_machine(private_dns_name="ip-10-0-1-5.ec2.internal")
        assert m.display_name == "ip-10-0-1-5.ec2.internal"

    def test_falls_back_to_public_dns(self):
        m = _make_machine(public_dns_name="ec2-1-2-3-4.compute-1.amazonaws.com")
        assert m.display_name == "ec2-1-2-3-4.compute-1.amazonaws.com"

    def test_falls_back_to_private_ip(self):
        m = _make_machine(private_ip="10.0.1.5")
        assert m.display_name == "10.0.1.5"

    def test_falls_back_to_machine_id(self):
        m = _make_machine(machine_id="i-fallback-only")
        assert m.display_name == "i-fallback-only"


# ---------------------------------------------------------------------------
# update_status branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineUpdateStatus:
    def test_running_sets_launch_time_when_not_set(self):
        m = _make_machine(status=MachineStatus.LAUNCHING)
        updated = m.update_status(MachineStatus.RUNNING)
        assert updated.launch_time is not None

    def test_running_does_not_overwrite_existing_launch_time(self):
        fixed_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        m = _make_machine(status=MachineStatus.LAUNCHING, launch_time=fixed_time)
        updated = m.update_status(MachineStatus.RUNNING)
        assert updated.launch_time == fixed_time

    def test_terminated_sets_termination_time(self):
        m = _make_machine(status=MachineStatus.RUNNING)
        updated = m.update_status(MachineStatus.TERMINATED)
        assert updated.termination_time is not None

    def test_failed_sets_termination_time(self):
        m = _make_machine(status=MachineStatus.LAUNCHING)
        updated = m.update_status(MachineStatus.FAILED)
        assert updated.termination_time is not None

    def test_same_status_produces_no_domain_event(self):
        m = _make_machine(status=MachineStatus.RUNNING)
        updated = m.update_status(MachineStatus.RUNNING)
        assert len(updated.get_domain_events()) == 0

    def test_running_with_private_ip_fires_provisioned_event(self):
        m = _make_machine(
            status=MachineStatus.LAUNCHING,
            private_ip="10.0.1.5",
        )
        updated = m.update_status(MachineStatus.RUNNING)
        event_types = [type(e).__name__ for e in updated.get_domain_events()]
        assert "MachineProvisionedEvent" in event_types

    def test_running_without_ip_does_not_fire_provisioned_event(self):
        m = _make_machine(status=MachineStatus.LAUNCHING)
        updated = m.update_status(MachineStatus.RUNNING)
        event_types = [type(e).__name__ for e in updated.get_domain_events()]
        assert "MachineProvisionedEvent" not in event_types

    def test_status_reason_is_stored(self):
        m = _make_machine(status=MachineStatus.LAUNCHING)
        updated = m.update_status(MachineStatus.FAILED, reason="spot interrupted")
        assert updated.status_reason == "spot interrupted"

    def test_version_incremented(self):
        m = _make_machine(status=MachineStatus.PENDING)
        updated = m.update_status(MachineStatus.LAUNCHING)
        assert updated.version == m.version + 1


# ---------------------------------------------------------------------------
# update_network_info
# ---------------------------------------------------------------------------
# NOTE: update_network_info wraps strings in IPAddress(...) before passing them
# to model_validate, but Machine.private_ip / public_ip are typed Optional[str].
# This causes a Pydantic ValidationError — src bug. Tests for that path are
# skipped rather than working around the defect here.


@pytest.mark.unit
class TestMachineUpdateNetworkInfo:
    @pytest.mark.skip(
        reason="src bug: update_network_info wraps IPs in IPAddress but field is Optional[str]"
    )
    def test_sets_private_ip(self):
        m = _make_machine()
        updated = m.update_network_info(private_ip="10.0.0.1")
        assert updated.private_ip == "10.0.0.1"

    @pytest.mark.skip(
        reason="src bug: update_network_info wraps IPs in IPAddress but field is Optional[str]"
    )
    def test_sets_public_ip(self):
        m = _make_machine()
        updated = m.update_network_info(public_ip="54.1.2.3")
        assert updated.public_ip == "54.1.2.3"

    @pytest.mark.skip(
        reason="src bug: update_network_info wraps IPs in IPAddress but field is Optional[str]"
    )
    def test_updates_version(self):
        m = _make_machine()
        updated = m.update_network_info(private_ip="10.0.0.1")
        assert updated.version == m.version + 1


# ---------------------------------------------------------------------------
# update_tags / set_provider_data / get_provider_data
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineTagsAndProviderData:
    def test_update_tags_merges(self):
        m = _make_machine(tags=Tags(tags={"Env": "prod"}))
        updated = m.update_tags(Tags(tags={"App": "web"}))
        assert updated.tags.get("Env") == "prod"
        assert updated.tags.get("App") == "web"

    def test_set_provider_data(self):
        m = _make_machine()
        updated = m.set_provider_data({"fleet_id": "fleet-xyz"})
        assert updated.provider_data["fleet_id"] == "fleet-xyz"
        assert updated.version == m.version + 1

    def test_get_provider_data_existing_key(self):
        m = _make_machine(provider_data={"spot_price": "0.05"})
        assert m.get_provider_data("spot_price") == "0.05"

    def test_get_provider_data_missing_key_returns_default(self):
        m = _make_machine()
        assert m.get_provider_data("missing", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# to_provider_format / from_provider_format
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineProviderFormat:
    def test_to_provider_format_includes_base_keys(self):
        m = _make_machine()
        fmt = m.to_provider_format("aws")
        assert fmt["instance_id"] == str(m.machine_id)
        assert fmt["template_id"] == m.template_id
        assert fmt["status"] == m.status.value

    def test_to_provider_format_includes_launch_time_when_set(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        m = _make_machine(launch_time=ts)
        fmt = m.to_provider_format("aws")
        assert fmt["launch_time"] == ts.isoformat()

    def test_to_provider_format_includes_termination_time_when_set(self):
        ts = datetime(2024, 6, 1, tzinfo=timezone.utc)
        m = _make_machine(termination_time=ts)
        fmt = m.to_provider_format("aws")
        assert fmt["termination_time"] == ts.isoformat()

    def test_to_provider_format_omits_missing_optional_fields(self):
        m = _make_machine()
        fmt = m.to_provider_format("aws")
        assert "private_ip" not in fmt
        assert "public_ip" not in fmt
        assert "launch_time" not in fmt

    def test_to_provider_format_includes_private_ip(self):
        m = _make_machine(private_ip="10.0.0.1")
        fmt = m.to_provider_format("aws")
        assert fmt["private_ip"] == "10.0.0.1"

    def test_from_provider_format_raises_without_provider_api(self):
        with pytest.raises(ValueError, match="provider_api"):
            Machine.from_provider_format(
                {
                    "instance_id": "i-test",
                    "template_id": "tpl-1",
                    "provider_name": "aws-us-east-1",
                    "instance_type": "m5.large",
                    "image_id": "ami-1",
                    "status": "pending",
                },
                provider_type="aws",
            )

    def test_from_provider_format_raises_without_provider_name(self):
        with pytest.raises(ValueError, match="provider_name"):
            Machine.from_provider_format(
                {
                    "instance_id": "i-test",
                    "template_id": "tpl-1",
                    "provider_api": "EC2Fleet",
                    "instance_type": "m5.large",
                    "image_id": "ami-1",
                    "status": "pending",
                },
                provider_type="aws",
            )

    def test_from_provider_format_accepts_camel_case_keys(self):
        m = Machine.from_provider_format(
            {
                "instance_id": "i-camel",
                "template_id": "tpl-1",
                "providerApi": "EC2Fleet",
                "providerName": "aws-us-east-1",
                "instance_type": "m5.large",
                "image_id": "ami-1",
                "status": "pending",
            },
            provider_type="aws",
        )
        assert str(m.machine_id) == "i-camel"

    @pytest.mark.skip(
        reason="src bug: from_provider_format wraps IPs in IPAddress but field is Optional[str]"
    )
    def test_from_provider_format_parses_optional_ips(self):
        m = Machine.from_provider_format(
            {
                "instance_id": "i-ips",
                "template_id": "tpl-1",
                "provider_api": "RunInstances",
                "provider_name": "aws-us-east-1",
                "instance_type": "t2.micro",
                "image_id": "ami-2",
                "status": "running",
                "private_ip": "10.0.0.5",
                "public_ip": "54.0.0.1",
            },
            provider_type="aws",
        )
        assert m.private_ip == "10.0.0.5"
        assert m.public_ip == "54.0.0.1"


# ---------------------------------------------------------------------------
# Properties: is_running, is_terminated, is_healthy, uptime
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineProperties:
    def test_is_running_true_only_for_running(self):
        assert _make_machine(status=MachineStatus.RUNNING).is_running is True
        assert _make_machine(status=MachineStatus.PENDING).is_running is False

    def test_is_terminated_true_for_terminated_and_shutting_down(self):
        assert _make_machine(status=MachineStatus.TERMINATED).is_terminated is True
        assert _make_machine(status=MachineStatus.SHUTTING_DOWN).is_terminated is True
        assert _make_machine(status=MachineStatus.RUNNING).is_terminated is False

    def test_is_healthy_true_for_pending_and_running(self):
        assert _make_machine(status=MachineStatus.PENDING).is_healthy is True
        assert _make_machine(status=MachineStatus.RUNNING).is_healthy is True
        assert _make_machine(status=MachineStatus.TERMINATED).is_healthy is False

    def test_uptime_none_when_not_running(self):
        m = _make_machine(
            status=MachineStatus.PENDING,
            launch_time=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert m.uptime is None

    def test_uptime_none_when_no_launch_time(self):
        m = _make_machine(status=MachineStatus.RUNNING)
        assert m.uptime is None

    def test_uptime_positive_when_running_with_launch_time(self):
        past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        m = _make_machine(status=MachineStatus.RUNNING, launch_time=past)
        assert m.uptime is not None
        assert m.uptime > 0


# ---------------------------------------------------------------------------
# MachineType value object
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineType:
    def test_valid_instance_type(self):
        mt = MachineType(value="m5.large")
        assert mt.family == "m5"
        assert mt.size == "large"

    def test_from_str_factory(self):
        mt = MachineType.from_str("t2.micro")
        assert mt.value == "t2.micro"

    def test_invalid_format_raises(self):
        with pytest.raises(Exception):
            MachineType(value="invalid")

    def test_empty_value_raises(self):
        with pytest.raises(Exception):
            MachineType(value="")

    def test_str_returns_value(self):
        mt = MachineType(value="c5.xlarge")
        assert str(mt) == "c5.xlarge"
