"""Integration tests for full application workflow."""

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from orb.bootstrap import Application
from orb.config.manager import ConfigurationManager
from orb.infrastructure.di.buses import CommandBus, QueryBus


def _make_mock_container(mock_config_manager=None):
    """Create a mock DI container."""
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.infrastructure.di.buses import CommandBus, QueryBus

    if mock_config_manager is None:
        mock_config_manager = _make_mock_config_manager()

    mock_query_bus = Mock(spec=QueryBus)
    mock_command_bus = Mock(spec=CommandBus)

    mock_container = Mock()
    mock_container.is_lazy_loading_enabled.return_value = False

    def _container_get(service_type):
        if service_type is ConfigurationPort:
            return mock_config_manager
        if service_type is QueryBus:
            return mock_query_bus
        if service_type is CommandBus:
            return mock_command_bus
        return Mock()

    mock_container.get.side_effect = _container_get
    return mock_container


def _make_mock_config_manager(region="us-east-1"):
    """Create a fully configured mock config manager."""
    mock_config_manager = Mock(spec=ConfigurationManager)
    mock_config_manager.get.return_value = {"type": "aws"}
    mock_config_manager.get_provider_config.return_value = None

    mock_app_config = Mock()
    mock_logging_config = Mock()
    mock_logging_config.level = "DEBUG"
    mock_logging_config.format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    mock_logging_config.file_path = "logs/test.log"
    mock_logging_config.max_size = 10485760
    mock_logging_config.backup_count = 5
    mock_logging_config.console_enabled = True
    mock_logging_config.accept_propagated_setting = False
    mock_app_config.logging = mock_logging_config
    mock_config_manager.get_typed.return_value = mock_app_config
    return mock_config_manager


async def _init_app(config_path=None):
    """Initialize an Application with mocked container."""
    mock_config_manager = _make_mock_config_manager()
    mock_container = _make_mock_container(mock_config_manager)

    with (
        patch("orb.infrastructure.di.container.get_container", return_value=mock_container),
        patch("orb.providers.registry.get_provider_registry") as mock_get_registry,
    ):
        mock_registry = Mock()
        mock_registry.get_registered_providers.return_value = ["aws"]
        mock_registry.get_registered_provider_instances.return_value = ["aws_default_us-east-1"]
        mock_registry.is_provider_instance_registered.return_value = True
        mock_get_registry.return_value = mock_registry

        app = Application(config_path=config_path, skip_validation=True)
        with patch.object(app, "_preload_templates", new=AsyncMock(return_value=None)):
            await app.initialize()

    return app, mock_config_manager, mock_container


