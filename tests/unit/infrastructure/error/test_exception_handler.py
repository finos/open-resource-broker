"""
Comprehensive tests for the ExceptionHandler infrastructure.

These tests verify that the exception handling system works correctly
with all types of exceptions while preserving domain semantics.
"""

import json
from unittest.mock import Mock, patch

import pytest

# Import all exception types for testing
from domain.base.exceptions import (
    ConfigurationError,
    InfrastructureError,
    ValidationError,
)
from domain.request.exceptions import RequestValidationError
from domain.template.exceptions import TemplateNotFoundError
from infrastructure.error.decorators import (
    handle_application_exceptions,
    handle_domain_exceptions,
    handle_exceptions,
    handle_infrastructure_exceptions,
    handle_provider_exceptions,
)
from infrastructure.error.exception_handler import (
    ExceptionContext,
    ExceptionHandler,
    get_exception_handler,
    reset_exception_handler,
)
from providers.aws.exceptions.aws_exceptions import LaunchError, NetworkError


class TestExceptionContext:
    """Test ExceptionContext functionality."""

    def test_context_creation(self):
        """Test creating exception context with all parameters."""
        context = ExceptionContext(
            operation="test_operation",
            layer="application",
            user_id="test-user",
            request_id="req-123",
        )

        assert context.operation == "test_operation"
        assert context.layer == "application"
        assert context.additional_context["user_id"] == "test-user"
        assert context.additional_context["request_id"] == "req-123"
        assert context.thread_id is not None
        assert context.timestamp is not None

    def test_context_to_dict(self):
        """Test converting context to dictionary."""
        context = ExceptionContext(operation="test_operation", layer="domain", entity_id="test-123")

        context_dict = context.to_dict()

        assert context_dict["operation"] == "test_operation"
        assert context_dict["layer"] == "domain"
        assert context_dict["entity_id"] == "test-123"
        assert "timestamp" in context_dict
        assert "thread_id" in context_dict


