"""Unit tests for dry-run context manager."""

import threading

import pytest

from infrastructure.mocking.dry_run_context import (
    dry_run_context,
    get_dry_run_status,
    is_dry_run_active,
)


class TestDryRunContext:
    """Test cases for dry-run context manager."""

    def test_dry_run_context_activation(self):
        """Test that dry-run context activates and deactivates correctly."""
        # Initially not active
        assert not is_dry_run_active()

        # Active within context
        with dry_run_context(True):
            assert is_dry_run_active()

        # Inactive after context
        assert not is_dry_run_active()

    def test_dry_run_context_deactivation(self):
        """Test that dry-run context can be explicitly deactivated."""
        assert not is_dry_run_active()

        with dry_run_context(False):
            assert not is_dry_run_active()

        assert not is_dry_run_active()

    def test_nested_dry_run_contexts(self):
        """Test that nested contexts work correctly."""
        assert not is_dry_run_active()

        with dry_run_context(True):
            assert is_dry_run_active()

            # Nested context with different value
            with dry_run_context(False):
                assert not is_dry_run_active()

            # Back to outer context
            assert is_dry_run_active()

        # Back to original state
        assert not is_dry_run_active()

    def test_exception_handling_in_context(self):
        """Test that context is properly restored even if exception occurs."""
        assert not is_dry_run_active()

        try:
            with dry_run_context(True):
                assert is_dry_run_active()
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Context should be restored even after exception
        assert not is_dry_run_active()

    def test_thread_local_isolation(self):
        """Test that dry-run context is isolated between threads."""
        results = {}

        def thread_function(thread_id: int, activate: bool):
            """Function to run in separate thread."""
            with dry_run_context(activate):
                results[thread_id] = is_dry_run_active()

        # Create threads with different dry-run states
        thread1 = threading.Thread(target=thread_function, args=(1, True))
        thread2 = threading.Thread(target=thread_function, args=(2, False))

        thread1.start()
        thread2.start()

        thread1.join()
        thread2.join()

        # Each thread should have its own state
        assert results[1] is True
        assert results[2] is False

        # Main thread should be unaffected
        assert not is_dry_run_active()

    def test_get_dry_run_status(self):
        """Test dry-run status information."""
        status = get_dry_run_status()

        assert isinstance(status, dict)
        assert "active" in status
        assert "thread_id" in status
        assert "thread_name" in status
        assert status["active"] is False

        with dry_run_context(True):
            status = get_dry_run_status()
            assert status["active"] is True

    def test_default_context_activation(self):
        """Test that dry_run_context() defaults to True."""
        assert not is_dry_run_active()

        with dry_run_context():  # No argument, should default to True
            assert is_dry_run_active()

        assert not is_dry_run_active()

    def test_multiple_sequential_contexts(self):
        """Test multiple sequential contexts work correctly."""
        assert not is_dry_run_active()

        # First context
        with dry_run_context(True):
            assert is_dry_run_active()

        assert not is_dry_run_active()

        # Second context
        with dry_run_context(False):
            assert not is_dry_run_active()

        assert not is_dry_run_active()

        # Third context
        with dry_run_context(True):
            assert is_dry_run_active()

        assert not is_dry_run_active()


@pytest.mark.integration
class TestDryRunContextIntegration:
    """Integration tests for dry-run context manager."""

    def test_context_with_mock_operations(self):
        """Test dry-run context with mock operations."""
        operations_log = []

        def mock_operation():
            """Mock operation that checks dry-run status."""
            if is_dry_run_active():
                operations_log.append("MOCK_OPERATION")
                return "mocked_result"
            else:
                operations_log.append("REAL_OPERATION")
                return "real_result"

        # Real operation
        result = mock_operation()
        assert result == "real_result"
        assert operations_log[-1] == "REAL_OPERATION"

        # Mock operation
        with dry_run_context(True):
            result = mock_operation()
            assert result == "mocked_result"
            assert operations_log[-1] == "MOCK_OPERATION"
