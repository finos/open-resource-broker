"""GCP dry-run context manager.

Activates the shared dry-run context so GCP services can short-circuit before
touching live Compute Engine APIs.
"""

from collections.abc import Generator
from contextlib import contextmanager

from orb.infrastructure.mocking.dry_run_context import dry_run_context


@contextmanager
def gcp_dry_run_context() -> Generator[None, None, None]:
    """Context manager that activates dry-run mode for GCP operations."""
    with dry_run_context(active=True):
        yield
