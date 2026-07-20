"""Unit tests for domain decorators module."""

from unittest.mock import MagicMock

import pytest

from orb.domain.base.decorators import (
    get_domain_container,
    get_error_handling_port,
    handle_domain_exceptions,
    set_domain_container,
)


@pytest.fixture(autouse=True)
def reset_domain_container():
    """Restore the module-level container to None after each test."""
    yield
    # Always clean up after test so state does not bleed between tests
    set_domain_container(None)  # type: ignore[arg-type]


@pytest.mark.unit
class TestSetGetDomainContainer:
    def test_default_is_none(self):
        assert get_domain_container() is None

    def test_set_and_get_container(self):
        mock_container = MagicMock()
        set_domain_container(mock_container)
        assert get_domain_container() is mock_container

    def test_overwrite_container(self):
        first = MagicMock()
        second = MagicMock()
        set_domain_container(first)
        set_domain_container(second)
        assert get_domain_container() is second


@pytest.mark.unit
class TestGetErrorHandlingPort:
    def test_returns_none_when_no_container(self):
        assert get_error_handling_port() is None

    def test_returns_port_from_container(self):
        mock_port = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_port
        set_domain_container(mock_container)

        result = get_error_handling_port()

        assert result is mock_port

    def test_returns_none_when_container_raises(self):
        mock_container = MagicMock()
        mock_container.get.side_effect = RuntimeError("service not registered")
        set_domain_container(mock_container)

        result = get_error_handling_port()

        assert result is None


@pytest.mark.unit
class TestHandleDomainExceptionsDecorator:
    def test_returns_value_without_container(self):
        @handle_domain_exceptions("test_op")
        def add(a, b):
            return a + b

        assert add(2, 3) == 5

    def test_re_raises_with_context_when_no_container(self):
        @handle_domain_exceptions("my_context")
        def boom():
            raise ValueError("inner problem")

        with pytest.raises(ValueError, match="my_context: inner problem"):
            boom()

    def test_preserves_exception_type_when_no_container(self):
        @handle_domain_exceptions("ctx")
        def raise_runtime():
            raise RuntimeError("oops")

        with pytest.raises(RuntimeError):
            raise_runtime()

    def test_wraps_preserves_function_name(self):
        @handle_domain_exceptions("ctx")
        def my_func():
            return 42

        assert my_func.__name__ == "my_func"

    def test_with_error_handler_that_returns_message(self):
        mock_port = MagicMock()
        mock_port.handle_domain_exceptions.return_value = "translated error"
        mock_container = MagicMock()
        mock_container.get.return_value = mock_port
        set_domain_container(mock_container)

        @handle_domain_exceptions("ctx")
        def raise_value():
            raise ValueError("raw")

        with pytest.raises(ValueError, match="ctx: translated error"):
            raise_value()

    def test_with_error_handler_that_returns_none_re_raises_original(self):
        mock_port = MagicMock()
        mock_port.handle_domain_exceptions.return_value = None
        mock_container = MagicMock()
        mock_container.get.return_value = mock_port
        set_domain_container(mock_container)

        @handle_domain_exceptions("ctx")
        def raise_value():
            raise ValueError("original")

        with pytest.raises(ValueError, match="original"):
            raise_value()

    def test_happy_path_with_container_available(self):
        mock_port = MagicMock()
        mock_container = MagicMock()
        mock_container.get.return_value = mock_port
        set_domain_container(mock_container)

        @handle_domain_exceptions("ctx")
        def ok():
            return "success"

        assert ok() == "success"