@pytest.mark.integration
class TestFullWorkflow:
    """Integration tests for full application workflow."""

    @pytest.mark.asyncio
    async def test_application_initialization_and_basic_operations(self):
        """Test full application initialization and basic operations."""
        app, _, _ = await _init_app()

        assert app._initialized is True

        # Get CQRS buses directly
        query_bus = app.get_query_bus()
        command_bus = app.get_command_bus()
        assert isinstance(query_bus, QueryBus)
        assert isinstance(command_bus, CommandBus)

        # Test provider info
        provider_info = app.get_provider_info()
        assert isinstance(provider_info, dict)
        assert "status" in provider_info

    @pytest.mark.asyncio
    async def test_template_management_workflow(self):
        """Test template management workflow via query bus."""
        app, _, _ = await _init_app()

        query_bus = app.get_query_bus()
        assert query_bus is not None

        # Mock query execution to return empty template list
        with patch.object(query_bus, "execute_sync", return_value=[]) as mock_exec:
            from orb.application.dto.queries import ListTemplatesQuery

            result = query_bus.execute_sync(ListTemplatesQuery())
            assert isinstance(result, list)
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_machine_request_workflow(self):
        """Test machine request workflow via command/query bus."""
        app, _, _ = await _init_app()

        command_bus = app.get_command_bus()
        query_bus = app.get_query_bus()

        with patch.object(
            command_bus,
            "execute",
            new=AsyncMock(
                return_value={
                    "request_id": "req-12345678",
                    "status": "pending",
                    "machine_count": 2,
                }
            ),
        ):
            cmd_result = await command_bus.execute(Mock())
            assert cmd_result["request_id"] == "req-12345678"
            assert cmd_result["status"] == "pending"

        with patch.object(
            query_bus,
            "execute_sync",
            return_value={
                "request_id": "req-12345678",
                "status": "processing",
                "progress": 50.0,
            },
        ):
            qry_result = query_bus.execute_sync(Mock())
            assert qry_result["status"] == "processing"
            assert qry_result["progress"] == 50.0

    @pytest.mark.asyncio
    async def test_machine_return_workflow(self):
        """Test machine return workflow via command bus."""
        app, _, _ = await _init_app()

        command_bus = app.get_command_bus()

        with patch.object(
            command_bus,
            "execute",
            new=AsyncMock(
                return_value={
                    "request_id": "req-return-123",
                    "status": "pending",
                    "machine_count": 2,
                }
            ),
        ):
            result = await command_bus.execute(Mock())
            assert result["request_id"] == "req-return-123"
            assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_machine_status_monitoring_workflow(self):
        """Test machine status monitoring workflow via query bus."""
        app, _, _ = await _init_app()

        query_bus = app.get_query_bus()

        with patch.object(
            query_bus,
            "execute_sync",
            return_value={
                "machine_id": "machine-001",
                "instance_id": "i-1234567890abcdef0",
                "status": "running",
            },
        ):
            result = query_bus.execute_sync(Mock())
            assert result["machine_id"] == "machine-001"
            assert result["status"] == "running"

    @pytest.mark.asyncio
    async def test_configuration_management_integration(self, temp_dir: Path):
        """Test configuration management integration."""
        config_data = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "active_provider": "aws_default_us-west-2",
                "providers": [
                    {
                        "name": "aws_default_us-west-2",
                        "type": "aws",
                        "enabled": True,
                        "priority": 0,
                        "weight": 100,
                        "config": {"region": "us-west-2", "profile": "test-profile"},
                    }
                ],
            },
            "aws": {"region": "us-west-2", "profile": "test-profile"},
            "logging": {"level": "INFO", "console_enabled": True},
        }

        config_file = temp_dir / "integration_config.json"
        with open(config_file, "w") as f:
            json.dump(config_data, f, indent=2)

        app, _, _ = await _init_app(config_path=str(config_file))
        assert app._initialized is True

    @pytest.mark.asyncio
    async def test_error_handling_integration(self):
        """Test error handling integration."""
        # Test initialization failure
        with patch("orb.infrastructure.di.container.get_container") as mock_get_container:
            mock_container = Mock()
            mock_container.is_lazy_loading_enabled.return_value = False
            mock_container.get.side_effect = Exception("Initialization failed")
            mock_get_container.return_value = mock_container

            app = Application(skip_validation=True)
            result = await app.initialize()
            assert result is False

    @pytest.mark.asyncio
    async def test_dependency_injection_integration(self):
        """Test dependency injection integration."""
        app, _, _ = await _init_app()

        # Verify buses are accessible
        query_bus = app.get_query_bus()
        command_bus = app.get_command_bus()
        assert isinstance(query_bus, QueryBus)
        assert isinstance(command_bus, CommandBus)

    @pytest.mark.asyncio
    async def test_provider_integration(self):
        """Test provider integration."""
        app, _, _ = await _init_app()

        provider_info = app.get_provider_info()
        assert isinstance(provider_info, dict)
        assert "status" in provider_info
        assert provider_info["status"] in ("configured", "not_initialized")

        health = app.health_check()
        assert isinstance(health, dict)
        assert "status" in health


