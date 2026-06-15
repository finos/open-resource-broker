"""Azure dry-run context manager.

Activates global ``dry_run_context`` from the infrastructure layer so that
Azure handler code can call ``is_dry_run_active()`` to skip real ARM calls.
"""

from collections.abc import Generator
from contextlib import contextmanager

from orb.infrastructure.mocking.dry_run_context import dry_run_context


@contextmanager
def azure_dry_run_context() -> Generator[None, None, None]:
    """Context manager that activates dry-run mode for Azure operations.

    Usage::

        with azure_dry_run_context():
            result = await strategy.execute_operation(op)
    """
    with dry_run_context(active=True):
        yield