class TestExceptionHandler:
    """Test ExceptionHandler core functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_logger = Mock()
        self.mock_metrics = Mock()
        self.handler = ExceptionHandler(logger=self.mock_logger, metrics=self.mock_metrics)

    def test_handler_initialization(self):
        """Test exception handler initialization."""
        assert self.handler.logger == self.mock_logger
        assert self.handler.metrics == self.mock_metrics
        assert len(self.handler._handlers) > 0
        assert self.handler._performance_stats["total_handled"] == 0

    def test_domain_exception_preservation(self):
        """Test that domain exceptions are preserved without wrapping."""
        original_exception = TemplateNotFoundError("test-123")
        context = ExceptionContext("template_retrieval", "domain")

        result = self.handler.handle(original_exception, context)

        # Should return the SAME exception instance
        assert result is original_exception
        assert isinstance(result, TemplateNotFoundError)
        assert result.details["entity_id"] == "test-123"

        # Should log with rich context
        self.mock_logger.warning.assert_called_once()
        log_call = self.mock_logger.warning.call_args
        assert "Template not found" in log_call[0][0]
        assert log_call[1]["extra"]["template_id"] == "test-123"
        assert log_call[1]["extra"]["domain"] == "template"

    def test_aws_exception_preservation(self):
        """Test that AWS exceptions are preserved without wrapping."""
        original_exception = LaunchError(
            "Failed to launch instances",
            "template-123",
            {"instance_count": 5, "region": "us-east-1"},
        )
        context = ExceptionContext("instance_launch", "infrastructure", provider="aws")

        result = self.handler.handle(original_exception, context)

        # Should return the SAME exception instance
        assert result is original_exception
        assert isinstance(result, LaunchError)
        assert result.details["template_id"] == "template-123"

        # Should log with AWS context
        self.mock_logger.error.assert_called_once()
        log_call = self.mock_logger.error.call_args
        assert "AWS launch error" in log_call[0][0]
        assert log_call[1]["extra"]["provider"] == "aws"
        assert log_call[1]["extra"]["operation_type"] == "launch"

    def test_json_decode_error_wrapping(self):
        """Test that JSONDecodeError is wrapped in ConfigurationError."""
        original_exception = json.JSONDecodeError("Invalid JSON", "test", 10)
        context = ExceptionContext("config_parsing", "infrastructure")

        result = self.handler.handle(original_exception, context)

        # Should wrap in ConfigurationError
        assert isinstance(result, ConfigurationError)
        assert result.error_code == "INVALID_JSON"
        assert "Invalid JSON" in result.message
        assert result.details["line"] == original_exception.lineno
        assert result.details["context"] == "config_parsing"

        # Should log the wrapping
        self.mock_logger.error.assert_called_once()
        log_call = self.mock_logger.error.call_args
        assert "JSON parsing error" in log_call[0][0]

    def test_connection_error_wrapping(self):
        """Test that ConnectionError is wrapped in NetworkError."""
        original_exception = ConnectionError("Connection refused")
        context = ExceptionContext("api_call", "infrastructure")

        result = self.handler.handle(original_exception, context)

        # Should wrap in NetworkError
        assert isinstance(result, NetworkError)
        assert result.error_code == "CONNECTION_FAILED"
        assert "Network connection failed" in result.message
        assert result.details["original_error"] == "Connection refused"

    def test_generic_exception_wrapping(self):
        """Test that unknown exceptions are wrapped in InfrastructureError."""

        class UnknownException(Exception):
            pass

        original_exception = UnknownException("Something weird happened")
        context = ExceptionContext("unknown_operation", "application")

        result = self.handler.handle(original_exception, context)

        # Should wrap in InfrastructureError
        assert isinstance(result, InfrastructureError)
        assert result.error_code == "UNEXPECTED_ERROR"
        assert "Unexpected error" in result.message
        assert result.details["original_exception_type"] == "UnknownException"
        assert result.details["original_message"] == "Something weird happened"

    def test_handler_type_resolution(self):
        """Test that handler finds the most specific handler for exception types."""
        # Test exact match
        handler_func = self.handler._get_handler(TemplateNotFoundError)
        assert handler_func == self.handler._preserve_template_not_found

        # Test parent class match
        class CustomTemplateError(TemplateNotFoundError):
            pass

        handler_func = self.handler._get_handler(CustomTemplateError)
        assert handler_func == self.handler._preserve_template_not_found

        # Test generic fallback
        class CompletelyUnknownError(Exception):
            pass

        handler_func = self.handler._get_handler(CompletelyUnknownError)
        assert handler_func == self.handler._handle_generic_exception

    def test_performance_stats(self):
        """Test performance statistics collection."""
        context = ExceptionContext("test", "application")

        # Handle different exception types
        self.handler.handle(ValidationError("test"), context)
        self.handler.handle(TemplateNotFoundError("test-123"), context)
        self.handler.handle(ValueError("test"), context)

        stats = self.handler.get_performance_stats()

        assert stats["total_handled"] == 3
        assert stats["by_type"]["ValidationError"] == 1
        assert stats["by_type"]["TemplateNotFoundError"] == 1
        assert stats["by_type"]["ValueError"] == 1
        assert "cache_info" in stats

    def test_metrics_recording(self):
        """Test that metrics are recorded when metrics collector is available."""
        context = ExceptionContext("test", "application")
        exception = ValidationError("test")

        self.handler.handle(exception, context)

        # Should record metrics
        self.mock_metrics.increment.assert_any_call("exception.ValidationError")
        self.mock_metrics.increment.assert_any_call("exception.layer.application")


class TestExceptionHandlerSingleton:
    """Test singleton behavior of exception handler."""

    def setup_method(self):
        """Reset singleton before each test."""
        reset_exception_handler()

    def teardown_method(self):
        """Reset singleton after each test."""
        reset_exception_handler()

    def test_singleton_behavior(self):
        """Test that get_exception_handler returns the same instance."""
        handler1 = get_exception_handler()
        handler2 = get_exception_handler()

        assert handler1 is handler2

    def test_singleton_reset(self):
        """Test that reset_exception_handler creates new instance."""
        handler1 = get_exception_handler()
        reset_exception_handler()
        handler2 = get_exception_handler()

        assert handler1 is not handler2


class TestExceptionDecorators:
    """Test exception handling decorators."""

    def setup_method(self):
        """Set up test fixtures."""
        reset_exception_handler()
        self.mock_logger = Mock()

        # Patch the global handler to use our mock logger
        with patch("src.infrastructure.error.exception_handler.get_logger") as mock_get_logger:
            mock_get_logger.return_value = self.mock_logger
            self.handler = get_exception_handler()

    def teardown_method(self):
        """Clean up after tests."""
        reset_exception_handler()

    def test_handle_exceptions_decorator_domain_preservation(self):
        """Test that decorator preserves domain exceptions."""

        @handle_exceptions(context="template_test", layer="domain")
        def raise_template_error():
            raise TemplateNotFoundError("test-123")

        with pytest.raises(TemplateNotFoundError) as exc_info:
            raise_template_error()

        # Should preserve the original exception
        assert exc_info.value.details["entity_id"] == "test-123"

        # Should have logged
        assert self.mock_logger.warning.called

    def test_handle_exceptions_decorator_generic_wrapping(self):
        """Test that decorator wraps generic exceptions."""

        @handle_exceptions(context="json_test", layer="infrastructure")
        def raise_json_error():
            json.loads("invalid json")

        with pytest.raises(ConfigurationError) as exc_info:
            raise_json_error()

        # Should wrap in ConfigurationError
        assert exc_info.value.error_code == "INVALID_JSON"
        assert "json_test" in exc_info.value.message

    def test_handle_exceptions_decorator_context_building(self):
        """Test that decorator builds rich context."""

        @handle_exceptions(
            context="context_test",
            layer="application",
            additional_context={"service": "test_service"},
        )
        def test_function(param1: str, param2: int = 42):
            raise ValueError("test error")

        with pytest.raises(ValidationError):
            test_function("test", param2=100)

        # Verify context was built and logged
        assert self.mock_logger.warning.called
        log_call = self.mock_logger.warning.call_args
        extra = log_call[1]["extra"]

        assert extra["context"]["operation"] == "context_test"
        assert extra["context"]["layer"] == "application"
        assert extra["context"]["function"] == "test_function"
        assert extra["context"]["service"] == "test_service"

    def test_specialized_decorators(self):
        """Test specialized decorators for different layers."""

        @handle_domain_exceptions(context="domain_test")
        def domain_function():
            raise ValidationError("domain validation failed")

        @handle_application_exceptions(context="app_test")
        def application_function():
            raise ValueError("app error")

        @handle_infrastructure_exceptions(context="infra_test")
        def infrastructure_function():
            json.loads("invalid")

        @handle_provider_exceptions(context="provider_test", provider="aws")
        def provider_function():
            raise ConnectionError("connection failed")

        # Test domain decorator
        with pytest.raises(ValidationError):
            domain_function()

        # Test application decorator
        with pytest.raises(ValidationError):
            application_function()

        # Test infrastructure decorator
        with pytest.raises(ConfigurationError):
            infrastructure_function()

        # Test provider decorator
        with pytest.raises(NetworkError):
            provider_function()

    def test_exception_chaining(self):
        """Test that exception chaining is preserved."""

        @handle_exceptions(context="chain_test", layer="application")
        def raise_chained_error():
            try:
                json.loads("invalid")
            except json.JSONDecodeError as e:
                raise ValueError("Processing failed") from e

        with pytest.raises(ValidationError) as exc_info:
            raise_chained_error()

        # Should preserve the exception chain
        # The ValueError gets wrapped in ValidationError, and the chain is preserved
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, ValueError)
        assert str(exc_info.value.__cause__) == "Processing failed"


class TestHTTPErrorHandling:
    """Test HTTP error handling capabilities of the integrated ExceptionHandler."""

    def test_exception_handler_http_formatting(self):
        """Test that ExceptionHandler handles HTTP formatting correctly."""
        handler = get_exception_handler()

        # Test with domain exception
        domain_exception = TemplateNotFoundError("test-123")
        error_response = handler.handle_error_for_http(domain_exception)

        # Should create appropriate HTTP response
        assert error_response.error_code is not None
        assert error_response.message is not None
        assert error_response.http_status == 404  # Not Found

        # Test response formatting
        response_dict = error_response.to_dict()
        assert "error" in response_dict
        assert "status" in response_dict
        assert response_dict["error"]["code"] is not None
        assert response_dict["error"]["message"] is not None


class TestPerformanceAndThreadSafety:
    """Test performance and thread safety of exception handling."""

    def test_handler_caching(self):
        """Test that handler lookup is cached for performance."""
        handler = ExceptionHandler()

        # First lookup should populate cache
        handler_func1 = handler._get_handler(TemplateNotFoundError)

        # Second lookup should use cache
        handler_func2 = handler._get_handler(TemplateNotFoundError)

        assert handler_func1 is handler_func2

        # Check cache statistics
        cache_info = handler._get_handler.cache_info()
        assert cache_info.hits >= 1

    def test_thread_safety(self):
        """Test that exception handler is thread-safe."""
        import threading

        handler = ExceptionHandler()
        results = []
        errors = []

        def handle_exception(exception_type, thread_id):
            try:
                context = ExceptionContext(f"thread_test_{thread_id}", "application")
                if exception_type == "template":
                    exc = TemplateNotFoundError(f"test-{thread_id}")
                else:
                    exc = ValueError(f"test-{thread_id}")

                result = handler.handle(exc, context)
                results.append((thread_id, type(result).__name__))
            except Exception as e:
                errors.append((thread_id, str(e)))

        # Create multiple threads
        threads = []
        for i in range(10):
            exc_type = "template" if i % 2 == 0 else "value"
            thread = threading.Thread(target=handle_exception, args=(exc_type, i))
            threads.append(thread)

        # Start all threads
        for thread in threads:
            thread.start()

        # Wait for all threads
        for thread in threads:
            thread.join()

        # Verify results
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 10

        # Verify correct exception types were returned
        template_results = [r for r in results if r[1] == "TemplateNotFoundError"]
        validation_results = [r for r in results if r[1] == "ValidationError"]

        assert len(template_results) == 5
        assert len(validation_results) == 5


# =============================================================================
# PYTHON BUILT-IN EXCEPTION WRAPPING TESTS
# =============================================================================


class TestPythonBuiltinExceptionWrapping:
    """Test wrapping of Python built-in exceptions into domain exceptions."""

    def test_wrap_json_decode_error_config_context(self):
        """Test JSON decode error wrapping with configuration context."""
        handler = ExceptionHandler()

        # Create a JSON decode error (pos parameter is character position, not line number)
        json_error = json.JSONDecodeError("Invalid JSON", '{"invalid": }', 12)

        # Test configuration context
        result = handler._wrap_json_decode_error(json_error, context="config_loading")

        assert isinstance(result, ConfigurationError)
        assert "Invalid JSON format in config_loading" in str(result)
        assert result.details["line_number"] == json_error.lineno  # Use actual lineno
        assert result.details["original_error"] == str(json_error)
        assert result.details["handler"] == "json_decode_error_handler"

    def test_wrap_json_decode_error_template_context(self):
        """Test JSON decode error wrapping with template context."""
        handler = ExceptionHandler()

        json_error = json.JSONDecodeError("Missing comma", '{"key": "value" "key2": "value2"}', 15)

        result = handler._wrap_json_decode_error(json_error, context="template_parsing")

        assert isinstance(result, ConfigurationError)
        assert "template_parsing" in str(result)
        assert result.details["line_number"] == json_error.lineno  # Use actual lineno
        assert result.details["column_number"] == json_error.colno

    def test_wrap_json_decode_error_request_context(self):
        """Test JSON decode error wrapping with request context."""
        handler = ExceptionHandler()

        json_error = json.JSONDecodeError("Unexpected token", '{"request": invalid}', 10)

        result = handler._wrap_json_decode_error(json_error, context="request_processing")

        assert isinstance(result, RequestValidationError)
        assert "Invalid JSON in request data" in str(result)
        assert result.details["context"] == "request_processing"

    def test_wrap_json_decode_error_general_context(self):
        """Test JSON decode error wrapping with general context."""
        handler = ExceptionHandler()

        json_error = json.JSONDecodeError("Parse error", '{"data": }', 5)

        result = handler._wrap_json_decode_error(json_error, context="data_processing")

        assert isinstance(result, InfrastructureError)
        assert "JSON parsing failed" in str(result)
        assert result.details["context"] == "data_processing"

    def test_wrap_json_decode_error_no_context(self):
        """Test JSON decode error wrapping without context."""
        handler = ExceptionHandler()

        json_error = json.JSONDecodeError("Error", "{}", 1)

        result = handler._wrap_json_decode_error(json_error)

        assert isinstance(result, InfrastructureError)
        assert result.details["context"] == "json_processing"

    def test_wrap_connection_error(self):
        """Test connection error wrapping."""
        handler = ExceptionHandler()

        conn_error = ConnectionError("Connection refused")

        result = handler._wrap_connection_error(conn_error, context="aws_api_call")

        assert isinstance(result, InfrastructureError)
        assert "Connection failed" in str(result)
        assert result.details["context"] == "aws_api_call"
        assert result.details["error_type"] == "ConnectionError"
        assert result.details["handler"] == "connection_error_handler"

    def test_wrap_file_not_found_error_config_context(self):
        """Test file not found error wrapping with config context."""
        handler = ExceptionHandler()

        file_error = FileNotFoundError(2, "No such file", "/path/to/config.json")

        result = handler._wrap_file_not_found_error(file_error, context="config_loading")

        assert isinstance(result, ConfigurationError)
        assert "Required file not found" in str(result)
        assert result.details["filename"] == "/path/to/config.json"
        assert result.details["errno"] == 2
        assert result.details["handler"] == "file_not_found_error_handler"

    def test_wrap_file_not_found_error_template_context(self):
        """Test file not found error wrapping with template context."""
        handler = ExceptionHandler()

        file_error = FileNotFoundError(2, "No such file", "/templates/template.json")

        result = handler._wrap_file_not_found_error(file_error, context="template_loading")

        assert isinstance(result, ConfigurationError)
        assert "template_loading" in result.details["context"]

    def test_wrap_file_not_found_error_general_context(self):
        """Test file not found error wrapping with general context."""
        handler = ExceptionHandler()

        file_error = FileNotFoundError(2, "No such file", "/data/file.txt")

        result = handler._wrap_file_not_found_error(file_error, context="data_access")

        assert isinstance(result, InfrastructureError)
        assert "File not found" in str(result)
        assert result.details["context"] == "data_access"

    def test_wrap_value_error(self):
        """Test value error wrapping."""
        handler = ExceptionHandler()

        value_error = ValueError("Invalid value for parameter")

        result = handler._wrap_value_error(value_error, context="parameter_validation")

        assert isinstance(result, ValidationError)
        assert "Invalid value" in str(result)
        assert result.details["context"] == "parameter_validation"
        assert result.details["error_type"] == "ValueError"
        assert result.details["handler"] == "value_error_handler"

    def test_wrap_key_error(self):
        """Test key error wrapping."""
        handler = ExceptionHandler()

        key_error = KeyError("'required_field'")

        result = handler._wrap_key_error(key_error, context="data_validation")

        assert isinstance(result, ValidationError)
        assert "Missing required key" in str(result)
        assert result.details["missing_key"] == "required_field"
        assert result.details["context"] == "data_validation"
        assert result.details["handler"] == "key_error_handler"

    def test_wrap_type_error(self):
        """Test type error wrapping."""
        handler = ExceptionHandler()

        type_error = TypeError("Expected str, got int")

        result = handler._wrap_type_error(type_error, context="type_validation")

        assert isinstance(result, ValidationError)
        assert "Type error" in str(result)
        assert result.details["context"] == "type_validation"
        assert result.details["error_type"] == "TypeError"
        assert result.details["handler"] == "type_error_handler"

    def test_wrap_attribute_error(self):
        """Test attribute error wrapping."""
        handler = ExceptionHandler()

        attr_error = AttributeError("'NoneType' object has no attribute 'method'")

        result = handler._wrap_attribute_error(attr_error, context="object_access")

        assert isinstance(result, InfrastructureError)
        assert "Attribute error" in str(result)
        assert result.details["context"] == "object_access"
        assert result.details["error_type"] == "AttributeError"
        assert result.details["handler"] == "attribute_error_handler"

    def test_wrap_methods_preserve_kwargs(self):
        """Test that wrap methods preserve additional kwargs."""
        handler = ExceptionHandler()

        json_error = json.JSONDecodeError("Error", "{}", 1)

        result = handler._wrap_json_decode_error(
            json_error,
            context="test",
            custom_field="custom_value",
            operation_id="op_123",
        )

        assert result.details["custom_field"] == "custom_value"
        assert result.details["operation_id"] == "op_123"

    def test_wrap_methods_add_timestamps(self):
        """Test that wrap methods add timestamps."""
        handler = ExceptionHandler()

        value_error = ValueError("Test error")

        result = handler._wrap_value_error(value_error)

        assert "timestamp" in result.details
        assert result.details["timestamp"] is not None
        # Verify timestamp format (ISO format)
        from datetime import datetime

        datetime.fromisoformat(result.details["timestamp"])  # Should not raise

    def test_integration_with_exception_handler(self):
        """Test that wrapped exceptions work with the main handler."""
        handler = ExceptionHandler()

        # Test that a JSON decode error gets properly wrapped when handled
        json_error = json.JSONDecodeError("Invalid JSON", '{"test": }', 10)

        result = handler.handle(json_error, context="config_test")

        assert isinstance(result, ConfigurationError)
        assert "Invalid JSON format" in str(result)
        assert result.details["context"] == "config_test"
