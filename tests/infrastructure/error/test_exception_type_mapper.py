"""
Tests for ExceptionTypeMapper.

Tests handler registration, lookup, MRO resolution, and caching behavior.
"""

from unittest.mock import Mock

import pytest

from infrastructure.error.exception_type_mapper import ExceptionTypeMapper


class CustomException(Exception):
    """Custom exception for testing."""

    pass


class ChildException(CustomException):
    """Child exception for testing inheritance."""

    pass


class GrandchildException(ChildException):
    """Grandchild exception for testing deep inheritance."""

    pass


class UnrelatedError(Exception):
    """Unrelated exception for testing."""

    pass


class TestExceptionTypeMapper:
    """Test suite for ExceptionTypeMapper."""

    def test_register_and_get_handler(self):
        """Test basic handler registration and retrieval."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_handler(ValueError, handler)
        retrieved_handler = mapper.get_handler(ValueError)

        assert retrieved_handler == handler

    def test_register_and_get_http_handler(self):
        """Test HTTP handler registration and retrieval."""
        mapper = ExceptionTypeMapper()
        http_handler = Mock()

        mapper.register_http_handler(ValueError, http_handler)
        retrieved_handler = mapper.get_http_handler(ValueError)

        assert retrieved_handler == http_handler

    def test_mro_handler_lookup(self):
        """Test that handler lookup follows Method Resolution Order."""
        mapper = ExceptionTypeMapper()
        parent_handler = Mock()

        # Register handler for parent class
        mapper.register_handler(CustomException, parent_handler)

        # Child should get parent's handler
        retrieved_handler = mapper.get_handler(ChildException)
        assert retrieved_handler == parent_handler

        # Grandchild should also get parent's handler
        retrieved_handler = mapper.get_handler(GrandchildException)
        assert retrieved_handler == parent_handler

    def test_mro_http_handler_lookup(self):
        """Test that HTTP handler lookup follows Method Resolution Order."""
        mapper = ExceptionTypeMapper()
        parent_handler = Mock()

        # Register HTTP handler for parent class
        mapper.register_http_handler(CustomException, parent_handler)

        # Child should get parent's HTTP handler
        retrieved_handler = mapper.get_http_handler(ChildException)
        assert retrieved_handler == parent_handler

    def test_specific_handler_overrides_parent(self):
        """Test that specific handlers override parent handlers."""
        mapper = ExceptionTypeMapper()
        parent_handler = Mock()
        child_handler = Mock()

        # Register handlers
        mapper.register_handler(CustomException, parent_handler)
        mapper.register_handler(ChildException, child_handler)

        # Parent gets parent handler
        assert mapper.get_handler(CustomException) == parent_handler

        # Child gets specific handler, not parent
        assert mapper.get_handler(ChildException) == child_handler

        # Grandchild gets child handler (most specific)
        assert mapper.get_handler(GrandchildException) == child_handler

    def test_fallback_handler_used_when_no_match(self):
        """Test that fallback handler is used when no specific handler found."""
        mapper = ExceptionTypeMapper()
        fallback_handler = Mock()

        retrieved_handler = mapper.get_handler(UnrelatedError, fallback_handler)
        assert retrieved_handler == fallback_handler

    def test_fallback_http_handler_used_when_no_match(self):
        """Test that fallback HTTP handler is used when no specific handler found."""
        mapper = ExceptionTypeMapper()
        fallback_handler = Mock()

        retrieved_handler = mapper.get_http_handler(UnrelatedError, fallback_handler)
        assert retrieved_handler == fallback_handler

    def test_no_handler_raises_error_without_fallback(self):
        """Test that ValueError is raised when no handler found and no fallback."""
        mapper = ExceptionTypeMapper()

        with pytest.raises(ValueError, match="No handler found for exception type"):
            mapper.get_handler(UnrelatedError)

    def test_no_http_handler_raises_error_without_fallback(self):
        """Test that ValueError is raised when no HTTP handler found and no fallback."""
        mapper = ExceptionTypeMapper()

        with pytest.raises(ValueError, match="No HTTP handler found for exception type"):
            mapper.get_http_handler(UnrelatedError)

    def test_has_handler_exact_match(self):
        """Test has_handler returns True for exact matches."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_handler(ValueError, handler)

        assert mapper.has_handler(ValueError) is True
        assert mapper.has_handler(TypeError) is False

    def test_has_handler_inheritance(self):
        """Test has_handler returns True for inherited handlers."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_handler(CustomException, handler)

        assert mapper.has_handler(CustomException) is True
        assert mapper.has_handler(ChildException) is True
        assert mapper.has_handler(GrandchildException) is True
        assert mapper.has_handler(UnrelatedError) is False

    def test_has_http_handler_exact_match(self):
        """Test has_http_handler returns True for exact matches."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_http_handler(ValueError, handler)

        assert mapper.has_http_handler(ValueError) is True
        assert mapper.has_http_handler(TypeError) is False

    def test_has_http_handler_inheritance(self):
        """Test has_http_handler returns True for inherited handlers."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_http_handler(CustomException, handler)

        assert mapper.has_http_handler(CustomException) is True
        assert mapper.has_http_handler(ChildException) is True
        assert mapper.has_http_handler(GrandchildException) is True
        assert mapper.has_http_handler(UnrelatedError) is False

    def test_clear_handlers(self):
        """Test that clear_handlers removes all registered handlers."""
        mapper = ExceptionTypeMapper()
        handler = Mock()
        http_handler = Mock()

        mapper.register_handler(ValueError, handler)
        mapper.register_http_handler(TypeError, http_handler)

        # Verify handlers are registered
        assert mapper.has_handler(ValueError) is True
        assert mapper.has_http_handler(TypeError) is True

        # Clear handlers
        mapper.clear_handlers()

        # Verify handlers are cleared
        assert mapper.has_handler(ValueError) is False
        assert mapper.has_http_handler(TypeError) is False

    def test_get_registered_types(self):
        """Test that get_registered_types returns all registered types."""
        mapper = ExceptionTypeMapper()

        mapper.register_handler(ValueError, Mock())
        mapper.register_handler(TypeError, Mock())
        mapper.register_http_handler(KeyError, Mock())

        registered_types = mapper.get_registered_types()

        assert ValueError in registered_types
        assert TypeError in registered_types
        assert KeyError in registered_types
        assert len(registered_types) == 3

    def test_caching_behavior(self):
        """Test that handler lookup is cached for performance."""
        mapper = ExceptionTypeMapper()
        handler = Mock()

        mapper.register_handler(CustomException, handler)

        # First call should cache the result
        result1 = mapper.get_handler(ChildException)
        result2 = mapper.get_handler(ChildException)

        # Should return same handler instance (cached)
        assert result1 is result2
        assert result1 == handler

    def test_cache_cleared_on_clear_handlers(self):
        """Test that cache is cleared when handlers are cleared."""
        mapper = ExceptionTypeMapper()
        handler1 = Mock()
        handler2 = Mock()

        # Register and cache a handler
        mapper.register_handler(ValueError, handler1)
        cached_result = mapper.get_handler(ValueError)
        assert cached_result == handler1

        # Clear handlers and register new one
        mapper.clear_handlers()
        mapper.register_handler(ValueError, handler2)

        # Should get new handler, not cached one
        new_result = mapper.get_handler(ValueError)
        assert new_result == handler2
        assert new_result != handler1

    def test_separate_handler_namespaces(self):
        """Test that regular and HTTP handlers are in separate namespaces."""
        mapper = ExceptionTypeMapper()
        regular_handler = Mock()
        http_handler = Mock()

        # Register different handlers for same exception type
        mapper.register_handler(ValueError, regular_handler)
        mapper.register_http_handler(ValueError, http_handler)

        # Should get different handlers
        assert mapper.get_handler(ValueError) == regular_handler
        assert mapper.get_http_handler(ValueError) == http_handler
        assert regular_handler != http_handler