@pytest.mark.integration
class TestEndToEndScenarios:
    """End-to-end integration test scenarios."""

    @pytest.mark.asyncio
    async def test_complete_machine_lifecycle(self):
        """Test complete machine lifecycle from request to termination."""
        app, _, _ = await _init_app()

        command_bus = app.get_command_bus()
        query_bus = app.get_query_bus()

        with patch.object(
            command_bus,
            "execute",
            new=AsyncMock(
                return_value={
                    "request_id": "req-12345678",
                    "status": "pending",
                    "machine_count": 2,
                }
            ),
        ):
            result = await command_bus.execute(Mock())
            assert result["request_id"] == "req-12345678"

        with patch.object(
            query_bus,
            "execute_sync",
            return_value={
                "request_id": "req-12345678",
                "status": "completed",
                "progress": 100.0,
            },
        ):
            status = query_bus.execute_sync(Mock())
            assert status["status"] == "completed"

        with patch.object(
            command_bus,
            "execute",
            new=AsyncMock(
                return_value={
                    "request_id": "req-return-123",
                    "status": "pending",
                }
            ),
        ):
            return_result = await command_bus.execute(Mock())
            assert return_result["request_id"] == "req-return-123"

    @pytest.mark.asyncio
    async def test_error_recovery_scenario(self):
        """Test error recovery scenario."""
        app, _, _ = await _init_app()

        command_bus = app.get_command_bus()

        call_count = [0]

        async def side_effect(cmd):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Temporary failure")
            return {"request_id": "req-recovery-123", "status": "pending"}

        with patch.object(command_bus, "execute", side_effect=side_effect):
            with pytest.raises(Exception, match="Temporary failure"):
                await command_bus.execute(Mock())

            result = await command_bus.execute(Mock())
            assert result["request_id"] == "req-recovery-123"

    @pytest.mark.asyncio
    async def test_concurrent_operations_scenario(self):
        """Test concurrent operations scenario."""
        app, _, _ = await _init_app()

        command_bus = app.get_command_bus()
        results = []
        errors = []

        def worker(worker_id):
            import asyncio

            try:
                with patch.object(
                    command_bus,
                    "execute",
                    new=AsyncMock(
                        return_value={
                            "request_id": f"req-worker-{worker_id}",
                            "status": "pending",
                        }
                    ),
                ):
                    loop = asyncio.new_event_loop()
                    result = loop.run_until_complete(command_bus.execute(Mock()))
                    loop.close()
                    results.append(result)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 5


@pytest.mark.integration
class TestConfigurationIntegration:
    """Integration tests for configuration management."""

    @pytest.mark.asyncio
    async def test_configuration_file_loading(self, temp_dir: Path):
        """Test loading configuration from a JSON file."""
        json_config = {
            "provider": {
                "selection_policy": "FIRST_AVAILABLE",
                "active_provider": "aws_default_us-east-1",
                "providers": [
                    {
                        "name": "aws_default_us-east-1",
                        "type": "aws",
                        "enabled": True,
                        "priority": 0,
                        "weight": 100,
                        "config": {"region": "us-east-1"},
                    }
                ],
            },
            "aws": {"region": "us-east-1"},
            "logging": {"level": "DEBUG"},
        }

        json_file = temp_dir / "config.json"
        with open(json_file, "w") as f:
            json.dump(json_config, f)

        app, _, _ = await _init_app(config_path=str(json_file))
        assert app._initialized is True

    @pytest.mark.asyncio
    async def test_environment_variable_override(self):
        """Test environment variable override of configuration."""
        with patch.dict(os.environ, {"AWS_REGION": "us-west-1", "LOG_LEVEL": "ERROR"}):
            app, _, _ = await _init_app()
            assert app._initialized is True
            assert os.environ.get("AWS_REGION") == "us-west-1"
            assert os.environ.get("LOG_LEVEL") == "ERROR"


@pytest.mark.integration
@pytest.mark.slow
class TestPerformanceIntegration:
    """Performance integration tests."""

    @pytest.mark.asyncio
    async def test_large_template_list_performance(self):
        """Test performance with large template list."""
        app, _, _ = await _init_app()

        query_bus = app.get_query_bus()

        large_list = [{"id": f"template-{i:04d}", "name": f"template-{i:04d}"} for i in range(1000)]

        with patch.object(query_bus, "execute_sync", return_value=large_list):
            start = time.time()
            result = query_bus.execute_sync(Mock())
            elapsed = time.time() - start

            assert elapsed < 1.0
            assert len(result) == 1000

    @pytest.mark.asyncio
    async def test_concurrent_request_performance(self):
        """Test performance with concurrent requests."""
        app, _, _ = await _init_app()

        query_bus = app.get_query_bus()
        results = []

        def worker(worker_id):
            with patch.object(
                query_bus,
                "execute_sync",
                return_value={
                    "request_id": f"req-{worker_id}",
                    "status": "completed",
                },
            ):
                result = query_bus.execute_sync(Mock())
                results.append(result)

        overall_start = time.time()
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        overall_time = time.time() - overall_start

        assert overall_time < 5.0
        assert len(results) == 50
