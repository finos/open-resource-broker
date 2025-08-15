"""Tests for format conversion consistency."""

from unittest.mock import MagicMock, patch

from domain.base.ports import SchedulerPort
from infrastructure.scheduler.default.strategy import DefaultSchedulerStrategy
from infrastructure.scheduler.hostfactory.strategy import (
    HostFactorySchedulerStrategy,
)


class TestFormatConversionConsistency:
    """Test format conversion consistency across scheduler strategies."""

    def setup_method(self):
        """Set up test dependencies."""
        self.config_manager = MagicMock()
        self.logger = MagicMock()

        # Create scheduler strategies
        self.default_strategy = DefaultSchedulerStrategy(self.config_manager, self.logger)
        self.symphony_strategy = HostFactorySchedulerStrategy(self.config_manager, self.logger)

        # Sample data for testing
        self.sample_templates = [
            {
                "id": "template1",
                "name": "Template 1",
                "description": "Test template 1",
                "provider_api": "aws",
                "instance_type": "t2.micro",
                "image_id": "ami-12345",
                "subnet_ids": ["subnet-1", "subnet-2"],
                "security_group_ids": ["sg-1", "sg-2"],
                "tags": {"Name": "Test", "Environment": "Dev"},
            },
            {
                "id": "template2",
                "name": "Template 2",
                "description": "Test template 2",
                "provider_api": "aws",
                "instance_type": "t2.small",
                "image_id": "ami-67890",
                "subnet_ids": ["subnet-3"],
                "security_group_ids": ["sg-3"],
                "tags": {"Name": "Test2", "Environment": "Prod"},
            },
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
        # Act
        default_result = self.default_strategy.format_templates_response(self.sample_templates)
        symphony_result = self.symphony_strategy.format_templates_response(self.sample_templates)

        # Assert
        assert "templates" in default_result
        assert "templates" in symphony_result
        assert len(default_result["templates"]) == len(self.sample_templates)
        assert len(symphony_result["templates"]) == len(self.sample_templates)

        # Check that both strategies include the same fields
        for i, _template in enumerate(self.sample_templates):
            default_template = default_result["templates"][i]
            symphony_template = symphony_result["templates"][i]

            # Check that both strategies include the same fields
            assert set(default_template.keys()) == set(symphony_template.keys())

            # Check that both strategies include the required fields
            required_fields = ["id", "name", "description", "provider_api"]
            for field in required_fields:
                assert field in default_template
                assert field in symphony_template
                assert default_template[field] == symphony_template[field]

    def test_format_request_response_consistency(self):
        """Test that format_request_response is consistent across strategies."""
        # Act
        default_result = self.default_strategy.format_request_response(self.sample_request)
        symphony_result = self.symphony_strategy.format_request_response(self.sample_request)

        # Assert
        assert "request" in default_result
        assert "request" in symphony_result

        default_request = default_result["request"]
        symphony_request = symphony_result["request"]

        # Check that both strategies include the same fields
        assert set(default_request.keys()) == set(symphony_request.keys())

        # Check that both strategies include the required fields
        required_fields = ["id", "template_id", "status"]
        for field in required_fields:
            assert field in default_request
            assert field in symphony_request
            assert default_request[field] == symphony_request[field]

        # Check that machines are included and formatted consistently
        assert "machines" in default_request
        assert "machines" in symphony_request
        assert len(default_request["machines"]) == len(self.sample_request["machines"])
        assert len(symphony_request["machines"]) == len(self.sample_request["machines"])

    def test_format_return_request_response_consistency(self):
        """Test that format_return_request_response is consistent across strategies."""
        # Act
        default_result = self.default_strategy.format_return_request_response(
            self.sample_return_request
        )
        symphony_result = self.symphony_strategy.format_return_request_response(
            self.sample_return_request
        )

        # Assert
        assert "return_request" in default_result
        assert "return_request" in symphony_result

        default_request = default_result["return_request"]
        symphony_request = symphony_result["return_request"]

        # Check that both strategies include the same fields
        assert set(default_request.keys()) == set(symphony_request.keys())

        # Check that both strategies include the required fields
        required_fields = ["id", "request_id", "status"]
        for field in required_fields:
            assert field in default_request
            assert field in symphony_request
            assert default_request[field] == symphony_request[field]

        # Check that machine_ids are included and formatted consistently
        assert "machine_ids" in default_request
        assert "machine_ids" in symphony_request
        assert len(default_request["machine_ids"]) == len(self.sample_return_request["machine_ids"])
        assert len(symphony_request["machine_ids"]) == len(
            self.sample_return_request["machine_ids"]
        )


class TestFormatConversionInHandlers:
    """Test that format conversion is used consistently in handlers."""

    @patch(
        "src.api.handlers.get_available_templates_handler.GetAvailableTemplatesRESTHandler._handle"
    )
    def test_format_conversion_in_api_handler(self, mock_handle):
        """Test that format conversion is done using the scheduler strategy in API handlers."""
        # Arrange
        from api.handlers.get_available_templates_handler import (
            GetAvailableTemplatesRESTHandler,
        )

        query_bus = MagicMock()
        command_bus = MagicMock()
        scheduler_strategy = MagicMock(spec=SchedulerPort)
        metrics = MagicMock()

        # Create handler
        handler = GetAvailableTemplatesRESTHandler(
            query_bus=query_bus,
            command_bus=command_bus,
            scheduler_strategy=scheduler_strategy,
            metrics=metrics,
        )

        # Mock _handle to return templates
        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        mock_handle.return_value = templates

        # Mock scheduler_strategy.format_templates_response to return formatted templates
        formatted_templates = {
            "templates": [
                {"id": "template1", "formatted": True},
                {"id": "template2", "formatted": True},
            ]
        }
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        # Act
        handler.handle(MagicMock())

        # Assert
        # Verify that the scheduler strategy was used for format conversion
        scheduler_strategy.format_templates_response.assert_called_once_with(templates)

    @patch("src.interface.template_command_handlers.get_container")
    async def test_format_conversion_in_cli_handler(self, mock_get_container):
        """Test that format conversion is done using the scheduler strategy in CLI handlers."""
        # Arrange
        import argparse

        from interface.template_command_handlers import handle_list_templates

        container = MagicMock()
        query_bus = MagicMock()
        scheduler_strategy = MagicMock(spec=SchedulerPort)

        # Mock query_bus.execute to return templates
        templates = [
            {"id": "template1", "name": "Template 1"},
            {"id": "template2", "name": "Template 2"},
        ]
        query_bus.execute = MagicMock(return_value=templates)

        # Mock scheduler_strategy.format_templates_response to return formatted templates
        formatted_templates = {
            "templates": [
                {"id": "template1", "formatted": True},
                {"id": "template2", "formatted": True},
            ]
        }
        scheduler_strategy.format_templates_response = MagicMock(return_value=formatted_templates)

        # Set up container.get to return the mocked objects
        container.get.side_effect = lambda x: {
            "QueryBus": query_bus,
            "SchedulerPort": scheduler_strategy,
        }.get(x)

        mock_get_container.return_value = container

        # Create args with default values
        args = argparse.Namespace(provider_api=None, active_only=True, include_config=False)

        # Act
        await handle_list_templates(args)

        # Assert
        # Verify that the scheduler strategy was used for format conversion
        scheduler_strategy.format_templates_response.assert_called_once_with(templates)
