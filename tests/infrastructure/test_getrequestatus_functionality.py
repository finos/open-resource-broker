"""Test suite for getRequestStatus functionality."""

from unittest.mock import MagicMock, Mock, patch

import pytest

from application.request.dto import MachineReferenceDTO
from application.services.provider_selection_service import ProviderSelectionResult
from domain.base.ports import LoggingPort
from domain.base.ports.configuration_port import ConfigurationPort
from infrastructure.scheduler.hostfactory.hostfactory_strategy import HostFactorySchedulerStrategy


class MockRequestDTO:
    """Mock RequestDTO for testing."""

    def __init__(self, request_id="test-123", status="completed", machines=None):
        """Initialize the instance."""
        self.request_id = request_id
        self.status = status
        self.machines = machines or []
        self.machine_references = []

        for m in machines or []:
            machine_ref = MachineReferenceDTO(
                machine_id=m.get("instance_id", ""),
                name="",
                result="succeed" if m.get("status") == "running" else "executing",
                status=m.get("status", ""),
                private_ip_address=m.get("private_ip", ""),
                public_ip_address=m.get("public_ip"),
                launch_time=m.get("launch_time_timestamp"),
                message="",
            )
            self.machine_references.append(machine_ref)

    def to_dict(self):
        """Convert to dictionary format."""
        return {
            "request_id": self.request_id,
            "status": self.status,
            "machines": [
                {
                    "instance_id": m.machine_id,
                    "status": m.status,
                    "private_ip": m.private_ip_address,
                    "public_ip": m.public_ip_address,
                    "launch_time_timestamp": m.launch_time or 0,
                }
                for m in self.machine_references
            ],
        }


class TestGetRequestStatusFunctionality:
    """Test suite for getRequestStatus functionality."""

    @pytest.fixture
    def scheduler_strategy(self):
        """Create scheduler strategy for testing."""
        with patch("infrastructure.di.container.get_container") as mock_get_container:
            mock_container = Mock()
            mock_provider_service = Mock()
            mock_provider_service.select_active_provider.return_value = ProviderSelectionResult(
                provider_type="aws", provider_instance="aws-default", selection_reason="test"
            )
            mock_container.get.return_value = mock_provider_service
            mock_get_container.return_value = mock_container

            config_manager = MagicMock(spec=ConfigurationPort)
            logger = MagicMock(spec=LoggingPort)
            return HostFactorySchedulerStrategy(config_manager, logger)

    def test_hostfactory_format_basic(self, scheduler_strategy):
        """Test basic HostFactory formatting for getRequestStatus."""
        # Create test data
        mock_dto = MockRequestDTO(
            request_id="test-request-123",
            status="completed",
            machines=[
                {
                    "instance_id": "i-1234567890abcdef0",
                    "status": "running",
                    "private_ip": "10.0.1.100",
                    "public_ip": "54.123.45.67",
                    "launch_time_timestamp": 1734619942,
                }
            ],
        )

        # Convert to HostFactory format
        result = scheduler_strategy.convert_domain_to_hostfactory_output(
            "getRequestStatus", mock_dto
        )

        # Verify structure matches hf_docs/input-output.md
        assert "requests" in result
        assert len(result["requests"]) == 1

        request = result["requests"][0]
        assert request["requestId"] == "test-request-123"
        assert request["status"] == "complete"  # Mapped from 'completed'
        assert "machines" in request
        assert len(request["machines"]) == 1

        machine = request["machines"][0]
        assert machine["machineId"] == "i-1234567890abcdef0"
        assert machine["result"] == "succeed"  # Mapped from 'running'
        assert machine["status"] == "running"
        assert machine["privateIpAddress"] == "10.0.1.100"
        assert machine["publicIpAddress"] == "54.123.45.67"
        assert machine["launchtime"] == 1734619942
        assert machine["message"] == ""

    def test_hostfactory_format_multiple_machines(self, scheduler_strategy):
        """Test HostFactory formatting with multiple machines."""
        mock_dto = MockRequestDTO(
            request_id="multi-test-456",
            status="completed",
            machines=[
                {
                    "instance_id": "i-1111111111111111",
                    "status": "running",
                    "private_ip": "10.0.1.100",
                    "public_ip": "54.123.45.67",
                    "launch_time_timestamp": 1734619942,
                },
                {
                    "instance_id": "i-2222222222222222",
                    "status": "pending",
                    "private_ip": "10.0.1.101",
                    "public_ip": None,
                    "launch_time_timestamp": 1734619943,
                },
            ],
        )

        result = scheduler_strategy.convert_domain_to_hostfactory_output(
            "getRequestStatus", mock_dto
        )

        assert len(result["requests"][0]["machines"]) == 2

        machine1 = result["requests"][0]["machines"][0]
        assert machine1["result"] == "succeed"  # running -> succeed

        machine2 = result["requests"][0]["machines"][1]
        assert machine2["result"] == "executing"  # pending -> executing
        assert machine2["publicIpAddress"] is None

    def test_status_mapping(self, scheduler_strategy):
        """Test domain status to HostFactory status mapping."""
        test_cases = [
            ("pending", "running"),
            ("in_progress", "running"),
            ("provisioning", "running"),
            ("completed", "complete"),
            ("partial", "complete_with_error"),
            ("failed", "complete_with_error"),
            ("cancelled", "complete_with_error"),
        ]

        for domain_status, expected_hf_status in test_cases:
            mock_dto = MockRequestDTO(status=domain_status)
            result = scheduler_strategy.convert_domain_to_hostfactory_output(
                "getRequestStatus", mock_dto
            )
            assert result["requests"][0]["status"] == expected_hf_status

    def test_machine_result_mapping(self, scheduler_strategy):
        """Test machine status to result mapping."""
        test_cases = [
            ("running", "succeed"),
            ("pending", "executing"),
            ("launching", "executing"),
            ("terminated", "fail"),
            ("failed", "fail"),
            ("error", "fail"),
            ("unknown", "executing"),  # Default case
        ]

        for machine_status, expected_result in test_cases:
            mock_dto = MockRequestDTO(
                machines=[
                    {
                        "instance_id": "i-test",
                        "status": machine_status,
                        "private_ip": "10.0.1.100",
                        "launch_time_timestamp": 0,
                    }
                ]
            )

            result = scheduler_strategy.convert_domain_to_hostfactory_output(
                "getRequestStatus", mock_dto
            )
            machine = result["requests"][0]["machines"][0]
            assert machine["result"] == expected_result

    def test_empty_machines_list(self, scheduler_strategy):
        """Test handling of empty machines list."""
        mock_dto = MockRequestDTO(machines=[])
        result = scheduler_strategy.convert_domain_to_hostfactory_output(
            "getRequestStatus", mock_dto
        )

        assert result["requests"][0]["machines"] == []
        assert result["requests"][0]["status"] == "complete"

    def test_dict_input_fallback(self, scheduler_strategy):
        """Test fallback handling for dict input instead of DTO."""
        dict_data = {
            "request_id": "dict-test-789",
            "status": "failed",
            "machines": [
                {
                    "instance_id": "i-dicttest",
                    "status": "terminated",
                    "private_ip": "10.0.1.200",
                    "launch_time_timestamp": 1234567890,
                }
            ],
        }

        result = scheduler_strategy.convert_domain_to_hostfactory_output(
            "getRequestStatus", dict_data
        )

        assert result["requests"][0]["requestId"] == "dict-test-789"
        assert result["requests"][0]["status"] == "complete_with_error"
        assert result["requests"][0]["machines"][0]["result"] == "fail"
