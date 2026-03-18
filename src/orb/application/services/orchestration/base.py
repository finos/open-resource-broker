from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class OrchestratorBase(ABC, Generic[InputT, OutputT]):
    """Base class for all interface-facing orchestrators.

    Orchestrators are the single source of truth for each operation.
    They dispatch CQRS commands/queries and return typed DTOs.
    They do NOT call SchedulerPort — formatting is the adapter's concern.
    They do NOT call get_container() — all deps are constructor-injected.
    """

    @abstractmethod
    async def execute(self, input: InputT) -> OutputT:  # type: ignore[return]
        pass
