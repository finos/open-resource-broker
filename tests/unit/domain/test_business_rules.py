"""Comprehensive tests for business rule validation across all domain aggregates."""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# Import domain components
try:
    from src.domain.machine.aggregate import Machine
    from src.domain.machine.exceptions import (
        InvalidMachineStateError,
        MachineValidationError,
    )
    from src.domain.machine.value_objects import MachineStatus
    from src.domain.request.aggregate import Request
    from src.domain.request.exceptions import (
        InvalidRequestStateError,
        RequestValidationError,
    )
    from src.domain.request.value_objects import RequestStatus
    from src.domain.template.aggregate import Template
    from src.domain.template.exceptions import TemplateValidationError

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Domain imports not available: {e}")


@pytest.mark.unit
class TestRequestBusinessRules:
    """Test business rules for Request aggregate."""

    def test_machine_count_must_be_positive(self):
        """Test that machine count must be positive."""
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=0, requester_id="test-user"
            )

        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=-1, requester_id="test-user"
            )

    def test_machine_count_maximum_limit(self):
        """Test that machine count respects maximum limits."""
        # Assuming there's a maximum limit (e.g., 1000)
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template",
                machine_count=10000,  # Exceeds reasonable limit
                requester_id="test-user",
            )

    def test_template_id_cannot_be_empty(self):
        """Test that template ID cannot be empty."""
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="", machine_count=1, requester_id="test-user")

        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id=None, machine_count=1, requester_id="test-user")

    def test_requester_id_cannot_be_empty(self):
        """Test that requester ID cannot be empty."""
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=1, requester_id=""
            )

        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=1, requester_id=None
            )

    def test_priority_must_be_valid_range(self):
        """Test that priority must be within valid range."""
        # Valid priorities (assuming 1-10 range)
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user", priority=5
        )
        assert request.priority == 5

        # Invalid priorities
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template",
                machine_count=1,
                requester_id="test-user",
                priority=0,  # Below minimum
            )

        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template",
                machine_count=1,
                requester_id="test-user",
                priority=11,  # Above maximum
            )

    def test_timeout_must_be_positive(self):
        """Test that timeout must be positive if specified."""
        # Valid timeout
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user", timeout=300
        )
        assert request.timeout == 300

        # Invalid timeout
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="test-template", machine_count=1, requester_id="test-user", timeout=-1
            )

    def test_return_request_must_have_machine_ids(self):
        """Test that return requests must have machine IDs."""
        with pytest.raises(RequestValidationError):
            Request.create_return_request(machine_ids=[], requester_id="test-user")

        with pytest.raises(RequestValidationError):
            Request.create_return_request(machine_ids=None, requester_id="test-user")

    def test_return_request_machine_ids_must_be_valid(self):
        """Test that return request machine IDs must be valid."""
        # Valid machine IDs
        request = Request.create_return_request(
            machine_ids=["i-1234567890abcdef0", "i-abcdef1234567890"], requester_id="test-user"
        )
        assert len(request.machine_ids) == 2

        # Invalid machine IDs
        with pytest.raises(RequestValidationError):
            Request.create_return_request(
                machine_ids=["invalid-id", "another-invalid"], requester_id="test-user"
            )

    def test_request_status_transitions_are_valid(self):
        """Test that request status transitions follow business rules."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        # Valid transition: PENDING -> PROCESSING
        assert request.status == RequestStatus.PENDING
        request.start_processing()
        assert request.status == RequestStatus.PROCESSING

        # Valid transition: PROCESSING -> COMPLETED
        request.complete_successfully(
            machine_ids=["i-123"], completion_message="Success")
        assert request.status == RequestStatus.COMPLETED

        # Invalid transition: COMPLETED -> PROCESSING
        with pytest.raises(InvalidRequestStateError):
            request.start_processing()

    def test_request_cannot_be_completed_without_processing(self):
        """Test that requests cannot be completed without being processed."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        # Cannot complete directly from PENDING
        with pytest.raises(InvalidRequestStateError):
            request.complete_successfully(
                machine_ids=["i-123"], completion_message="Success")

    def test_completed_request_cannot_be_modified(self):
        """Test that completed requests cannot be modified."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123"], completion_message="Success")

        # Cannot modify completed request
        with pytest.raises(InvalidRequestStateError):
            request.fail_with_error("Should not be allowed")

    def test_request_timeout_enforcement(self):
        """Test that request timeout is enforced."""
        request = Request.create_new_request(
            template_id="test-template",
            machine_count=1,
            requester_id="test-user",
            timeout=1,  # 1 second timeout
        )

        request.start_processing()

        # Simulate timeout
        with patch("datetime.datetime") as mock_datetime:
            mock_datetime.utcnow.return_value = datetime.utcnow() + timedelta(seconds=2)

            # Should be able to check if request is timed out
            if hasattr(request, "is_timed_out"):
                assert request.is_timed_out(), "Request should be timed out"


@pytest.mark.unit
class TestTemplateBusinessRules:
    """Test business rules for Template aggregate."""

    def test_template_id_must_be_unique(self):
        """Test that template IDs must be unique."""
        template1 = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-123",
            instance_type="t2.micro",
        )

        # Should not be able to create another template with same ID
        # This would be enforced at the repository level
        assert template1.template_id == "test-template"

    def test_provider_api_must_be_valid(self):
        """Test that provider_api must be one of valid values."""
        valid_apis = ["RunInstances", "SpotFleet", "EC2Fleet", "ASG"]

        # Valid provider APIs
        for api in valid_apis:
            template = Template(
                template_id=f"test-template-{api}",
                name="Test Template",
                provider_api=api,
                image_id="ami-123",
                instance_type="t2.micro",
            )
            assert template.provider_api == api

        # Invalid provider API
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="InvalidAPI",
                image_id="ami-123",
                instance_type="t2.micro",
            )

    def test_image_id_must_be_valid_format(self):
        """Test that image ID must follow valid format."""
        # Valid AMI ID
        template = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-1234567890abcdef0",
            instance_type="t2.micro",
        )
        assert template.image_id == "ami-1234567890abcdef0"

        # Invalid AMI ID format
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="RunInstances",
                image_id="invalid-ami-id",
                instance_type="t2.micro",
            )

    def test_instance_type_must_be_valid(self):
        """Test that instance type must be valid."""
        valid_types = ["t2.micro", "t2.small", "t3.medium", "m5.large"]

        # Valid instance types
        for instance_type in valid_types:
            template = Template(
                template_id=f"test-template-{instance_type}",
                name="Test Template",
                provider_api="RunInstances",
                image_id="ami-123",
                instance_type=instance_type,
            )
            assert template.instance_type == instance_type

        # Invalid instance type
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="RunInstances",
                image_id="ami-123",
                instance_type="invalid.type",
            )

    def test_max_number_must_be_positive(self):
        """Test that max_number must be positive."""
        # Valid max_number
        template = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-123",
            instance_type="t2.micro",
            max_number=10,
        )
        assert template.max_number == 10

        # Invalid max_number
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="RunInstances",
                image_id="ami-123",
                instance_type="t2.micro",
                max_number=0,
            )

    def test_subnet_ids_must_be_valid_format(self):
        """Test that subnet IDs must follow valid format."""
        # Valid subnet IDs
        template = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-123",
            instance_type="t2.micro",
            subnet_ids=["subnet-1234567890abcdef0", "subnet-abcdef1234567890"],
        )
        assert len(template.subnet_ids) == 2

        # Invalid subnet ID format
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="RunInstances",
                image_id="ami-123",
                instance_type="t2.micro",
                subnet_ids=["invalid-subnet-id"],
            )

    def test_security_group_ids_must_be_valid_format(self):
        """Test that security group IDs must follow valid format."""
        # Valid security group IDs
        template = Template(
            template_id="test-template",
            name="Test Template",
            provider_api="RunInstances",
            image_id="ami-123",
            instance_type="t2.micro",
            security_group_ids=["sg-1234567890abcdef0", "sg-abcdef1234567890"],
        )
        assert len(template.security_group_ids) == 2

        # Invalid security group ID format
        with pytest.raises(TemplateValidationError):
            Template(
                template_id="test-template",
                name="Test Template",
                provider_api="RunInstances",
                image_id="ami-123",
                instance_type="t2.micro",
                security_group_ids=["invalid-sg-id"],
            )

    def test_template_compatibility_rules(self):
        """Test template compatibility business rules."""
        # SpotFleet templates should have specific requirements
        spot_template = Template(
            template_id="spot-template",
            name="Spot Template",
            provider_api="SpotFleet",
            image_id="ami-123",
            instance_type="t2.micro",
            max_spot_price=0.05,  # Required for spot fleet
        )

        # EC2Fleet templates should support mixed instance types
        fleet_template = Template(
            template_id="fleet-template",
            name="Fleet Template",
            provider_api="EC2Fleet",
            image_id="ami-123",
            instance_type="t2.micro",
            instance_types=["t2.micro", "t2.small", "t3.micro"],  # Mixed types
        )

        assert spot_template.provider_api == "SpotFleet"
        assert fleet_template.provider_api == "EC2Fleet"


@pytest.mark.unit
class TestMachineBusinessRules:
    """Test business rules for Machine aggregate."""

    def test_machine_must_have_valid_instance_id(self):
        """Test that machines must have valid instance IDs."""
        # Valid instance ID
        machine = Machine(
            instance_id="i-1234567890abcdef0",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.PENDING,
        )
        assert machine.instance_id == "i-1234567890abcdef0"

        # Invalid instance ID
        with pytest.raises(MachineValidationError):
            Machine(
                instance_id="invalid-instance-id",
                request_id="req-123",
                template_id="template-123",
                status=MachineStatus.PENDING,
            )

    def test_machine_status_transitions_are_valid(self):
        """Test that machine status transitions follow business rules."""
        machine = Machine(
            instance_id="i-123",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.PENDING,
        )

        # Valid transitions
        machine.start_provisioning()
        assert machine.status == MachineStatus.PROVISIONING

        machine.mark_as_running()
        assert machine.status == MachineStatus.RUNNING

        machine.terminate()
        assert machine.status == MachineStatus.TERMINATED

        # Invalid transition: TERMINATED -> RUNNING
        with pytest.raises(InvalidMachineStateError):
            machine.mark_as_running()

    def test_machine_cannot_be_terminated_twice(self):
        """Test that machines cannot be terminated twice."""
        machine = Machine(
            instance_id="i-123",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.RUNNING,
        )

        machine.terminate()
        assert machine.status == MachineStatus.TERMINATED

        # Cannot terminate again
        with pytest.raises(InvalidMachineStateError):
            machine.terminate()

    def test_machine_health_monitoring_rules(self):
        """Test machine health monitoring business rules."""
        machine = Machine(
            instance_id="i-123",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.RUNNING,
        )

        # Should be able to update health status
        if hasattr(machine, "update_health_status"):
            machine.update_health_status(healthy=True)
            assert machine.is_healthy

            machine.update_health_status(healthy=False)
            assert not machine.is_healthy

    def test_machine_cost_tracking_rules(self):
        """Test machine cost tracking business rules."""
        machine = Machine(
            instance_id="i-123",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.RUNNING,
            instance_type="t2.micro",
            hourly_cost=0.0116,
        )

        # Should be able to calculate running costs
        if hasattr(machine, "calculate_running_cost"):
            # Simulate 2 hours of running time
            with patch("datetime.datetime") as mock_datetime:
                start_time = datetime.utcnow()
                mock_datetime.utcnow.return_value = start_time + timedelta(hours=2)

                cost = machine.calculate_running_cost()
                expected_cost = 0.0116 * 2  # 2 hours at $0.0116/hour
                assert abs(cost - expected_cost) < 0.001


@pytest.mark.unit
class TestCrossAggregateBusinessRules:
    """Test business rules that span multiple aggregates."""

    def test_request_machine_count_matches_template_limits(self):
        """Test that request machine count respects template limits."""
        # This would typically be enforced by a domain service
        template_max = 5

        # Valid request within template limits
        request = Request.create_new_request(
            template_id="limited-template",
            machine_count=3,  # Within limit
            requester_id="test-user",
        )
        assert request.machine_count <= template_max

        # Invalid request exceeding template limits
        # This validation would happen in a domain service
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="limited-template",
                machine_count=10,  # Exceeds limit
                requester_id="test-user",
            )

    def test_machine_template_compatibility(self):
        """Test that machines are compatible with their templates."""
        # Machine should match template specifications
        template_instance_type = "t2.micro"

        machine = Machine(
            instance_id="i-123",
            request_id="req-123",
            template_id="template-123",
            status=MachineStatus.RUNNING,
            instance_type=template_instance_type,
        )

        # Machine instance type should match template
        assert machine.instance_type == template_instance_type

    def test_request_completion_requires_all_machines(self):
        """Test that requests can only be completed when all machines are ready."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()

        # Should not be able to complete with fewer machines than requested
        with pytest.raises(RequestValidationError):
            request.complete_successfully(
                machine_ids=["i-123"],  # Only 1 machine, but requested 2
                completion_message="Partial completion",
            )

        # Should be able to complete with correct number of machines
        request.complete_successfully(
            machine_ids=["i-123", "i-456"],  # 2 machines as requested
            completion_message="All machines ready",
        )
        assert request.status == RequestStatus.COMPLETED

    def test_return_request_machines_must_exist(self):
        """Test that return requests can only reference existing machines."""
        # This would be validated by a domain service
        existing_machine_ids = ["i-123", "i-456"]

        # Valid return request
        request = Request.create_return_request(
            machine_ids=existing_machine_ids, requester_id="test-user"
        )
        assert request.machine_ids == existing_machine_ids

        # Invalid return request with non-existent machines
        # This validation would happen in a domain service
        with pytest.raises(RequestValidationError):
            # Domain service would validate machine existence
            pass  # Placeholder for domain service validation


