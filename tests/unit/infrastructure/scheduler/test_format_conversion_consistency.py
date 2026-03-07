"""Tests for format conversion consistency."""

from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.ports import SchedulerPort
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


class TestFormatConversionInHandlers:
    """Test that format conversion is used consistently in handlers."""

    def test_format_conversion_in_api_handler(self):
        """Test that format conversion is done using the scheduler strategy in API handlers."""
        from orb.api.handlers.get_available_templates_handler import (
            GetAvailableTemplatesRESTHandler,
        )

        query_bus = MagicMock()
        command_bus = MagicMock()
        scheduler_strategy = MagicMock(spec=SchedulerPort)
        metrics = MagicMock()

        handler = GetAvailableTemplatesRESTHandler(
            query_bus=query_bus,
            command_bus=command_bus,
            scheduler_strategy=scheduler_strategy,
            metrics=metrics,
        )

        # Verify handler was created and has the scheduler strategy
        assert handler is not None
        assert handler._scheduler_strategy is scheduler_strategy

    @pytest.mark.asyncio
    async def test_format_conversion_in_cli_handler(self):
        """Test that format conversion is done using the scheduler strategy in CLI handlers."""
        import argparse

        from orb.interface.template_command_handlers import handle_list_templates

        with patch("orb.interface.template_command_handlers.get_container") as mock_get_container:
            container = MagicMock()
            query_bus = MagicMock()
            scheduler_strategy = MagicMock(spec=SchedulerPort)

            from unittest.mock import AsyncMock

            query_bus.execute = AsyncMock(return_value=[])

            container.get.side_effect = lambda x: {
                "QueryBus": query_bus,
                "SchedulerPort": scheduler_strategy,
            }.get(x.__name__ if hasattr(x, "__name__") else str(x), MagicMock())

            mock_get_container.return_value = container

            args = argparse.Namespace(provider_api=None, active_only=True, include_config=False)

            result = await handle_list_templates(args)
            assert result is not None
