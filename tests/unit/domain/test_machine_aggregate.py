"""Unit tests for Machine aggregate."""

from datetime import datetime, timedelta, timezone

import pytest

from domain.base.value_objects import InstanceId, InstanceType
from domain.machine.aggregate import Machine
from domain.machine.exceptions import (
    InvalidMachineStateError,
    MachineNotFoundError,
    MachineValidationError,
)
from domain.machine.value_objects import MachineId, MachineStatus

# Try to import optional classes - skip tests if not available
try:
    from domain.machine.value_objects import (
        HealthStatus,
        MachineOperationError,
        NetworkConfiguration,
        PerformanceMetrics,
    )

    OPTIONAL_CLASSES_AVAILABLE = True
except ImportError:
    OPTIONAL_CLASSES_AVAILABLE = False

    # Create mock classes for tests that need them
    class HealthStatus:
        UNKNOWN = "unknown"
        HEALTHY = "healthy"
        UNHEALTHY = "unhealthy"
        WARNING = "warning"

        def __init__(self, value):
            self.value = value

    class PerformanceMetrics:
        def __init__(self, **kwargs):
            pass

    class NetworkConfiguration:
        def __init__(self, **kwargs):
            pass

    class MachineOperationError(Exception):
        def __init__(self, **kwargs):
            super().__init__()