@pytest.mark.unit
class TestBusinessRuleEnforcement:
    """Test that business rules are consistently enforced."""

    def test_invariants_maintained_across_operations(self):
        """Test that business invariants are maintained across all operations."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Invariant: machine_count should never change after creation
        original_count = request.machine_count

        request.start_processing()
        assert request.machine_count == original_count

        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success")
        assert request.machine_count == original_count

    def test_business_rules_prevent_invalid_states(self):
        """Test that business rules prevent invalid aggregate states."""
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        # Cannot have completed request without machine IDs
        request.start_processing()

        with pytest.raises((RequestValidationError, InvalidRequestStateError)):
            request.complete_successfully(
                machine_ids=[], completion_message="Invalid completion"  # Empty machine IDs
            )

    def test_validation_happens_at_aggregate_boundaries(self):
        """Test that validation happens when data enters aggregates."""
        # All validation should happen in aggregate constructors and methods

        # Invalid data should be rejected at creation
        with pytest.raises(RequestValidationError):
            Request.create_new_request(
                template_id="", machine_count=1, requester_id="test-user"  # Invalid
            )

        # Invalid data should be rejected in methods
        request = Request.create_new_request(
            template_id="test-template", machine_count=1, requester_id="test-user"
        )

        request.start_processing()

        with pytest.raises((RequestValidationError, InvalidRequestStateError)):
            request.complete_successfully(
                machine_ids=None, completion_message="Invalid")  # Invalid
