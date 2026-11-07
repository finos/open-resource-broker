from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable
from typing import Generic, TypeVar

from orb.domain.base.clock import Clock, SystemClock
from orb.domain.base.ports.logging_port import LoggingPort

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")
DispatchT = TypeVar("DispatchT")

# Shared polling constants used by orchestrators that block-and-poll until a
# request reaches a terminal state.  Both spellings of "complete/completed" and
# "cancel/cancelled" are included because the HostFactory scheduler returns
# "completed" / "canceled" (past-tense) whereas the internal RequestStatus enum
# uses "complete" / "cancelled".  The defensive set keeps polling correct across
# both sources without adding a spelling-normalisation step to every caller.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "complete",
        "completed",
        "failed",
        "error",
        "cancelled",
        "canceled",
        "partial",
        "timeout",
    }
)
MAX_CONSECUTIVE_POLL_ERRORS: int = 3


class OrchestratorBase(ABC, Generic[InputT, OutputT]):
    """Base class for all interface-facing orchestrators.

    Orchestrators are the single source of truth for each operation.
    They dispatch CQRS commands/queries and return typed DTOs.
    They do NOT call SchedulerPort — formatting is the adapter's concern.
    They do NOT call get_container() — all deps are constructor-injected.
    """

    # Subclasses assign a LoggingPort to self._logger in their constructor.
    _logger: LoggingPort
    # Time source for wait loops; subclasses that poll accept ``clock`` in their
    # constructor and assign it here.
    _clock: Clock = SystemClock()

    @abstractmethod
    async def execute(self, input: InputT) -> OutputT:  # type: ignore[return]
        pass

    async def _dispatch(self, operation: str, awaitable: Awaitable[DispatchT]) -> DispatchT:
        """Await a command/query bus dispatch, logging then re-raising failures.

        Wraps a single ``command_bus.execute`` / ``query_bus.execute`` call so
        that every orchestrator surfaces failures the same way: log at error
        level with the operation label, then re-raise the *original* exception
        unchanged. Re-raising preserves the exception type so downstream REST
        exception mapping (``handle_rest_exceptions``) can translate it to the
        correct HTTP status.

        This is a thin, uniform seam — it does not swallow, wrap, or translate
        exceptions. Orchestrators that intentionally treat specific exceptions
        as non-fatal (e.g. ``EntityNotFoundError`` → ``None``) should catch
        those first and only route the unexpected ones through this helper.
        """
        try:
            return await awaitable
        except Exception as exc:
            self._logger.error("%s failed: %s", operation, exc)
            raise
