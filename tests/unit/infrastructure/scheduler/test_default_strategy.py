"""Tests for Default scheduler strategy."""

from unittest.mock import Mock

from infrastructure.scheduler.default.strategy import DefaultSchedulerStrategy


class TestDefaultSchedulerStrategy:
    """Test Default scheduler strategy - native domain format."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_manager = Mock()
        self.mock_logger = Mock()
        self.strategy = DefaultSchedulerStrategy(self.mock_config_manager, self.mock_logger)

    def test_parse_request_data_requests_format(self):
        """Test request status parsing with requests format."""
        raw_request = {"requests": [{"requestId": "req-123"}, {"requestId": "req-456"}]}

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify requests format parsing
        assert isinstance(parsed_request, list)
        assert len(parsed_request) == 2
        assert parsed_request[0]["request_id"] == "req-123"
        assert parsed_request[1]["request_id"] == "req-456"

    def test_parse_request_data_domain_format(self):
        """Test request parsing with native domain format."""
        raw_request = {
            "template_id": "test-template",
            "requested_count": 3,
            "request_type": "provision",
            "metadata": {"user": "test-user"},
        }

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify domain format parsing
        assert parsed_request["template_id"] == "test-template"
        assert parsed_request["requested_count"] == 3
        assert parsed_request["request_type"] == "provision"
        assert parsed_request["metadata"] == {"user": "test-user"}

    def test_parse_request_data_with_count_fallback(self):
        """Test request parsing with count fallback."""
        raw_request = {"template_id": "test-template", "count": 5}

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify count fallback
        assert parsed_request["template_id"] == "test-template"
        assert parsed_request["requested_count"] == 5
        assert parsed_request["request_type"] == "provision"  # Default
        assert parsed_request["metadata"] == {}  # Default

    def test_parse_request_data_with_defaults(self):
        """Test request parsing with default values."""
        raw_request = {"template_id": "minimal-template"}

        parsed_request = self.strategy.parse_request_data(raw_request)

        # Verify defaults
        assert parsed_request["template_id"] == "minimal-template"
        assert parsed_request["requested_count"] == 1  # Default
        assert parsed_request["request_type"] == "provision"  # Default
        assert parsed_request["metadata"] == {}  # Default
