"""Tests for CLI Response Formatter."""

import unittest
from unittest.mock import Mock

from orb.application.dto.base import BaseDTO, BaseResponse
from orb.cli.response_formatter import CLIResponseFormatter, create_cli_formatter


class MockDTO(BaseDTO):
    """Mock DTO for testing."""

    name: str
    value: int


class MockArgs:
    """Mock CLI args for testing."""

    def __init__(self, format_type="json", resource=None, action=None):
        self.format = format_type
        self.resource = resource
        self.action = action


class TestCLIResponseFormatter(unittest.TestCase):
    """Test CLI Response Formatter functionality."""

    def test_format_simple_dict(self):
        """Test formatting simple dictionary."""
        formatter = CLIResponseFormatter()
        data = {"message": "success", "count": 5}

        result = formatter.format_response(data, MockArgs("json"))

        self.assertIn('"message": "success"', result)
        self.assertIn('"count": 5', result)

    def test_format_base_response_success(self):
        """Test formatting BaseResponse success."""
        formatter = CLIResponseFormatter()
        response = BaseResponse(success=True, message="Operation completed")

        result = formatter.format_response(response, MockArgs("json"))

        # Should return the message only since that's the meaningful data
        self.assertEqual(result, '"Operation completed"')

    def test_format_base_response_error(self):
        """Test formatting BaseResponse error."""
        formatter = CLIResponseFormatter()
        response = BaseResponse(
            success=False, message="Operation failed", error_code="VALIDATION_ERROR"
        )

        result = formatter.format_response(response, MockArgs("json"))

        # Should return tuple with exit code for errors
        self.assertIsInstance(result, tuple)
        formatted_output, exit_code = result
        self.assertEqual(exit_code, 1)
        self.assertIn('"error": true', formatted_output)
        self.assertIn('"message": "Operation failed"', formatted_output)

    def test_format_dto(self):
        """Test formatting DTO object."""
        formatter = CLIResponseFormatter()
        dto = MockDTO(name="test", value=42)

        result = formatter.format_response(dto, MockArgs("json"))

        self.assertIn('"name": "test"', result)
        self.assertIn('"value": 42', result)

    def test_format_with_scheduler_strategy(self):
        """Test formatting with scheduler strategy."""
        mock_scheduler = Mock()
        mock_scheduler.format_template_for_display = Mock(
            return_value={"formatted": True, "template_id": "test"}
        )

        formatter = CLIResponseFormatter(mock_scheduler)
        data = {"template_id": "test", "instance_type": "t3.micro"}

        result = formatter.format_response(
            data, MockArgs("json", resource="templates", action="show")
        )

        # Should have called scheduler formatting
        mock_scheduler.format_template_for_display.assert_called_once()
        self.assertIn('"formatted": true', result)

    def test_format_error_response_dict(self):
        """Test formatting error response as dict."""
        formatter = CLIResponseFormatter()
        error_data = {
            "error": True,
            "message": "Something went wrong",
            "error_code": "INTERNAL_ERROR",
        }

        result = formatter.format_response(error_data, MockArgs("json"))

        self.assertIsInstance(result, tuple)
        formatted_output, exit_code = result
        self.assertEqual(exit_code, 1)
        self.assertIn('"error": true', formatted_output)

    def test_format_success_with_exit_code(self):
        """Test formatting success response with exit code."""
        formatter = CLIResponseFormatter()
        data = {"status": "success", "message": "Completed", "exit_code": 0}

        result = formatter.format_response(data, MockArgs("json"))

        self.assertIsInstance(result, tuple)
        formatted_output, exit_code = result
        self.assertEqual(exit_code, 0)
        self.assertIn('"status": "success"', formatted_output)
        # exit_code should be removed from display
        self.assertNotIn('"exit_code"', formatted_output)

    def test_format_table_output(self):
        """Test formatting table output."""
        formatter = CLIResponseFormatter()
        data = {
            "templates": [
                {"template_id": "t1", "instance_type": "t3.micro"},
                {"template_id": "t2", "instance_type": "t3.small"},
            ]
        }

        result = formatter.format_response(data, MockArgs("table"))

        # Should contain table formatting
        self.assertIn("t1", result)
        self.assertIn("t3.micro", result)

    def test_format_error_method(self):
        """Test format_error method."""
        formatter = CLIResponseFormatter()
        error = ValueError("Test error")

        result = formatter.format_error(error, "json")

        self.assertIsInstance(result, tuple)
        formatted_output, exit_code = result
        self.assertEqual(exit_code, 1)
        self.assertIn('"error": true', formatted_output)
        self.assertIn('"message": "Test error"', formatted_output)

    def test_format_success_message(self):
        """Test format_success_message method."""
        formatter = CLIResponseFormatter()

        # JSON format
        result = formatter.format_success_message("All good", "json")
        self.assertIn('"message": "All good"', result)
        self.assertIn('"success": true', result)

        # Table format
        result = formatter.format_success_message("All good", "table")
        self.assertEqual(result, "All good")

    def test_create_cli_formatter_factory(self):
        """Test factory function."""
        mock_scheduler = Mock()
        formatter = create_cli_formatter(mock_scheduler)

        self.assertIsInstance(formatter, CLIResponseFormatter)
        self.assertEqual(formatter.scheduler_strategy, mock_scheduler)

    def test_fallback_error_handling(self):
        """Test fallback error handling when formatting fails."""
        formatter = CLIResponseFormatter()

        # Create an object that will cause formatting to fail
        class BadObject:
            def to_dict(self):
                raise RuntimeError("Formatting failed")

        bad_obj = BadObject()
        result = formatter.format_response(bad_obj, MockArgs("json"))

        # Should return error tuple
        self.assertIsInstance(result, tuple)
        formatted_output, exit_code = result
        self.assertEqual(exit_code, 1)
        self.assertIn("Formatting error", formatted_output)

    def test_extract_command_context(self):
        """Test command context extraction."""
        formatter = CLIResponseFormatter()

        # Test templates context
        args = MockArgs(resource="templates")
        context = formatter._extract_command_context(args)
        self.assertEqual(context, "templates")

        # Test requests context
        args = MockArgs(resource="requests")
        context = formatter._extract_command_context(args)
        self.assertEqual(context, "requests")

        # Test unknown resource
        args = MockArgs(resource="unknown")
        context = formatter._extract_command_context(args)
        self.assertIsNone(context)


if __name__ == "__main__":
    unittest.main()
