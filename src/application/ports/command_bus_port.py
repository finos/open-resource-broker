"""Command bus port interface."""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from application.dto.commands import Command

TCommand = TypeVar("TCommand", bound=Command)
TResult = TypeVar("TResult")


class CommandBusPort(ABC):
    """Port interface for command bus operations.
    
    This port defines the contract for executing commands in the application layer.
    Infrastructure adapters must implement this interface to provide command execution.
    """

    @abstractmethod
    async def execute(self, command: TCommand) -> TResult:
        """Execute a command and return the result.
        
        Args:
            command: The command to execute
            
        Returns:
            The command result (should be void/acknowledgment for CQRS compliance)
            
        Raises:
            CommandExecutionError: If command execution fails
        """
        ...
