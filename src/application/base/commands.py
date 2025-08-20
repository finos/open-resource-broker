"""Base command infrastructure - foundation for command processing."""

from typing import Protocol

from application.dto.base import BaseCommand, BaseResponse
from application.interfaces.command_handler import CommandHandler

__all__: list[str] = ["CommandHandler", "CommandBus"]


class CommandBus(Protocol):
    """Protocol for command bus."""

    async def execute(self, command: BaseCommand) -> BaseResponse:
        """Execute a command asynchronously for processing."""
        ...

    def register_handler(self, command_type: type, handler: CommandHandler) -> None:
        """Register a command handler for a specific command type."""
        ...
