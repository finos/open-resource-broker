"""Global dry-run context manager for provider-agnostic mocking support.

This module provides a thread-safe context manager that enables dry-run mode
across all providers. Each provider is responsible for implementing its own
mocking strategy when dry-run is active.

Architecture:
- Global context at entry point (CLI)
- Provider-specific adapters handle mocking
- Zero business logic changes required
- Thread-safe for concurrent operations
"""

import threading
from contextlib import contextmanager
from typing import Generator

# Thread-local storage for dry-run state
_dry_run_context = threading.local()


@contextmanager
def dry_run_context(active: bool = True) -> Generator[None, None, None]:
    """
    Global dry-run context manager.

    When active, providers should use their respective mocking strategies:
    - AWS Provider: Uses moto for boto3 calls
    - Provider1: Uses Provider1 SDK mocks
    - Provider2: Uses Provider2 SDK mocks

    Args:
        active: Whether to activate dry-run mode

    Yields:
        None

    Example:
        ```python
        with dry_run_context(True):
            # All provider operations within this context use mocking
            result = provider.create_instances(...)
        ```
    """
    # Store previous state for nested contexts
    old_value = getattr(_dry_run_context, "active", False)
    _dry_run_context.active = active

    try:
        yield
    finally:
        # Restore previous state
        _dry_run_context.active = old_value


def is_dry_run_active() -> bool:
    """
    Check if dry-run mode is currently active for the current thread.

    Returns:
        bool: True if dry-run is active, False otherwise

    Example:
        ```python
        if is_dry_run_active():
            # Use mocked operations
            with mock_aws():
                return boto3.client('ec2').run_instances(...)
        else:
            # Use real operations
            return boto3.client('ec2').run_instances(...)
        ```
    """
    return getattr(_dry_run_context, "active", False)


def get_dry_run_status() -> dict:
    """
    Get detailed dry-run status information for debugging.

    Returns:
        dict: Status information including thread ID and active state
    """
    return {
        "active": is_dry_run_active(),
        "thread_id": threading.get_ident(),
        "thread_name": threading.current_thread().name,
    }