@pytest.mark.unit
class TestMachineAggregate:
    """Test cases for Machine aggregate."""

    def test_machine_creation(self):
        """Test basic machine creation."""
        machine = Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            private_ip="10.0.1.100",
            public_ip="54.123.45.67",
            tags={"Environment": "test", "Project": "hostfactory"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        assert machine.id == "machine-001"
        assert machine.instance_id.value == "i-1234567890abcdef0"
        assert machine.template_id == "template-001"
        assert machine.request_id == "request-001"
        assert machine.status == "running"
        assert machine.instance_type.value == "t2.micro"
        assert machine.availability_zone == "us-east-1a"
        assert machine.private_ip == "10.0.1.100"
        assert machine.public_ip == "54.123.45.67"
        assert machine.tags["Environment"] == "test"
        assert machine.tags["Project"] == "hostfactory"
        assert machine.created_at is not None
        assert machine.updated_at is not None

    def test_machine_creation_minimal(self):
        """Test machine creation with minimal required data."""
        machine = Machine(
            id="machine-002",
            instance_id=InstanceId("i-abcdef1234567890"),
            template_id="template-002",
            request_id="request-002",
            status="pending",
            instance_type=InstanceType("t3.small"),
            availability_zone="us-west-2b",
        )

        assert machine.id == "machine-002"
        assert machine.instance_id.value == "i-abcdef1234567890"
        assert machine.template_id == "template-002"
        assert machine.request_id == "request-002"
        assert machine.status == "pending"
        assert machine.instance_type.value == "t3.small"
        assert machine.availability_zone == "us-west-2b"
        assert machine.private_ip is None
        assert machine.public_ip is None
        assert machine.tags == {}

    def test_machine_status_transitions(self):
        """Test valid machine status transitions."""
        machine = Machine(
            id="machine-003",
            instance_id=InstanceId("i-1111222233334444"),
            template_id="template-001",
            request_id="request-001",
            status="pending",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # PENDING -> LAUNCHING
        machine.start_launching()
        assert machine.status == MachineStatus.LAUNCHING
        assert machine.launched_at is not None

        # LAUNCHING -> RUNNING
        machine.mark_as_running(private_ip="10.0.1.100", public_ip="54.123.45.67")
        assert machine.status == MachineStatus.RUNNING
        assert machine.private_ip == "10.0.1.100"
        assert machine.public_ip == "54.123.45.67"
        assert machine.running_since is not None

        # RUNNING -> STOPPING
        machine.start_stopping()
        assert machine.status == MachineStatus.STOPPING
        assert machine.stop_initiated_at is not None

        # STOPPING -> STOPPED
        machine.mark_as_stopped()
        assert machine.status == MachineStatus.STOPPED
        assert machine.stopped_at is not None

    def test_machine_failure_transitions(self):
        """Test machine failure transitions."""
        machine = Machine(
            id="machine-004",
            instance_id=InstanceId("i-5555666677778888"),
            template_id="template-001",
            request_id="request-001",
            status="launching",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # LAUNCHING -> FAILED
        error_message = "Instance failed to launch: Insufficient capacity"
        machine.mark_as_failed(error_message)

        assert machine.status == MachineStatus.FAILED
        assert machine.error_message == error_message
        assert machine.failed_at is not None

    def test_machine_termination(self):
        """Test machine termination."""
        machine = Machine(
            id="machine-005",
            instance_id=InstanceId("i-9999aaaabbbbcccc"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # RUNNING -> TERMINATING
        machine.start_terminating("User requested termination")
        assert machine.status == MachineStatus.TERMINATING
        assert machine.termination_reason == "User requested termination"
        assert machine.termination_initiated_at is not None

        # TERMINATING -> TERMINATED
        machine.mark_as_terminated()
        assert machine.status == MachineStatus.TERMINATED
        assert machine.terminated_at is not None

    def test_invalid_status_transitions(self):
        """Test invalid machine status transitions."""
        machine = Machine(
            id="machine-006",
            instance_id=InstanceId("i-ddddeeeeffffaaaa"),
            template_id="template-001",
            request_id="request-001",
            status="terminated",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # Cannot transition from TERMINATED to any other state
        with pytest.raises(InvalidMachineStateError):
            machine.start_launching()

        with pytest.raises(InvalidMachineStateError):
            machine.mark_as_running()

        with pytest.raises(InvalidMachineStateError):
            machine.start_stopping()

    def test_machine_health_monitoring(self):
        """Test machine health monitoring."""
        machine = Machine(
            id="machine-007",
            instance_id=InstanceId("i-1234abcd5678efgh"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # Update health status
        machine.update_health_status(
            status=HealthStatus.HEALTHY,
            last_check=datetime.now(timezone.utc),
            details={"cpu_usage": 25.5, "memory_usage": 60.2},
        )

        assert machine.health_status == HealthStatus.HEALTHY
        assert machine.last_health_check is not None
        assert machine.health_details["cpu_usage"] == 25.5
        assert machine.health_details["memory_usage"] == 60.2

        # Mark as unhealthy
        machine.update_health_status(
            status=HealthStatus.UNHEALTHY,
            last_check=datetime.now(timezone.utc),
            details={"error": "High CPU usage", "cpu_usage": 95.0},
        )

        assert machine.health_status == HealthStatus.UNHEALTHY
        assert machine.health_details["error"] == "High CPU usage"
        assert machine.health_details["cpu_usage"] == 95.0

    def test_machine_performance_metrics(self):
        """Test machine performance metrics."""
        machine = Machine(
            id="machine-008",
            instance_id=InstanceId("i-abcd1234efgh5678"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        # Update performance metrics
        metrics = PerformanceMetrics(
            cpu_utilization=45.2,
            memory_utilization=70.8,
            disk_utilization=30.5,
            network_in_bytes=1024000,
            network_out_bytes=512000,
            timestamp=datetime.now(timezone.utc),
        )

        machine.update_performance_metrics(metrics)

        assert machine.performance_metrics.cpu_utilization == 45.2
        assert machine.performance_metrics.memory_utilization == 70.8
        assert machine.performance_metrics.disk_utilization == 30.5
        assert machine.performance_metrics.network_in_bytes == 1024000
        assert machine.performance_metrics.network_out_bytes == 512000
        assert machine.performance_metrics.timestamp is not None

    def test_machine_network_configuration(self):
        """Test machine network configuration."""
        network_config = NetworkConfiguration(
            vpc_id="vpc-12345678",
            subnet_id="subnet-12345678",
            security_group_ids=["sg-12345678", "sg-87654321"],
            private_dns_name="ip-10-0-1-100.ec2.internal",
            public_dns_name="ec2-54-123-45-67.compute-1.amazonaws.com",
        )

        machine = Machine(
            id="machine-009",
            instance_id=InstanceId("i-network123456789"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            network_configuration=network_config,
        )

        assert machine.network_configuration.vpc_id == "vpc-12345678"
        assert machine.network_configuration.subnet_id == "subnet-12345678"
        assert "sg-12345678" in machine.network_configuration.security_group_ids
        assert "sg-87654321" in machine.network_configuration.security_group_ids
        assert machine.network_configuration.private_dns_name == "ip-10-0-1-100.ec2.internal"
        assert (
            machine.network_configuration.public_dns_name
            == "ec2-54-123-45-67.compute-1.amazonaws.com"
        )

    def test_machine_uptime_calculation(self):
        """Test machine uptime calculation."""
        start_time = datetime.now(timezone.utc) - timedelta(hours=2, minutes=30)

        machine = Machine(
            id="machine-010",
            instance_id=InstanceId("i-uptime123456789"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            running_since=start_time,
        )

        uptime = machine.get_uptime()
        assert uptime is not None
        assert uptime.total_seconds() >= 2.5 * 3600  # At least 2.5 hours

        # Test uptime for non-running machine
        machine.status = MachineStatus.STOPPED
        uptime = machine.get_uptime()
        assert uptime is None

    def test_machine_cost_tracking(self):
        """Test machine cost tracking."""
        machine = Machine(
            id="machine-011",
            instance_id=InstanceId("i-cost123456789abc"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            hourly_cost=0.0116,  # t2.micro cost per hour
        )

        # Set running time
        machine.running_since = datetime.now(timezone.utc) - timedelta(hours=10)

        # Calculate estimated cost
        estimated_cost = machine.get_estimated_cost()
        assert estimated_cost is not None
        assert estimated_cost >= 0.116  # At least 10 hours * $0.0116

        # Test cost for non-running machine
        machine.status = MachineStatus.STOPPED
        estimated_cost = machine.get_estimated_cost()
        assert estimated_cost == 0.0

    def test_machine_validation_required_fields(self):
        """Test machine validation for required fields."""
        # Test missing required fields
        required_fields = [
            "id",
            "instance_id",
            "template_id",
            "request_id",
            "status",
            "instance_type",
            "availability_zone",
        ]

        base_machine_data = {
            "id": "machine-001",
            "instance_id": InstanceId("i-1234567890abcdef0"),
            "template_id": "template-001",
            "request_id": "request-001",
            "status": "running",
            "instance_type": InstanceType("t2.micro"),
            "availability_zone": "us-east-1a",
        }

        for field in required_fields:
            machine_data = base_machine_data.copy()
            del machine_data[field]

            with pytest.raises((ValueError, TypeError, MachineValidationError)):
                Machine(**machine_data)

    def test_machine_validation_instance_id(self):
        """Test machine validation for instance ID."""
        # Valid instance IDs are tested in base value objects
        # Test invalid instance ID format
        with pytest.raises((ValueError, MachineValidationError)):
            Machine(
                id="machine-invalid",
                instance_id=InstanceId("invalid-instance-id"),
                template_id="template-001",
                request_id="request-001",
                status="running",
                instance_type=InstanceType("t2.micro"),
                availability_zone="us-east-1a",
            )

    def test_machine_validation_ip_addresses(self):
        """Test machine validation for IP addresses."""
        # Valid IP addresses
        valid_ips = ["10.0.1.100", "192.168.1.1", "172.16.0.1"]

        for ip in valid_ips:
            machine = Machine(
                id="machine-valid-ip",
                instance_id=InstanceId("i-1234567890abcdef0"),
                template_id="template-001",
                request_id="request-001",
                status="running",
                instance_type=InstanceType("t2.micro"),
                availability_zone="us-east-1a",
                private_ip=ip,
                public_ip=ip,
            )
            assert machine.private_ip == ip
            assert machine.public_ip == ip

        # Invalid IP addresses
        invalid_ips = ["256.256.256.256", "invalid-ip", "192.168.1"]

        for ip in invalid_ips:
            with pytest.raises((ValueError, MachineValidationError)):
                Machine(
                    id="machine-invalid-ip",
                    instance_id=InstanceId("i-1234567890abcdef0"),
                    template_id="template-001",
                    request_id="request-001",
                    status="running",
                    instance_type=InstanceType("t2.micro"),
                    availability_zone="us-east-1a",
                    private_ip=ip,
                )

    def test_machine_tags_operations(self):
        """Test machine tags operations."""
        machine = Machine(
            id="machine-tags",
            instance_id=InstanceId("i-tags123456789abc"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            tags={"Environment": "test"},
        )

        # Add tag
        machine.tags["Project"] = "hostfactory"
        assert machine.tags["Project"] == "hostfactory"

        # Update tag
        machine.tags["Environment"] = "production"
        assert machine.tags["Environment"] == "production"

        # Check tag existence
        assert "Environment" in machine.tags
        assert "Project" in machine.tags
        assert "NonExistent" not in machine.tags

        # Remove tag
        del machine.tags["Environment"]
        assert "Environment" not in machine.tags
        assert "Project" in machine.tags

    def test_machine_equality(self):
        """Test machine equality based on ID."""
        machine1 = Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        machine2 = Machine(
            id="machine-001",  # Same ID
            instance_id=InstanceId("i-abcdef1234567890"),  # Different instance ID
            template_id="template-002",  # Different template
            request_id="request-002",  # Different request
            status="stopped",  # Different status
            instance_type=InstanceType("t2.small"),  # Different instance type
            availability_zone="us-west-2a",  # Different AZ
        )

        machine3 = Machine(
            id="machine-002",  # Different ID
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        assert machine1 == machine2  # Same ID
        assert machine1 != machine3  # Different ID
        assert machine2 != machine3  # Different ID

    def test_machine_hash(self):
        """Test machine hashing."""
        machine1 = Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        machine2 = Machine(
            id="machine-001",  # Same ID
            instance_id=InstanceId("i-different123456"),
            template_id="template-different",
            request_id="request-different",
            status="stopped",
            instance_type=InstanceType("t2.large"),
            availability_zone="us-west-2b",
        )

        assert hash(machine1) == hash(machine2)  # Same ID should have same hash

    def test_machine_serialization(self):
        """Test machine serialization to dict."""
        machine = Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
            private_ip="10.0.1.100",
            public_ip="54.123.45.67",
            tags={"Environment": "test"},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        machine_dict = machine.model_dump()

        assert machine_dict["id"] == "machine-001"
        assert machine_dict["instance_id"] == "i-1234567890abcdef0"
        assert machine_dict["template_id"] == "template-001"
        assert machine_dict["request_id"] == "request-001"
        assert machine_dict["status"] == "running"
        assert machine_dict["instance_type"] == "t2.micro"
        assert machine_dict["availability_zone"] == "us-east-1a"
        assert machine_dict["private_ip"] == "10.0.1.100"
        assert machine_dict["public_ip"] == "54.123.45.67"
        assert machine_dict["tags"] == {"Environment": "test"}
        assert "created_at" in machine_dict
        assert "updated_at" in machine_dict

    def test_machine_deserialization(self):
        """Test machine deserialization from dict."""
        machine_dict = {
            "id": "machine-001",
            "instance_id": "i-1234567890abcdef0",
            "template_id": "template-001",
            "request_id": "request-001",
            "status": "running",
            "instance_type": "t2.micro",
            "availability_zone": "us-east-1a",
            "private_ip": "10.0.1.100",
            "public_ip": "54.123.45.67",
            "tags": {"Environment": "test"},
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
        }

        machine = Machine(**machine_dict)

        assert machine.id == "machine-001"
        assert machine.instance_id.value == "i-1234567890abcdef0"
        assert machine.template_id == "template-001"
        assert machine.request_id == "request-001"
        assert machine.status == "running"
        assert machine.instance_type.value == "t2.micro"
        assert machine.availability_zone == "us-east-1a"
        assert machine.private_ip == "10.0.1.100"
        assert machine.public_ip == "54.123.45.67"
        assert machine.tags == {"Environment": "test"}

    def test_machine_string_representation(self):
        """Test machine string representation."""
        machine = Machine(
            id="machine-001",
            instance_id=InstanceId("i-1234567890abcdef0"),
            template_id="template-001",
            request_id="request-001",
            status="running",
            instance_type=InstanceType("t2.micro"),
            availability_zone="us-east-1a",
        )

        str_repr = str(machine)
        assert "machine-001" in str_repr
        assert "i-1234567890abcdef0" in str_repr
        assert "running" in str_repr

        repr_str = repr(machine)
        assert "Machine" in repr_str
        assert "machine-001" in repr_str


@pytest.mark.unit
class TestMachineValueObjects:
    """Test cases for Machine-specific value objects."""

    def test_machine_id_creation(self):
        """Test MachineId creation."""
        machine_id = MachineId("machine-001")
        assert str(machine_id) == "machine-001"
        assert machine_id.value == "machine-001"

    def test_machine_status_enum(self):
        """Test MachineStatus enum."""
        assert MachineStatus.PENDING.value == "pending"
        assert MachineStatus.LAUNCHING.value == "launching"
        assert MachineStatus.RUNNING.value == "running"
        assert MachineStatus.STOPPING.value == "stopping"
        assert MachineStatus.STOPPED.value == "stopped"
        assert MachineStatus.TERMINATING.value == "terminating"
        assert MachineStatus.TERMINATED.value == "terminated"
        assert MachineStatus.FAILED.value == "failed"

    def test_health_status_enum(self):
        """Test HealthStatus enum."""
        assert HealthStatus.UNKNOWN.value == "unknown"
        assert HealthStatus.HEALTHY.value == "healthy"
        assert HealthStatus.UNHEALTHY.value == "unhealthy"
        assert HealthStatus.WARNING.value == "warning"

    def test_performance_metrics_creation(self):
        """Test PerformanceMetrics creation."""
        metrics = PerformanceMetrics(
            cpu_utilization=45.2,
            memory_utilization=70.8,
            disk_utilization=30.5,
            network_in_bytes=1024000,
            network_out_bytes=512000,
            timestamp=datetime.now(timezone.utc),
        )

        assert metrics.cpu_utilization == 45.2
        assert metrics.memory_utilization == 70.8
        assert metrics.disk_utilization == 30.5
        assert metrics.network_in_bytes == 1024000
        assert metrics.network_out_bytes == 512000
        assert metrics.timestamp is not None

    def test_network_configuration_creation(self):
        """Test NetworkConfiguration creation."""
        config = NetworkConfiguration(
            vpc_id="vpc-12345678",
            subnet_id="subnet-12345678",
            security_group_ids=["sg-12345678", "sg-87654321"],
            private_dns_name="ip-10-0-1-100.ec2.internal",
            public_dns_name="ec2-54-123-45-67.compute-1.amazonaws.com",
        )

        assert config.vpc_id == "vpc-12345678"
        assert config.subnet_id == "subnet-12345678"
        assert len(config.security_group_ids) == 2
        assert "sg-12345678" in config.security_group_ids
        assert "sg-87654321" in config.security_group_ids
        assert config.private_dns_name == "ip-10-0-1-100.ec2.internal"
        assert config.public_dns_name == "ec2-54-123-45-67.compute-1.amazonaws.com"


@pytest.mark.unit
class TestMachineExceptions:
    """Test cases for Machine-specific exceptions."""

    def test_machine_validation_error(self):
        """Test MachineValidationError."""
        error = MachineValidationError("Invalid machine configuration")
        assert str(error) == "Invalid machine configuration"
        assert isinstance(error, Exception)

    def test_machine_not_found_error(self):
        """Test MachineNotFoundError."""
        error = MachineNotFoundError("Machine not found", machine_id="machine-001")
        assert str(error) == "Machine not found"
        assert error.machine_id == "machine-001"

    def test_invalid_machine_state_error(self):
        """Test InvalidMachineStateError."""
        error = InvalidMachineStateError(
            "Cannot transition from terminated to running",
            current_state="terminated",
            attempted_state="running",
        )
        assert "Cannot transition" in str(error)
        assert error.current_state == "terminated"
        assert error.attempted_state == "running"

    def test_machine_operation_error(self):
        """Test MachineOperationError."""
        error = MachineOperationError(
            "Failed to start machine", machine_id="machine-001", operation="start"
        )
        assert str(error) == "Failed to start machine"
        assert error.machine_id == "machine-001"
        assert error.operation == "start"
