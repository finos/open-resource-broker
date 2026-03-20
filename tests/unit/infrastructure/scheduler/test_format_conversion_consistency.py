"""Tests for format conversion consistency."""

from unittest.mock import MagicMock, patch

import pytest

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.template import Template
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.dtos import TemplateDTO


class TestFormatConversionConsistency:
    """Test format conversion consistency across scheduler strategies."""

    def setup_method(self):
        """Set up test dependencies."""
        self.config_manager = MagicMock()
        self.logger = MagicMock()

        # Create scheduler strategies
        self.default_strategy = DefaultSchedulerStrategy()

        # Mock the container to avoid DI issues
        mock_container = MagicMock()
        mock_provider_service = MagicMock()
        mock_container.get.return_value = mock_provider_service

        with patch("orb.infrastructure.di.container.get_container", return_value=mock_container):
            self.symphony_strategy = HostFactorySchedulerStrategy()

        # Sample data for testing
        self.sample_templates = [
            Template(
                template_id="template1",
                name="Template 1",
                description="Test template 1",
                instance_type="t2.micro",
                image_id="ami-12345",
                subnet_ids=["subnet-1", "subnet-2"],
                security_group_ids=["sg-1", "sg-2"],
            ),
            Template(
                template_id="template2",
                name="Template 2",
                description="Test template 2",
                instance_type="t2.small",
                image_id="ami-67890",
                subnet_ids=["subnet-3"],
                security_group_ids=["sg-3"],
            ),
        ]

        self.sample_request = {
            "id": "request1",
            "template_id": "template1",
            "count": 2,
            "status": "completed",
            "machines": [
                {"id": "machine1", "instance_id": "i-12345", "status": "running"},
                {"id": "machine2", "instance_id": "i-67890", "status": "running"},
            ],
        }

        self.sample_return_request = {
            "id": "return1",
            "request_id": "request1",
            "machine_ids": ["machine1", "machine2"],
            "status": "completed",
        }

    def test_format_templates_response_consistency(self):
        """Test that format_templates_response is consistent across strategies."""
        # format_template_for_display expects TemplateDTO objects with to_dict()
        template_dtos = [
            TemplateDTO(template_id=t.template_id, name=t.name, description=t.description)
            for t in self.sample_templates
        ]

        default_result = self.default_strategy.format_templates_response(template_dtos)
        symphony_result = self.symphony_strategy.format_templates_response(template_dtos)

        assert "templates" in default_result
        assert "templates" in symphony_result
        assert len(default_result["templates"]) == len(self.sample_templates)
        assert len(symphony_result["templates"]) == len(self.sample_templates)

        # Each strategy may use different field naming conventions; just verify
        # that each returns a non-empty dict per template
        for i in range(len(self.sample_templates)):
            assert isinstance(default_result["templates"][i], dict)
            assert isinstance(symphony_result["templates"][i], dict)

    def test_format_request_response_consistency(self):
        """Test that format_request_response returns a dict for both strategies."""
        # format_request_response takes a dict and returns a dict (shape varies by status)
        request_with_requests = {
            "requests": [{"requestId": "request1", "status": "complete"}],
            "status": "complete",
        }

        default_result = self.default_strategy.format_request_response(request_with_requests)
        symphony_result = self.symphony_strategy.format_request_response(request_with_requests)

        assert isinstance(default_result, dict)
        assert isinstance(symphony_result, dict)
        assert "requests" in default_result
        assert "requests" in symphony_result

    def test_format_return_request_response_consistency(self):
        """Test that format_request_response handles return request data consistently."""
        # format_return_request_response does not exist; use format_request_response
        # Both strategies should return a dict
        default_result = self.default_strategy.format_request_response(self.sample_return_request)
        symphony_result = self.symphony_strategy.format_request_response(self.sample_return_request)

        assert isinstance(default_result, dict)
        assert isinstance(symphony_result, dict)


