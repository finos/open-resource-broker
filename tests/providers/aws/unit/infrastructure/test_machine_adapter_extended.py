"""Extended unit tests for AWSMachineAdapter — health-check, cleanup, and details paths."""

from unittest.mock import MagicMock

import pytest

from orb.providers.aws.exceptions.aws_exceptions import (
    AWSError,
    EC2InstanceNotFoundError,
    NetworkError,
    RateLimitError,
    ResourceCleanupError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(region: str = "us-east-1") -> AWSMachineAdapter:
    aws_client = MagicMock()
    aws_client.region_name = region
    return AWSMachineAdapter(aws_client=aws_client, logger=MagicMock())


def _make_machine(machine_id: str = "i-0abc123") -> MagicMock:
    machine = MagicMock()
    machine.machine_id = machine_id
    return machine


# ---------------------------------------------------------------------------
# perform_health_check — happy path
# ---------------------------------------------------------------------------


class TestPerformHealthCheckSuccess:
    def test_returns_system_and_instance_status_when_ok(self):
        adapter = _make_adapter()
        status_response = {
            "InstanceStatuses": [
                {
                    "SystemStatus": {"Status": "ok", "Details": []},
                    "InstanceStatus": {"Status": "ok", "Details": []},
                }
            ]
        }
        adapter._aws_client.execute_with_circuit_breaker.return_value = status_response
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is True
        assert result["instance"]["status"] is True

    def test_returns_false_when_status_is_not_ok(self):
        adapter = _make_adapter()
        status_response = {
            "InstanceStatuses": [
                {
                    "SystemStatus": {"Status": "impaired", "Details": []},
                    "InstanceStatus": {"Status": "initializing", "Details": []},
                }
            ]
        }
        adapter._aws_client.execute_with_circuit_breaker.return_value = status_response
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is False
        assert result["instance"]["status"] is False

    def test_returns_status_false_when_no_instance_statuses(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.return_value = {"InstanceStatuses": []}
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is False
        assert result["system"]["details"]["reason"] == "Instance status not available"

    def test_detail_fields_present_in_success_response(self):
        adapter = _make_adapter()
        details = [{"Name": "reachability", "Status": "passed"}]
        status_response = {
            "InstanceStatuses": [
                {
                    "SystemStatus": {"Status": "ok", "Details": details},
                    "InstanceStatus": {"Status": "ok", "Details": details},
                }
            ]
        }
        adapter._aws_client.execute_with_circuit_breaker.return_value = status_response
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["details"]["details"] == details
        assert result["instance"]["details"]["details"] == details


# ---------------------------------------------------------------------------
# perform_health_check — error paths
# ---------------------------------------------------------------------------


class TestPerformHealthCheckErrors:
    def test_network_error_returns_health_check_with_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = NetworkError("timeout")
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is False
        assert "Network error" in result["system"]["details"]["reason"]

    def test_rate_limit_error_returns_health_check_with_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = RateLimitError("throttled")
        machine = _make_machine()

        result = adapter.perform_health_check(machine)

        assert result["system"]["status"] is False
        assert "Rate limit" in result["system"]["details"]["reason"]

    def test_aws_error_not_found_raises_ec2_instance_not_found_error(self):
        adapter = _make_adapter()
        err = AWSError("not found", error_code="InvalidInstanceID.NotFound")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine("i-gone")

        with pytest.raises(EC2InstanceNotFoundError):
            adapter.perform_health_check(machine)

    def test_aws_error_other_code_raises_aws_error(self):
        adapter = _make_adapter()
        err = AWSError("access denied", error_code="AccessDenied")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine()

        with pytest.raises(AWSError):
            adapter.perform_health_check(machine)

    def test_unexpected_exception_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = RuntimeError("boom")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.perform_health_check(machine)
        assert "Unexpected error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# cleanup_machine_resources — happy path
# ---------------------------------------------------------------------------


class TestCleanupMachineResourcesSuccess:
    def _setup_adapter_for_cleanup(self, volumes=None, nics=None):
        """Set up adapter with mocked responses for cleanup tests."""
        adapter = _make_adapter()
        volumes = volumes or []
        nics = nics or []

        def execute_side_effect(service, operation, fn):
            if operation == "describe_instances":
                return {"Reservations": [{"Instances": [{"InstanceId": "i-0abc123"}]}]}
            if operation == "describe_volumes":
                return {"Volumes": volumes}
            if operation == "describe_network_interfaces":
                return {"NetworkInterfaces": nics}
            return fn()

        adapter._aws_client.execute_with_circuit_breaker.side_effect = execute_side_effect
        return adapter

    def test_returns_empty_success_lists_when_no_resources(self):
        adapter = self._setup_adapter_for_cleanup()
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        assert result["volumes"]["success"] == []
        assert result["volumes"]["failed"] == []
        assert result["network_interfaces"]["success"] == []
        assert result["network_interfaces"]["failed"] == []

    def test_cleanup_in_use_volumes(self):
        volumes = [{"VolumeId": "vol-001", "State": "in-use"}]
        adapter = self._setup_adapter_for_cleanup(volumes=volumes)
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        assert "vol-001" in result["volumes"]["success"]

    def test_skips_volumes_not_in_use(self):
        volumes = [{"VolumeId": "vol-avail", "State": "available"}]
        adapter = self._setup_adapter_for_cleanup(volumes=volumes)
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        # available volumes are not cleaned up
        assert result["volumes"]["success"] == []
        assert result["volumes"]["failed"] == []

    def test_cleanup_in_use_network_interfaces(self):
        nics = [
            {
                "NetworkInterfaceId": "eni-001",
                "Status": "in-use",
                "Attachment": {"AttachmentId": "att-001"},
            }
        ]
        adapter = self._setup_adapter_for_cleanup(nics=nics)
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        assert "eni-001" in result["network_interfaces"]["success"]

    def test_skips_network_interfaces_not_in_use(self):
        nics = [
            {
                "NetworkInterfaceId": "eni-avail",
                "Status": "available",
                "Attachment": {"AttachmentId": "att-avail"},
            }
        ]
        adapter = self._setup_adapter_for_cleanup(nics=nics)
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        assert result["network_interfaces"]["success"] == []


# ---------------------------------------------------------------------------
# cleanup_machine_resources — error paths
# ---------------------------------------------------------------------------


class TestCleanupMachineResourcesErrors:
    def test_instance_not_found_raises_ec2_instance_not_found_error(self):
        adapter = _make_adapter()
        err = AWSError("not found", error_code="InvalidInstanceID.NotFound")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine("i-gone")

        with pytest.raises(EC2InstanceNotFoundError):
            adapter.cleanup_machine_resources(machine)

    def test_network_error_on_check_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = NetworkError("net err")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.cleanup_machine_resources(machine)
        assert "Network error" in str(exc_info.value)

    def test_rate_limit_error_on_check_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = RateLimitError("throttled")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.cleanup_machine_resources(machine)
        assert "Rate limit" in str(exc_info.value)

    def test_aws_error_other_code_raises_aws_error(self):
        adapter = _make_adapter()
        err = AWSError("access denied", error_code="AccessDenied")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine()

        with pytest.raises(AWSError):
            adapter.cleanup_machine_resources(machine)

    def test_volume_cleanup_failure_added_to_failed_list(self):
        """If detaching/deleting a volume raises AWSError it goes into failed list."""
        call_count = [0]

        def execute_side_effect(service, operation, fn):
            if operation == "describe_instances":
                return {"Reservations": [{}]}
            if operation == "describe_volumes":
                return {"Volumes": [{"VolumeId": "vol-fail", "State": "in-use"}]}
            if operation == "describe_network_interfaces":
                return {"NetworkInterfaces": []}
            # detach_volume or delete_volume → raise
            call_count[0] += 1
            raise AWSError("vol error")

        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = execute_side_effect
        machine = _make_machine()

        result = adapter.cleanup_machine_resources(machine)

        assert any(f["id"] == "vol-fail" for f in result["volumes"]["failed"])

    def test_unexpected_exception_raises_resource_cleanup_error(self):
        adapter = _make_adapter()

        call_count = [0]

        def execute_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"Reservations": [{}]}
            raise RuntimeError("unexpected")

        adapter._aws_client.execute_with_circuit_breaker.side_effect = execute_side_effect
        machine = _make_machine()

        with pytest.raises(ResourceCleanupError):
            adapter.cleanup_machine_resources(machine)


# ---------------------------------------------------------------------------
# get_machine_details
# ---------------------------------------------------------------------------


class TestGetMachineDetails:
    def _make_instance_response(self, instance_id: str = "i-0abc123") -> dict:
        return {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": instance_id,
                            "Placement": {"AvailabilityZone": "us-east-1a"},
                            "VpcId": "vpc-001",
                            "SubnetId": "subnet-001",
                            "SecurityGroups": [],
                            "BlockDeviceMappings": [],
                            "EbsOptimized": False,
                            "Monitoring": {"State": "disabled"},
                            "IamInstanceProfile": {},
                            "Tags": [{"Key": "Name", "Value": "test-machine"}],
                        }
                    ]
                }
            ]
        }

    def test_returns_aws_details_dict(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.return_value = (
            self._make_instance_response()
        )
        machine = _make_machine()

        result = adapter.get_machine_details(machine)

        assert "aws_details" in result
        assert "placement" in result["aws_details"]
        assert "network" in result["aws_details"]

    def test_tags_extracted_correctly(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.return_value = (
            self._make_instance_response()
        )
        machine = _make_machine()

        result = adapter.get_machine_details(machine)

        assert result["aws_details"]["tags"]["Name"] == "test-machine"

    def test_raises_error_when_reservations_empty(self):
        # EC2InstanceNotFoundError is a subclass of AWSError; the inner except AWSError
        # handler wraps it into an AWSError before re-raising.
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.return_value = {"Reservations": []}
        machine = _make_machine("i-gone")

        with pytest.raises(AWSError):
            adapter.get_machine_details(machine)

    def test_raises_error_when_instances_empty(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.return_value = {
            "Reservations": [{"Instances": []}]
        }
        machine = _make_machine("i-gone")

        with pytest.raises(AWSError):
            adapter.get_machine_details(machine)

    def test_network_error_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = NetworkError("net err")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.get_machine_details(machine)
        assert "Network error" in str(exc_info.value)

    def test_rate_limit_error_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = RateLimitError("throttled")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.get_machine_details(machine)
        assert "Rate limit" in str(exc_info.value)

    def test_aws_error_not_found_code_raises_ec2_not_found_error(self):
        adapter = _make_adapter()
        err = AWSError("not found", error_code="InvalidInstanceID.NotFound")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine("i-gone")

        with pytest.raises(EC2InstanceNotFoundError):
            adapter.get_machine_details(machine)

    def test_other_aws_error_code_re_raises_aws_error(self):
        adapter = _make_adapter()
        err = AWSError("access denied", error_code="AccessDenied")
        adapter._aws_client.execute_with_circuit_breaker.side_effect = err
        machine = _make_machine()

        with pytest.raises(AWSError):
            adapter.get_machine_details(machine)

    def test_unexpected_exception_raises_aws_error(self):
        adapter = _make_adapter()
        adapter._aws_client.execute_with_circuit_breaker.side_effect = RuntimeError("boom")
        machine = _make_machine()

        with pytest.raises(AWSError) as exc_info:
            adapter.get_machine_details(machine)
        assert "Unexpected error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# create_machine_from_aws_instance — PascalCase validation branches
# ---------------------------------------------------------------------------


class TestCreateMachinePascalCaseValidation:
    def _base_pascal(self) -> dict:
        return {
            "InstanceId": "i-pascal",
            "InstanceType": "t3.medium",
            "State": {"Name": "running"},
            "Placement": {"AvailabilityZone": "us-east-1a"},
            "SubnetId": "subnet-111",
            "VpcId": "vpc-111",
            "ImageId": "ami-0abc123",
            "PrivateIpAddress": "10.0.0.1",
            "SecurityGroups": [],
        }

    def test_missing_state_raises_aws_error(self):
        adapter = _make_adapter()
        data = {k: v for k, v in self._base_pascal().items() if k != "State"}
        with pytest.raises(AWSError):
            adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")

    def test_missing_state_name_raises_aws_error(self):
        adapter = _make_adapter()
        data = {**self._base_pascal(), "State": {}}
        with pytest.raises(AWSError):
            adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")

    def test_invalid_provider_api_raises_aws_error(self):
        adapter = _make_adapter()
        data = self._base_pascal()
        with pytest.raises(AWSError) as exc_info:
            adapter.create_machine_from_aws_instance(data, "req", "InvalidAPI", "fleet-1")
        assert "Invalid provider API" in str(exc_info.value)

    def test_missing_required_field_raises_aws_error(self):
        adapter = _make_adapter()
        data = {k: v for k, v in self._base_pascal().items() if k != "SubnetId"}
        with pytest.raises(AWSError) as exc_info:
            adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        assert "SubnetId" in str(exc_info.value)

    def test_spot_lifecycle_sets_spot_price_type(self):
        adapter = _make_adapter()
        data = {**self._base_pascal(), "InstanceLifecycle": "spot"}
        result = adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        assert result["price_type"] == "spot"

    def test_on_demand_lifecycle_sets_on_demand_price_type(self):
        adapter = _make_adapter()
        data = self._base_pascal()
        result = adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        # PriceType.ON_DEMAND.value == "ondemand"
        assert result["price_type"] == "ondemand"

    def test_security_groups_extracted(self):
        adapter = _make_adapter()
        data = {**self._base_pascal(), "SecurityGroups": [{"GroupId": "sg-001"}]}
        result = adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        assert result["security_group_ids"] == ["sg-001"]

    def test_metadata_ami_and_ebs(self):
        adapter = _make_adapter()
        data = {**self._base_pascal(), "EbsOptimized": True}
        result = adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        assert result["metadata"]["ami_id"] == "ami-0abc123"
        assert result["metadata"]["ebs_optimized"] is True


# ---------------------------------------------------------------------------
# create_machine_from_aws_instance — snake_case specific branches
# ---------------------------------------------------------------------------


class TestCreateMachineSnakeCaseBranches:
    _SNAKE_BASE = {
        "instance_id": "i-0abc123",
        "instance_type": "t3.medium",
        "private_ip": "10.0.0.1",
        "status": "running",
        "image_id": "ami-0abc123",
        "subnet_id": "subnet-111",
        "vpc_id": "vpc-111",
        "placement": {"availability_zone": "us-east-1a"},
        "security_groups": [],
        "launch_time": "2026-01-01T00:00:00+00:00",
    }

    def test_launch_time_added_when_missing(self):
        adapter = _make_adapter()
        data = {k: v for k, v in self._SNAKE_BASE.items() if k != "launch_time"}
        result = adapter.create_machine_from_aws_instance(data, "req", "EC2Fleet", "fleet-1")
        # launch_time should be present (None when missing)
        assert "launch_time" in result
        assert result["launch_time"] is None

    def test_provider_api_injected(self):
        adapter = _make_adapter()
        result = adapter.create_machine_from_aws_instance(
            dict(self._SNAKE_BASE), "req", "RunInstances", "res-1"
        )
        assert result["provider_api"] == "RunInstances"

    def test_resource_id_injected(self):
        adapter = _make_adapter()
        result = adapter.create_machine_from_aws_instance(
            dict(self._SNAKE_BASE), "req", "EC2Fleet", "fleet-xyz"
        )
        assert result["resource_id"] == "fleet-xyz"
