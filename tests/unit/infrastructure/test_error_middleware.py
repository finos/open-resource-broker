"""Unit tests for error/error_middleware.py."""

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.error.categories import ErrorCategory
from orb.infrastructure.error.error_middleware import (
    ErrorMiddleware,
    with_api_error_handling,
    with_error_handling,
)
from orb.infrastructure.error.responses import InfrastructureErrorResponse


def _make_mock_handler(error_code: str = "ERR", message: str = "error msg") -> MagicMock:
    """Build a mock ExceptionHandler whose handle_error_for_http returns a fixed response."""
    mock_response = MagicMock(spec=InfrastructureErrorResponse)
    mock_response.error_code = error_code
    mock_response.message = message
    mock_response.details = {"entity_type": "Machine"}
    mock_response.to_dict.return_value = {
        "error": {
            "code": error_code,
            "message": message,
            "category": ErrorCategory.INTERNAL,
            "details": {},
        },
        "status": 500,
        "timestamp": "2024-01-01T00:00:00Z",
    }
    handler = MagicMock()
    handler.handle_error_for_http.return_value = mock_response
    return handler


@pytest.mark.unit
class TestErrorMiddlewareWrapHandler:
    """Tests for ErrorMiddleware.wrap_handler."""

    def test_passes_through_return_value_on_success(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())

        def handler():
            return {"result": "ok"}

        wrapped = middleware.wrap_handler(handler)
        assert wrapped() == {"result": "ok"}

    def test_catches_exception_and_returns_error_dict(self) -> None:
        mock_handler = _make_mock_handler(error_code="UNEXPECTED_ERROR")
        middleware = ErrorMiddleware(error_handler=mock_handler)

        def bad_handler():
            raise RuntimeError("boom")

        wrapped = middleware.wrap_handler(bad_handler)
        result = wrapped()
        mock_handler.handle_error_for_http.assert_called_once()
        called_exc = mock_handler.handle_error_for_http.call_args[0][0]
        assert isinstance(called_exc, RuntimeError)
        # Result comes from to_dict()
        assert result["error"]["code"] == "UNEXPECTED_ERROR"

    def test_preserves_function_name(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())

        def my_handler():
            return 1

        wrapped = middleware.wrap_handler(my_handler)
        assert wrapped.__name__ == "my_handler"

    def test_passes_args_and_kwargs_to_handler(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())
        received: list = []

        def handler(a, b=None):
            received.extend([a, b])
            return "ok"

        wrapped = middleware.wrap_handler(handler)
        wrapped(42, b="hello")
        assert received == [42, "hello"]


@pytest.mark.unit
class TestErrorMiddlewareWrapApiHandler:
    """Tests for ErrorMiddleware.wrap_api_handler."""

    def test_passes_through_on_success(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())

        def api_handler(input_data=None, **kwargs):
            return {"data": input_data}

        wrapped = middleware.wrap_api_handler(api_handler)
        result = wrapped({"key": "val"})
        assert result == {"data": {"key": "val"}}

    def test_returns_error_dict_on_exception(self) -> None:
        mock_handler = _make_mock_handler(error_code="VALIDATION_ERROR", message="invalid")
        middleware = ErrorMiddleware(error_handler=mock_handler)

        def api_handler(input_data=None, **kwargs):
            raise ValueError("bad input")

        wrapped = middleware.wrap_api_handler(api_handler)
        result = wrapped({})
        assert result["error"] == "VALIDATION_ERROR"
        assert result["message"] == "invalid"

    def test_passes_none_input_data(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())

        def api_handler(input_data=None):
            return input_data

        wrapped = middleware.wrap_api_handler(api_handler)
        assert wrapped() is None


@pytest.mark.unit
class TestErrorMiddlewareWrapScriptHandler:
    """Tests for ErrorMiddleware.wrap_script_handler."""

    def test_passes_through_on_success(self) -> None:
        middleware = ErrorMiddleware(error_handler=_make_mock_handler())

        def script():
            return 0

        wrapped = middleware.wrap_script_handler(script)
        assert wrapped() == 0

    def test_calls_sys_exit_on_exception(self, monkeypatch) -> None:
        mock_handler = _make_mock_handler(error_code="ERR")
        middleware = ErrorMiddleware(error_handler=mock_handler)
        exit_calls: list = []
        monkeypatch.setattr("sys.exit", lambda code: exit_calls.append(code))

        def bad_script():
            raise RuntimeError("crash")

        wrapped = middleware.wrap_script_handler(bad_script)
        wrapped()
        assert exit_calls == [1]


@pytest.mark.unit
class TestWithErrorHandlingDecorator:
    """Tests for the with_error_handling function decorator."""

    def test_success_path(self) -> None:
        mock_handler = _make_mock_handler()

        @with_error_handling(error_handler=mock_handler)
        def func():
            return 42

        assert func() == 42

    def test_exception_path_returns_dict(self) -> None:
        mock_handler = _make_mock_handler(error_code="EXPECTED_CODE")

        @with_error_handling(error_handler=mock_handler)
        def func():
            raise KeyError("missing")

        result = func()
        assert result["error"]["code"] == "EXPECTED_CODE"


@pytest.mark.unit
class TestWithApiErrorHandlingDecorator:
    """Tests for the with_api_error_handling function decorator."""

    def test_success_path(self) -> None:
        mock_handler = _make_mock_handler()

        @with_api_error_handling(error_handler=mock_handler)
        def func(input_data=None):
            return {"out": input_data}

        assert func({"a": 1}) == {"out": {"a": 1}}

    def test_exception_returns_api_error_shape(self) -> None:
        mock_handler = _make_mock_handler(error_code="API_ERR", message="api error")

        @with_api_error_handling(error_handler=mock_handler)
        def func(input_data=None):
            raise ValueError("invalid")

        result = func({})
        assert result["error"] == "API_ERR"
        assert result["message"] == "api error"
        assert "details" in result

    def test_preserves_wrapped_function_name(self) -> None:
        mock_handler = _make_mock_handler()

        @with_api_error_handling(error_handler=mock_handler)
        def my_api_func(input_data=None):
            return None

        assert my_api_func.__name__ == "my_api_func"
