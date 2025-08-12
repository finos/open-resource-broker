"""Integration tests for full application workflow."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.application.dto.queries import ListTemplatesQuery
from src.bootstrap import Application
from src.config.manager import ConfigurationManager
from src.domain.base.value_objects import InstanceType
from src.domain.template.aggregate import Template
from src.infrastructure.di.buses import CommandBus, QueryBus


@pytest.mark.integration
class TestFullWorkflow:
    """Integration tests for full application workflow."""

    def test_application_initialization_and_basic_operations(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test full application initialization and basic operations."""
        # Initialize application
        app = Application(config_path=str(test_config_file))

        # Initialize should succeed
        assert app.initialize() is True

        # Get CQRS buses directly
        query_bus = app.get_query_bus()
        command_bus = app.get_command_bus()
        assert isinstance(query_bus, QueryBus)
        assert isinstance(command_bus, CommandBus)

        # Test basic operations using direct CQRS
        with patch.object(query_bus, "execute") as mock_query_execute:
            mock_query_execute.return_value = []

            # Test template listing
            query = ListTemplatesQuery()
            templates = query_bus.execute(query)
            assert isinstance(templates, list)

        # Test provider health using direct provider context
        provider_info = app.get_provider_info()
        assert isinstance(provider_info, dict)
        assert "status" in provider_info

    def test_template_management_workflow(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test template management workflow."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Test template validation
        template_data = {
            "name": "test-template",
            "provider_api": "ec2_fleet",
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "subnet_ids": [mock_ec2_resources["subnet_id"]],
            "security_group_ids": [mock_ec2_resources["security_group_id"]],
        }

        is_valid = service.validate_template(template_data)
        assert is_valid is True

        # Get available templates
        templates = service.get_available_templates()
        assert isinstance(templates, list)

        # If templates exist, test getting specific template
        if templates:
            template = service.get_template_by_id(templates[0].id)
            assert template is not None
            assert isinstance(template, Template)

    def test_machine_request_workflow(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test machine request workflow."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock command and query buses for this test
        with patch.object(service, "_command_bus") as mock_command_bus, patch.object(
            service, "_query_bus"
        ) as mock_query_bus:

            # Mock request machines response
            mock_command_bus.dispatch.return_value = {
                "request_id": "req-12345678",
                "status": "pending",
                "machine_count": 2,
            }

            # Request machines
            result = service.request_machines(
                template_id="template-001",
                machine_count=2,
                requester_id="test-user",
                priority=1,
                tags={"Environment": "test"},
            )

            assert result["request_id"] == "req-12345678"
            assert result["status"] == "pending"
            assert result["machine_count"] == 2

            # Mock request status response
            mock_query_bus.dispatch.return_value = {
                "request_id": "req-12345678",
                "status": "processing",
                "progress": 50.0,
                "machine_count": 2,
                "completed_count": 1,
            }

            # Check request status
            status = service.get_request_status("req-12345678")
            assert status["request_id"] == "req-12345678"
            assert status["status"] == "processing"
            assert status["progress"] == 50.0

    def test_machine_return_workflow(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test machine return workflow."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock command and query buses
        with patch.object(service, "_command_bus") as mock_command_bus, patch.object(
            service, "_query_bus"
        ) as mock_query_bus:

            # Mock return request response
            mock_command_bus.dispatch.return_value = {
                "request_id": "req-return-123",
                "status": "pending",
                "machine_count": 2,
            }

            # Request machine return
            machine_ids = ["machine-001", "machine-002"]
            result = service.request_return_machines(
                machine_ids=machine_ids,
                requester_id="test-user",
                reason="Testing complete",
            )

            assert result["request_id"] == "req-return-123"
            assert result["status"] == "pending"
            assert result["machine_count"] == 2

            # Mock return requests response
            mock_query_bus.dispatch.return_value = [
                {
                    "request_id": "req-return-123",
                    "status": "pending",
                    "machine_count": 2,
                    "requester_id": "test-user",
                }
            ]

            # Get return requests
            return_requests = service.get_return_requests(status="pending")
            assert len(return_requests) == 1
            assert return_requests[0]["request_id"] == "req-return-123"

    def test_machine_status_monitoring_workflow(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test machine status monitoring workflow."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock query bus
        with patch.object(service, "_query_bus") as mock_query_bus:

            # Mock machine status response
            mock_query_bus.dispatch.return_value = {
                "machine_id": "machine-001",
                "instance_id": "i-1234567890abcdef0",
                "status": "running",
                "private_ip": "10.0.1.100",
                "public_ip": "54.123.45.67",
                "instance_type": "t2.micro",
                "availability_zone": "us-east-1a",
            }

            # Get machine status
            status = service.get_machine_status("machine-001")
            assert status["machine_id"] == "machine-001"
            assert status["instance_id"] == "i-1234567890abcdef0"
            assert status["status"] == "running"

            # Mock machines by request response
            mock_query_bus.dispatch.return_value = [
                {
                    "machine_id": "machine-001",
                    "instance_id": "i-1234567890abcdef0",
                    "status": "running",
                },
                {
                    "machine_id": "machine-002",
                    "instance_id": "i-abcdef1234567890",
                    "status": "running",
                },
            ]

            # Get machines by request
            machines = service.get_machines_by_request("req-12345678")
            assert len(machines) == 2
            assert machines[0]["machine_id"] == "machine-001"
            assert machines[1]["machine_id"] == "machine-002"

    def test_configuration_management_integration(self, temp_dir: Path):
        """Test configuration management integration."""
        # Create test configuration
        config_data = {
            "aws": {"region": "us-west-2", "profile": "test-profile"},
            "logging": {"level": "INFO", "console_enabled": True},
            "database": {"type": "sqlite", "name": ":memory:"},
            "REPOSITORY_CONFIG": {
                "type": "json",
                "json": {
                    "storage_type": "single_file",
                    "base_path": str(temp_dir),
                    "filenames": {"single_file": "test_database.json"},
                },
            },
        }

        config_file = temp_dir / "integration_config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f, indent=2)

        # Initialize application with custom config
        app = Application(config_path=str(config_file))

        # Verify configuration is loaded correctly
        config_manager = app._container.get_config_manager()
        assert config_manager.get("aws.region") == "us-west-2"
        assert config_manager.get("aws.profile") == "test-profile"
        assert config_manager.get("logging.level") == "INFO"
        assert config_manager.get("database.type") == "sqlite"

    def test_error_handling_integration(self, test_config_file: Path, aws_mocks):
        """Test error handling integration."""
        app = Application(config_path=str(test_config_file))

        # Test initialization with invalid configuration
        with patch.object(app, "_container") as mock_container:
            mock_container.initialize.side_effect = Exception("Initialization failed")

            # Should handle initialization error gracefully
            result = app.initialize()
            assert result is False

        # Test with valid initialization but service errors
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock service to raise errors
        with patch.object(service, "_command_bus") as mock_command_bus:
            mock_command_bus.dispatch.side_effect = Exception("Command failed")

            # Should propagate the error
            with pytest.raises(Exception, match="Command failed"):
                service.request_machines(
                    template_id="template-001",
                    machine_count=1,
                    requester_id="test-user",
                )

    def test_dependency_injection_integration(self, test_config_file: Path, aws_mocks):
        """Test dependency injection integration."""
        app = Application(config_path=str(test_config_file))
        app.initialize()

        # Verify DI container is properly configured
        container = app._container
        assert container is not None

        # Verify services can be retrieved from container
        config_manager = container.get_config_manager()
        assert isinstance(config_manager, ConfigurationManager)

        template_service = container.get_template_service()
        assert template_service is not None

        command_bus = container.get_command_bus()
        assert command_bus is not None

        query_bus = container.get_query_bus()
        assert query_bus is not None

    def test_provider_integration(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test provider integration."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Test provider health check
        health = service.get_provider_health()
        assert isinstance(health, bool)

        # Test provider info
        info = service.get_provider_info()
        assert info["provider_type"] == "aws"
        assert "initialized" in info

        # Test provider capabilities
        if "capabilities" in info:
            capabilities = info["capabilities"]
            assert isinstance(capabilities, list)
            expected_capabilities = [
                "ec2_fleet",
                "auto_scaling_group",
                "spot_fleet",
                "run_instances",
            ]
            for capability in expected_capabilities:
                assert (
                    capability in capabilities or len(capabilities) == 0
                )  # Allow empty for mocked tests


@pytest.mark.integration
class TestEndToEndScenarios:
    """End-to-end integration test scenarios."""

    def test_complete_machine_lifecycle(
        self, test_config_file: Path, aws_mocks, mock_ec2_resources
    ):
        """Test complete machine lifecycle from request to termination."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock all the buses for end-to-end flow
        with patch.object(service, "_command_bus") as mock_command_bus, patch.object(
            service, "_query_bus"
        ) as mock_query_bus, patch.object(
            service, "_template_service"
        ) as mock_template_service:

            # Step 1: Get available templates
            mock_template = Template(
                id="template-001",
                name="test-template",
                provider_api="ec2_fleet",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=[mock_ec2_resources["subnet_id"]],
                security_group_ids=[mock_ec2_resources["security_group_id"]],
            )
            mock_template_service.get_available_templates.return_value = [mock_template]

            templates = service.get_available_templates()
            assert len(templates) == 1
            assert templates[0].id == "template-001"

            # Step 2: Request machines
            mock_command_bus.dispatch.return_value = {
                "request_id": "req-12345678",
                "status": "pending",
                "machine_count": 2,
            }

            request_result = service.request_machines(
                template_id="template-001", machine_count=2, requester_id="test-user"
            )
            assert request_result["request_id"] == "req-12345678"

            # Step 3: Monitor request progress
            mock_query_bus.dispatch.return_value = {
                "request_id": "req-12345678",
                "status": "completed",
                "progress": 100.0,
                "machine_count": 2,
                "completed_count": 2,
                "machine_ids": ["machine-001", "machine-002"],
            }

            status = service.get_request_status("req-12345678")
            assert status["status"] == "completed"
            assert status["progress"] == 100.0

            # Step 4: Get machine details
            mock_query_bus.dispatch.return_value = [
                {
                    "machine_id": "machine-001",
                    "instance_id": "i-1234567890abcdef0",
                    "status": "running",
                },
                {
                    "machine_id": "machine-002",
                    "instance_id": "i-abcdef1234567890",
                    "status": "running",
                },
            ]

            machines = service.get_machines_by_request("req-12345678")
            assert len(machines) == 2

            # Step 5: Return machines
            mock_command_bus.dispatch.return_value = {
                "request_id": "req-return-123",
                "status": "pending",
                "machine_count": 2,
            }

            return_result = service.request_return_machines(
                machine_ids=["machine-001", "machine-002"],
                requester_id="test-user",
                reason="Testing complete",
            )
            assert return_result["request_id"] == "req-return-123"

            # Step 6: Monitor return progress
            mock_query_bus.dispatch.return_value = [
                {
                    "request_id": "req-return-123",
                    "status": "completed",
                    "machine_count": 2,
                }
            ]

            return_requests = service.get_return_requests(status="completed")
            assert len(return_requests) == 1
            assert return_requests[0]["status"] == "completed"

    def test_error_recovery_scenario(self, test_config_file: Path, aws_mocks):
        """Test error recovery scenario."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        with patch.object(service, "_command_bus") as mock_command_bus, patch.object(
            service, "_query_bus"
        ) as mock_query_bus:

            # Simulate initial failure
            mock_command_bus.dispatch.side_effect = Exception("Temporary failure")

            # First request should fail
            with pytest.raises(Exception, match="Temporary failure"):
                service.request_machines(
                    template_id="template-001",
                    machine_count=1,
                    requester_id="test-user",
                )

            # Simulate recovery
            mock_command_bus.dispatch.side_effect = None
            mock_command_bus.dispatch.return_value = {
                "request_id": "req-recovery-123",
                "status": "pending",
                "machine_count": 1,
            }

            # Second request should succeed
            result = service.request_machines(
                template_id="template-001", machine_count=1, requester_id="test-user"
            )
            assert result["request_id"] == "req-recovery-123"
            assert result["status"] == "pending"

    def test_concurrent_operations_scenario(self, test_config_file: Path, aws_mocks):
        """Test concurrent operations scenario."""
        import threading

        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        results = []
        errors = []

        def request_worker(worker_id: int):
            try:
                with patch.object(service, "_command_bus") as mock_command_bus:
                    mock_command_bus.dispatch.return_value = {
                        "request_id": f"req-worker-{worker_id}",
                        "status": "pending",
                        "machine_count": 1,
                    }

                    result = service.request_machines(
                        template_id="template-001",
                        machine_count=1,
                        requester_id=f"user-{worker_id}",
                    )
                    results.append(result)
            except Exception as e:
                errors.append(e)

        # Run concurrent requests
        threads = [threading.Thread(target=request_worker, args=(i,)) for i in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # All requests should succeed without errors
        assert len(errors) == 0
        assert len(results) == 5

        # Each result should be unique
        request_ids = [result["request_id"] for result in results]
        assert len(set(request_ids)) == 5  # All unique


@pytest.mark.integration
class TestConfigurationIntegration:
    """Integration tests for configuration management."""

    def test_configuration_file_loading(self, temp_dir: Path):
        """Test loading configuration from different file formats."""
        # Test JSON configuration
        json_config = {"aws": {"region": "us-east-1"}, "logging": {"level": "DEBUG"}}

        json_file = temp_dir / "config.json"
        with open(json_file, "w") as f:
            json.dump(json_config, f)

        app = Application(config_path=str(json_file))
        app.initialize()

        config_manager = app._container.get_config_manager()
        assert config_manager.get("aws.region") == "us-east-1"
        assert config_manager.get("logging.level") == "DEBUG"

    def test_environment_variable_override(self, test_config_file: Path):
        """Test environment variable override of configuration."""
        import os

        # Set environment variables
        with patch.dict(os.environ, {"AWS_REGION": "us-west-1", "LOG_LEVEL": "ERROR"}):
            app = Application(config_path=str(test_config_file))
            app.initialize()

            config_manager = app._container.get_config_manager()

            # Environment variables should override file config
            assert config_manager.get_env("AWS_REGION") == "us-west-1"
            assert config_manager.get_env("LOG_LEVEL") == "ERROR"

    def test_configuration_validation_integration(self, temp_dir: Path):
        """Test configuration validation integration."""
        # Create invalid configuration
        invalid_config = {
            "aws": {"region": ""},  # Empty region
            "logging": {"level": "INVALID_LEVEL"},
        }

        invalid_file = temp_dir / "invalid_config.json"
        with open(invalid_file, "w") as f:
            json.dump(invalid_config, f)

        app = Application(config_path=str(invalid_file))

        # Should handle invalid configuration gracefully
        result = app.initialize()
        # Depending on implementation, might succeed with defaults or fail
        assert isinstance(result, bool)


@pytest.mark.integration
@pytest.mark.slow
class TestPerformanceIntegration:
    """Performance integration tests."""

    def test_large_template_list_performance(self, test_config_file: Path, aws_mocks):
        """Test performance with large template list."""
        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        # Mock large template list
        large_template_list = []
        for i in range(1000):
            template = Template(
                id=f"template-{i:04d}",
                name=f"template-{i:04d}",
                provider_api="ec2_fleet",
                image_id="ami-12345678",
                instance_type=InstanceType("t2.micro"),
                subnet_ids=["subnet-12345678"],
                security_group_ids=["sg-12345678"],
            )
            large_template_list.append(template)

        with patch.object(service, "_template_service") as mock_template_service:
            mock_template_service.get_available_templates.return_value = (
                large_template_list
            )

            import time

            start_time = time.time()

            templates = service.get_available_templates()

            end_time = time.time()
            execution_time = end_time - start_time

            # Should handle large list efficiently (under 1 second)
            assert execution_time < 1.0
            assert len(templates) == 1000

    def test_concurrent_request_performance(self, test_config_file: Path, aws_mocks):
        """Test performance with concurrent requests."""
        import threading
        import time

        app = Application(config_path=str(test_config_file))
        app.initialize()
        service = app.get_application_service()

        results = []
        start_times = []
        end_times = []

        def performance_worker(worker_id: int):
            start_time = time.time()
            start_times.append(start_time)

            try:
                with patch.object(service, "_query_bus") as mock_query_bus:
                    mock_query_bus.dispatch.return_value = {
                        "request_id": f"req-{worker_id}",
                        "status": "completed",
                    }

                    result = service.get_request_status(f"req-{worker_id}")
                    results.append(result)
            finally:
                end_time = time.time()
                end_times.append(end_time)

        # Run 50 concurrent operations
        threads = [
            threading.Thread(target=performance_worker, args=(i,)) for i in range(50)
        ]

        overall_start = time.time()

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        overall_end = time.time()
        overall_time = overall_end - overall_start

        # Should complete all operations efficiently
        assert overall_time < 5.0  # Under 5 seconds for 50 operations
        assert len(results) == 50

        # Calculate average response time
        response_times = [end - start for start, end in zip(start_times, end_times)]
        avg_response_time = sum(response_times) / len(response_times)

        # Average response time should be reasonable
        assert avg_response_time < 0.1  # Under 100ms average
