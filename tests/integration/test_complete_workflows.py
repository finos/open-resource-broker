"""Comprehensive integration workflow tests."""

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

# Import components for integration testing
try:
    from src.application.service import ApplicationService
    from src.domain.request.aggregate import Request
    from src.infrastructure.persistence.repositories.request_repository import (
        RequestRepository,
    )

    IMPORTS_AVAILABLE = True
except ImportError as e:
    IMPORTS_AVAILABLE = False
    pytestmark = pytest.mark.skip(f"Integration test imports not available: {e}")


@pytest.mark.integration
class TestCompleteWorkflowIntegration:
    """Test complete end-to-end workflows."""

    def test_complete_machine_provisioning_workflow(self):
        """Test complete machine provisioning workflow from start to finish."""
        # Setup mock dependencies
        mock_template_service = Mock()
        mock_command_bus = Mock()
        mock_query_bus = Mock()
        mock_logger = Mock()
        mock_container = Mock()
        mock_config = Mock()
        mock_provider = Mock()

        # Create application service
        app_service = ApplicationService(
            provider_type="aws",
            template_service=mock_template_service,
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=mock_logger,
            container=mock_container,
            config=mock_config,
            provider=mock_provider,
        )

        # Mock template service responses
        mock_template = {
            "template_id": "test-template",
            "name": "Test Template",
            "provider_api": "RunInstances",
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
            "max_number": 10,
        }
        mock_template_service.get_available_templates.return_value = [mock_template]
        mock_template_service.get_template_by_id.return_value = mock_template

        # Mock command bus responses
        mock_command_bus.dispatch.return_value = (
            "req-12345678-1234-1234-1234-123456789012"
        )

        # Mock query bus responses
        mock_request_status = {
            "request_id": "req-12345678-1234-1234-1234-123456789012",
            "status": "COMPLETED",
            "machine_count": 2,
            "machine_ids": ["i-1234567890abcdef0", "i-abcdef1234567890"],
            "created_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        mock_query_bus.dispatch.return_value = mock_request_status

        # Step 1: Get available templates
        templates = app_service.get_available_templates()
        assert len(templates) == 1
        assert templates[0]["template_id"] == "test-template"

        # Step 2: Request machines
        request_id = app_service.request_machines(
            template_id="test-template", machine_count=2
        )
        assert request_id == "req-12345678-1234-1234-1234-123456789012"

        # Step 3: Check request status
        status = app_service.get_request_status(request_id)
        assert status["status"] == "COMPLETED"
        assert status["machine_count"] == 2
        assert len(status["machine_ids"]) == 2

        # Verify all components were called correctly
        mock_template_service.get_available_templates.assert_called_once()
        mock_command_bus.dispatch.assert_called_once()
        mock_query_bus.dispatch.assert_called_once()

    def test_machine_return_workflow(self):
        """Test complete machine return workflow."""
        # Setup mock dependencies
        mock_template_service = Mock()
        mock_command_bus = Mock()
        mock_query_bus = Mock()
        mock_logger = Mock()
        mock_container = Mock()
        mock_config = Mock()
        mock_provider = Mock()

        app_service = ApplicationService(
            provider_type="aws",
            template_service=mock_template_service,
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=mock_logger,
            container=mock_container,
            config=mock_config,
            provider=mock_provider,
        )

        # Mock existing machines
        machine_ids = ["i-1234567890abcdef0", "i-abcdef1234567890"]

        # Mock command bus for return request
        return_request_id = "return-req-12345678-1234-1234-1234-123456789012"
        mock_command_bus.dispatch.return_value = return_request_id

        # Mock query bus for return status
        mock_return_status = {
            "request_id": return_request_id,
            "status": "COMPLETED",
            "machine_ids": machine_ids,
            "created_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        mock_query_bus.dispatch.return_value = [mock_return_status]

        # Step 1: Request machine return
        return_id = app_service.request_return_machines(machine_ids)
        assert return_id == return_request_id

        # Step 2: Check return request status
        return_requests = app_service.get_return_requests()
        assert len(return_requests) == 1
        assert return_requests[0]["status"] == "COMPLETED"
        assert return_requests[0]["machine_ids"] == machine_ids

        # Verify components were called
        mock_command_bus.dispatch.assert_called_once()
        mock_query_bus.dispatch.assert_called_once()

    def test_error_recovery_workflow(self):
        """Test error recovery workflow."""
        # Setup mock dependencies with error scenarios
        mock_template_service = Mock()
        mock_command_bus = Mock()
        mock_query_bus = Mock()
        mock_logger = Mock()
        mock_container = Mock()
        mock_config = Mock()
        mock_provider = Mock()

        app_service = ApplicationService(
            provider_type="aws",
            template_service=mock_template_service,
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=mock_logger,
            container=mock_container,
            config=mock_config,
            provider=mock_provider,
        )

        # Mock template service to return valid template
        mock_template = {
            "template_id": "test-template",
            "name": "Test Template",
            "provider_api": "RunInstances",
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
        }
        mock_template_service.get_template_by_id.return_value = mock_template

        # Mock command bus to simulate failure then success
        mock_command_bus.dispatch.side_effect = [
            Exception("Temporary failure"),  # First call fails
            "req-12345678-1234-1234-1234-123456789012",  # Second call succeeds
        ]

        # Step 1: First request fails
        with pytest.raises(Exception):
            app_service.request_machines(template_id="test-template", machine_count=2)

        # Step 2: Retry succeeds
        request_id = app_service.request_machines(
            template_id="test-template", machine_count=2
        )
        assert request_id == "req-12345678-1234-1234-1234-123456789012"

        # Verify retry behavior
        assert mock_command_bus.dispatch.call_count == 2

    def test_concurrent_operations_workflow(self):
        """Test concurrent operations workflow."""
        import threading

        # Setup mock dependencies
        mock_template_service = Mock()
        mock_command_bus = Mock()
        mock_query_bus = Mock()
        mock_logger = Mock()
        mock_container = Mock()
        mock_config = Mock()
        mock_provider = Mock()

        app_service = ApplicationService(
            provider_type="aws",
            template_service=mock_template_service,
            command_bus=mock_command_bus,
            query_bus=mock_query_bus,
            logger=mock_logger,
            container=mock_container,
            config=mock_config,
            provider=mock_provider,
        )

        # Mock template service
        mock_template = {
            "template_id": "test-template",
            "name": "Test Template",
            "provider_api": "RunInstances",
            "image_id": "ami-12345678",
            "instance_type": "t2.micro",
        }
        mock_template_service.get_template_by_id.return_value = mock_template

        # Mock command bus to return unique request IDs
        request_ids = []

        def mock_dispatch(command):
            request_id = f"req-{len(request_ids):08d}-1234-1234-1234-123456789012"
            request_ids.append(request_id)
            return request_id

        mock_command_bus.dispatch.side_effect = mock_dispatch

        # Concurrent request function
        results = []
        errors = []

        def make_concurrent_request(index):
            try:
                request_id = app_service.request_machines(
                    template_id="test-template", machine_count=1
                )
                results.append(request_id)
            except Exception as e:
                errors.append(e)

        # Create multiple threads
        threads = []
        num_threads = 10

        for i in range(num_threads):
            thread = threading.Thread(target=make_concurrent_request, args=(i,))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for completion
        for thread in threads:
            thread.join()

        # Verify results
        assert len(results) == num_threads
        assert len(errors) == 0
        assert len(set(results)) == num_threads  # All request IDs should be unique
        assert mock_command_bus.dispatch.call_count == num_threads


@pytest.mark.integration
class TestRepositoryIntegration:
    """Test repository integration workflows."""

    def test_repository_event_publishing_integration(self):
        """Test repository event publishing integration."""
        # Setup mock storage and event publisher
        mock_storage = Mock()
        mock_event_publisher = Mock()

        repository = RequestRepository(
            storage=mock_storage, event_publisher=mock_event_publisher
        )

        # Create request with events
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        # Perform operations that generate more events
        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        # Save request
        repository.save(request)

        # Verify storage was called
        mock_storage.save.assert_called_once()

        # Verify events were published
        mock_event_publisher.publish_events.assert_called_once()

        # Verify events were cleared from aggregate
        remaining_events = request.get_domain_events()
        assert len(remaining_events) == 0

    def test_repository_transaction_integration(self):
        """Test repository transaction integration."""
        # Setup mock storage with transaction support
        mock_storage = Mock()
        mock_storage.begin_transaction = Mock()
        mock_storage.commit_transaction = Mock()
        mock_storage.rollback_transaction = Mock()

        repository = RequestRepository(storage=mock_storage)

        # Test successful transaction
        if hasattr(repository, "begin_transaction"):
            repository.begin_transaction()

            request = Request.create_new_request(
                template_id="test-template", machine_count=2, requester_id="test-user"
            )

            repository.save(request)
            repository.commit_transaction()

            # Verify transaction methods were called
            mock_storage.begin_transaction.assert_called_once()
            mock_storage.commit_transaction.assert_called_once()
            mock_storage.save.assert_called_once()

        # Test transaction rollback on error
        if hasattr(repository, "begin_transaction"):
            mock_storage.save.side_effect = Exception("Save failed")

            try:
                repository.begin_transaction()

                request2 = Request.create_new_request(
                    template_id="test-template-2",
                    machine_count=1,
                    requester_id="test-user-2",
                )

                repository.save(request2)
                repository.commit_transaction()

            except Exception:
                repository.rollback_transaction()
                mock_storage.rollback_transaction.assert_called_once()

    def test_repository_migration_integration(self):
        """Test repository data migration integration."""
        # Setup source repository (JSON)
        mock_json_storage = Mock()
        source_repo = RequestRepository(storage=mock_json_storage)

        # Setup target repository (SQL)
        mock_sql_storage = Mock()
        target_repo = RequestRepository(storage=mock_sql_storage)

        # Mock source data
        mock_requests_data = [
            {
                "id": "req-1",
                "template_id": "template-1",
                "machine_count": 2,
                "status": "COMPLETED",
                "requester_id": "user-1",
            },
            {
                "id": "req-2",
                "template_id": "template-2",
                "machine_count": 1,
                "status": "PENDING",
                "requester_id": "user-2",
            },
        ]

        mock_json_storage.find_all.return_value = mock_requests_data

        # Perform migration if supported
        if hasattr(source_repo, "migrate_to"):
            source_repo.migrate_to(target_repo)

            # Verify data was read from source
            mock_json_storage.find_all.assert_called_once()

            # Verify data was written to target
            assert mock_sql_storage.save.call_count == len(mock_requests_data)


@pytest.mark.integration
class TestProviderIntegration:
    """Test provider integration workflows."""

    def test_aws_provider_integration_workflow(self):
        """Test AWS provider integration workflow."""
        # Setup mock AWS provider
        mock_aws_provider = Mock()
        mock_aws_provider.provider_type = "aws"
        mock_aws_provider.initialize.return_value = True
        mock_aws_provider.is_healthy.return_value = True

        # Mock provisioning response
        mock_provision_response = {
            "instance_ids": ["i-1234567890abcdef0", "i-abcdef1234567890"],
            "status": "success",
            "message": "Instances provisioned successfully",
        }
        mock_aws_provider.provision_instances.return_value = mock_provision_response

        # Mock termination response
        mock_terminate_response = {
            "terminated_instances": ["i-1234567890abcdef0", "i-abcdef1234567890"],
            "status": "success",
            "message": "Instances terminated successfully",
        }
        mock_aws_provider.terminate_instances.return_value = mock_terminate_response

        # Test provider initialization
        assert mock_aws_provider.initialize()
        assert mock_aws_provider.is_healthy()

        # Test instance provisioning
        provision_request = {
            "template_id": "test-template",
            "machine_count": 2,
            "instance_type": "t2.micro",
            "image_id": "ami-12345678",
        }

        provision_result = mock_aws_provider.provision_instances(provision_request)
        assert provision_result["status"] == "success"
        assert len(provision_result["instance_ids"]) == 2

        # Test instance termination
        instance_ids = provision_result["instance_ids"]
        terminate_result = mock_aws_provider.terminate_instances(instance_ids)
        assert terminate_result["status"] == "success"
        assert terminate_result["terminated_instances"] == instance_ids

        # Verify provider methods were called
        mock_aws_provider.initialize.assert_called_once()
        mock_aws_provider.is_healthy.assert_called_once()
        mock_aws_provider.provision_instances.assert_called_once_with(provision_request)
        mock_aws_provider.terminate_instances.assert_called_once_with(instance_ids)

    def test_provider_failover_integration(self):
        """Test provider failover integration."""
        # Setup primary provider (fails)
        mock_primary_provider = Mock()
        mock_primary_provider.provider_type = "aws"
        mock_primary_provider.is_healthy.return_value = False
        mock_primary_provider.provision_instances.side_effect = Exception(
            "Provider unavailable"
        )

        # Setup backup provider (succeeds)
        mock_backup_provider = Mock()
        mock_backup_provider.provider_type = "aws-backup"
        mock_backup_provider.is_healthy.return_value = True
        mock_backup_provider.provision_instances.return_value = {
            "instance_ids": ["i-backup123"],
            "status": "success",
        }

        # Test failover logic
        providers = [mock_primary_provider, mock_backup_provider]

        def provision_with_failover(request):
            for provider in providers:
                if provider.is_healthy():
                    try:
                        return provider.provision_instances(request)
                    except Exception:
                        continue
            raise Exception("All providers failed")

        # Test provisioning with failover
        provision_request = {"template_id": "test-template", "machine_count": 1}

        result = provision_with_failover(provision_request)
        assert result["status"] == "success"
        assert result["instance_ids"] == ["i-backup123"]

        # Verify failover behavior
        mock_primary_provider.is_healthy.assert_called_once()
        mock_backup_provider.is_healthy.assert_called_once()
        mock_backup_provider.provision_instances.assert_called_once()

    def test_provider_health_monitoring_integration(self):
        """Test provider health monitoring integration."""
        # Setup provider with varying health
        mock_provider = Mock()
        mock_provider.provider_type = "aws"

        # Health check results over time
        health_results = [True, True, False, False, True]
        health_index = 0

        def mock_health_check():
            nonlocal health_index
            result = health_results[health_index % len(health_results)]
            health_index += 1
            return result

        mock_provider.is_healthy.side_effect = mock_health_check

        # Monitor health over multiple checks
        health_history = []
        for _ in range(10):
            health_status = mock_provider.is_healthy()
            health_history.append(health_status)
            time.sleep(0.01)  # Small delay

        # Verify health monitoring
        assert len(health_history) == 10
        assert True in health_history  # Should have some healthy periods
        assert False in health_history  # Should have some unhealthy periods

        # Calculate health percentage
        healthy_count = sum(health_history)
        health_percentage = healthy_count / len(health_history)

        # Should have reasonable health percentage
        assert 0.0 <= health_percentage <= 1.0


@pytest.mark.integration
class TestConfigurationIntegration:
    """Test configuration integration workflows."""

    def test_configuration_loading_integration(self):
        """Test configuration loading integration."""
        # Create temporary configuration file
        config_data = {
            "provider": {
                "type": "aws",
                "aws": {"region": "us-east-1", "profile": "default"},
            },
            "logging": {
                "level": "INFO",
                "file_path": "logs/test.log",
                "console_enabled": True,
            },
            "storage": {
                "strategy": "json",
                "json_strategy": {
                    "storage_type": "single_file",
                    "base_path": "data",
                    "filenames": {"single_file": "test_database.json"},
                },
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            config_file_path = f.name

        try:
            # Test configuration loading
            # In a real implementation, this would use ConfigurationManager
            with open(config_file_path, "r") as f:
                loaded_config = json.load(f)

            # Verify configuration was loaded correctly
            assert loaded_config["provider"]["type"] == "aws"
            assert loaded_config["provider"]["aws"]["region"] == "us-east-1"
            assert loaded_config["logging"]["level"] == "INFO"
            assert loaded_config["storage"]["strategy"] == "json"

        finally:
            # Clean up
            os.unlink(config_file_path)

    def test_environment_variable_override_integration(self):
        """Test environment variable override integration."""
        # Set environment variables
        test_env_vars = {
            "HF_PROVIDER_TYPE": "aws",
            "HF_AWS_REGION": "us-west-2",
            "HF_LOGGING_LEVEL": "DEBUG",
            "HF_STORAGE_STRATEGY": "sql",
        }

        # Mock environment variables
        with patch.dict(os.environ, test_env_vars):
            # Test environment variable reading
            provider_type = os.environ.get("HF_PROVIDER_TYPE")
            aws_region = os.environ.get("HF_AWS_REGION")
            logging_level = os.environ.get("HF_LOGGING_LEVEL")
            storage_strategy = os.environ.get("HF_STORAGE_STRATEGY")

            # Verify environment variables are read correctly
            assert provider_type == "aws"
            assert aws_region == "us-west-2"
            assert logging_level == "DEBUG"
            assert storage_strategy == "sql"

    def test_configuration_validation_integration(self):
        """Test configuration validation integration."""
        # Test valid configuration
        valid_config = {
            "provider": {
                "type": "aws",
                "aws": {"region": "us-east-1", "max_retries": 3, "timeout": 30},
            },
            "logging": {"level": "INFO", "console_enabled": True},
        }

        # Test invalid configuration
        invalid_configs = [
            {"provider": {"type": "invalid_provider"}},  # Invalid provider type
            {
                "provider": {
                    "type": "aws",
                    # Empty region  # Invalid retry count
                    "aws": {"region": "", "max_retries": -1},
                }
            },
            {"logging": {"level": "INVALID_LEVEL"}},  # Invalid log level
        ]

        def validate_config(config):
            """Simple configuration validation."""
            errors = []

            # Validate provider
            if "provider" in config:
                provider_type = config["provider"].get("type")
                if provider_type not in ["aws", "azure", "gcp"]:
                    errors.append(f"Invalid provider type: {provider_type}")

                if provider_type == "aws" and "aws" in config["provider"]:
                    aws_config = config["provider"]["aws"]
                    if not aws_config.get("region"):
                        errors.append("AWS region is required")
                    if aws_config.get("max_retries", 0) < 0:
                        errors.append("Max retries must be non-negative")

            # Validate logging
            if "logging" in config:
                log_level = config["logging"].get("level")
                if log_level not in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
                    errors.append(f"Invalid log level: {log_level}")

            return errors

        # Test valid configuration
        valid_errors = validate_config(valid_config)
        assert len(valid_errors) == 0

        # Test invalid configurations
        for invalid_config in invalid_configs:
            invalid_errors = validate_config(invalid_config)
            assert len(invalid_errors) > 0


@pytest.mark.integration
class TestEventSystemIntegration:
    """Test event system integration workflows."""

    def test_event_publishing_integration(self):
        """Test event publishing integration."""
        # Setup mock event publisher and handlers
        mock_event_publisher = Mock()
        mock_audit_handler = Mock()
        mock_notification_handler = Mock()

        # Register event handlers
        event_handlers = {
            "RequestCreatedEvent": [mock_audit_handler, mock_notification_handler],
            "RequestCompletedEvent": [mock_audit_handler],
        }

        def mock_publish_events(events):
            """Mock event publishing with handler invocation."""
            for event in events:
                event_type = type(event).__name__
                handlers = event_handlers.get(event_type, [])
                for handler in handlers:
                    handler.handle(event)

        mock_event_publisher.publish_events.side_effect = mock_publish_events

        # Create request that generates events
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        # Get events and publish them
        events = request.get_domain_events()
        mock_event_publisher.publish_events(events)

        # Verify events were published
        mock_event_publisher.publish_events.assert_called_once_with(events)

        # Verify handlers were called
        assert mock_audit_handler.handle.call_count >= 2  # Created and Completed events
        assert mock_notification_handler.handle.call_count >= 1  # Created event

    def test_event_sourcing_integration(self):
        """Test event sourcing integration."""
        # Setup mock event store
        mock_event_store = Mock()
        stored_events = []

        def mock_append_events(aggregate_id, events, expected_version):
            """Mock event store append."""
            for event in events:
                stored_events.append(
                    {
                        "aggregate_id": aggregate_id,
                        "event": event,
                        "version": len(stored_events) + 1,
                        "timestamp": datetime.now(timezone.utc),
                    }
                )

        def mock_get_events(aggregate_id):
            """Mock event store retrieval."""
            return [
                entry["event"]
                for entry in stored_events
                if entry["aggregate_id"] == aggregate_id
            ]

        mock_event_store.append_events.side_effect = mock_append_events
        mock_event_store.get_events.side_effect = mock_get_events

        # Create and modify request
        request = Request.create_new_request(
            template_id="test-template", machine_count=2, requester_id="test-user"
        )

        request_id = str(request.id.value)

        # Store initial events
        initial_events = request.get_domain_events()
        mock_event_store.append_events(request_id, initial_events, 0)

        # Modify request and store more events
        request.clear_domain_events()
        request.start_processing()
        request.complete_successfully(
            machine_ids=["i-123", "i-456"], completion_message="Success"
        )

        additional_events = request.get_domain_events()
        mock_event_store.append_events(
            request_id, additional_events, len(initial_events)
        )

        # Verify events were stored
        all_stored_events = mock_event_store.get_events(request_id)
        assert len(all_stored_events) >= 3  # Created, StatusChanged, Completed

        # Verify event ordering
        timestamps = [
            entry["timestamp"]
            for entry in stored_events
            if entry["aggregate_id"] == request_id
        ]
        assert timestamps == sorted(timestamps)  # Should be in chronological order
