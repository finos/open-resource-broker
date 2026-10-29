"""Keep blocking OCI operations off the event loop without losing dry-run state."""

import asyncio
from collections.abc import Callable

from orb.infrastructure.mocking.dry_run_context import dry_run_context, is_dry_run_active
from orb.providers.base.strategy import ProviderOperation, ProviderResult


async def run_blocking_operation(
    handler: Callable[[ProviderOperation], ProviderResult], operation: ProviderOperation
) -> ProviderResult:
    """Propagate ORB's thread-local simulation flag to the worker thread."""
    dry_run = is_dry_run_active()

    def run() -> ProviderResult:
        with dry_run_context(dry_run):
            return handler(operation)

    return await asyncio.to_thread(run)