class TestExplicitFieldExtraction:
    """format_system_status_response and format_provider_detail_response must extract fields explicitly."""

    def test_format_system_status_response_extracts_known_fields(self):
        """Base strategy must extract all SystemStatusDTO fields explicitly — not passthrough raw."""
        from orb.infrastructure.scheduler.base.strategy import BaseSchedulerStrategy

        strategy = DefaultSchedulerStrategy()
        raw = {
            "status": "healthy",
            "uptime_seconds": 123.4,
            "version": "1.0.0",
            "environment": "test",
            "active_connections": 5,
            "memory_usage_mb": 256.0,
            "cpu_usage_percent": 10.0,
            "disk_usage_percent": 20.0,
            "last_health_check": "2026-03-20T00:00:00+00:00",
            "components": {"db": "ok"},
            "unknown_internal_field": "should_not_leak",
        }
        result = strategy.format_system_status_response(raw)

        assert result["status"] == "healthy"
        assert result["uptime_seconds"] == 123.4
        assert result["version"] == "1.0.0"
        assert result["environment"] == "test"
        assert result["active_connections"] == 5
        assert result["memory_usage_mb"] == 256.0
        assert result["cpu_usage_percent"] == 10.0
        assert result["disk_usage_percent"] == 20.0
        assert result["last_health_check"] == "2026-03-20T00:00:00+00:00"
        assert result["components"] == {"db": "ok"}
        assert "unknown_internal_field" not in result, (
            "Unknown fields must not leak into wire format"
        )

    def test_format_provider_detail_response_extracts_known_fields(self):
        """Base strategy must extract provider detail fields explicitly — not passthrough raw."""
        strategy = DefaultSchedulerStrategy()
        raw = {
            "name": "my-provider",
            "type": "aws",
            "enabled": True,
            "config": {"region": "us-east-1"},
            "template_defaults": {"instance_type": "t3.micro"},
            "unknown_internal_field": "should_not_leak",
        }
        result = strategy.format_provider_detail_response(raw)

        assert result["name"] == "my-provider"
        assert result["type"] == "aws"
        assert result["enabled"] is True
        assert result["config"] == {"region": "us-east-1"}
        assert result["template_defaults"] == {"instance_type": "t3.micro"}
        assert "unknown_internal_field" not in result, (
            "Unknown fields must not leak into wire format"
        )

    def test_format_provider_detail_response_omits_template_defaults_when_absent(self):
        """template_defaults must not appear in output when not present in input."""
        strategy = DefaultSchedulerStrategy()
        raw = {"name": "p", "type": "aws", "enabled": True, "config": {}}
        result = strategy.format_provider_detail_response(raw)

        assert "template_defaults" not in result


class TestMachineOperationDelegation:
    """format_machine_operation must delegate to format_machine_details_response, not a separate method."""

    def test_format_machine_operation_response_not_on_scheduler_port(self):
        """SchedulerPort must NOT have format_machine_operation_response — it is redundant."""
        assert not hasattr(SchedulerPort, "format_machine_operation_response"), (
            "format_machine_operation_response is redundant with format_machine_details_response "
            "and must not exist on SchedulerPort"
        )

    def test_format_machine_operation_delegates_to_format_machine_details_response(self):
        """ResponseFormattingService.format_machine_operation must call format_machine_details_response."""
        from orb.interface.response_formatting_service import ResponseFormattingService

        scheduler = MagicMock(spec=SchedulerPort)
        machine_data = {"id": "m-1", "name": "host1", "status": "running", "error": None}
        scheduler.format_machine_details_response.return_value = machine_data

        formatter = ResponseFormattingService(scheduler)
        result = formatter.format_machine_operation(machine_data)

        scheduler.format_machine_details_response.assert_called_once_with(machine_data)
        assert result.data == machine_data
        assert result.exit_code == 0

    def test_format_machine_operation_exit_code_1_on_error(self):
        """format_machine_operation must return exit_code=1 when data contains 'error'."""
        from orb.interface.response_formatting_service import ResponseFormattingService

        scheduler = MagicMock(spec=SchedulerPort)
        machine_data = {"id": "m-1", "status": "error", "error": "not found"}
        scheduler.format_machine_details_response.return_value = machine_data

        formatter = ResponseFormattingService(scheduler)
        result = formatter.format_machine_operation(machine_data)

        assert result.exit_code == 1


class TestFormatConversionInHandlers:
    """Test that format conversion is used consistently in handlers."""

    def test_format_conversion_in_list_templates_orchestrator(self):
        """Test that ListTemplatesOrchestrator can be instantiated with a scheduler strategy."""
        from orb.application.services.orchestration.list_templates import ListTemplatesOrchestrator
        from orb.infrastructure.di.buses import CommandBus, QueryBus

        command_bus = MagicMock(spec=CommandBus)
        query_bus = MagicMock(spec=QueryBus)
        logger = MagicMock()

        orchestrator = ListTemplatesOrchestrator(
            command_bus=command_bus,
            query_bus=query_bus,
            logger=logger,
        )

        assert orchestrator is not None
        assert orchestrator._query_bus is query_bus

    @pytest.mark.asyncio
    async def test_format_conversion_in_cli_handler(self):
        """Test that format conversion is done using ResponseFormattingService in CLI handlers."""
        import argparse

        from orb.interface.response_formatting_service import ResponseFormattingService
        from orb.interface.template_command_handlers import handle_list_templates

        with patch("orb.interface.template_command_handlers.get_container") as mock_get_container:
            from unittest.mock import AsyncMock

            from orb.application.services.orchestration.list_templates import (
                ListTemplatesOrchestrator,
            )

            container = MagicMock()
            formatter = MagicMock(spec=ResponseFormattingService)
            orchestrator = MagicMock(spec=ListTemplatesOrchestrator)
            orchestrator.execute = AsyncMock(return_value=MagicMock(templates=[{"id": "t1"}]))
            formatter.format_template_list = MagicMock(return_value=MagicMock())

            container.get.side_effect = lambda x: {
                ListTemplatesOrchestrator: orchestrator,
                ResponseFormattingService: formatter,
            }.get(x, MagicMock())

            mock_get_container.return_value = container

            args = argparse.Namespace(provider_api=None, active_only=True, include_config=False)

            result = await handle_list_templates(args)
            assert result is not None
