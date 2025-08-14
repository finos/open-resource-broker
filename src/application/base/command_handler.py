"""Command handler base classes for the entire application.

This module provides the complete command handler hierarchy, building on the
CQRS interface to provide concrete implementations for different layers.

Architecture:
- CommandHandler: Abstract CQRS interface (from interfaces/command_handler.py)
- ApplicationCommandHandler: For application layer CQRS handlers
- CLICommandHandler: For CLI interface handlers
"""

from typing import Any, Dict, Optional, TypeVar

# Import the CQRS interface
from src.application.interfaces.command_handler import CommandHandler

# Type variables for generic command handlers
TCommand = TypeVar("TCommand")
TResponse = TypeVar("TResponse")


class ApplicationCommandHandler(CommandHandler[TCommand, TResponse]):
    """
    Base class for application layer CQRS command handlers.

    This class provides common functionality for application layer handlers
    that implement business logic and coordinate between domain and infrastructure.

    Application handlers are responsible for:
    - Orchestrating business operations
    - Managing transactions
    - Publishing domain events
    - Coordinating between bounded contexts
    """

    def __init__(
        self,
        logger: Optional[Any] = None,
        metrics: Optional[Any] = None,
        event_publisher: Optional[Any] = None,
    ):
        """
        Initialize application command handler.

        Args:
            logger: Optional logger instance
            metrics: Optional metrics collector
            event_publisher: Optional domain event publisher
        """
        self.logger = logger
        self.metrics = metrics
        self.event_publisher = event_publisher

    def _publish_event(self, event: Any) -> None:
        """Publish domain event if publisher available."""
        if self.event_publisher:
            self.event_publisher.publish(event)

    def _log_info(self, message: str, **kwargs) -> None:
        """Log info message if logger available."""
        if self.logger:
            self.logger.info(message, **kwargs)

    def _log_error(self, message: str, **kwargs) -> None:
        """Log error message if logger available."""
        if self.logger:
            self.logger.error(message, **kwargs)

    def _record_metric(self, metric_name: str, value: Any, **tags) -> None:
        """Record metric if metrics collector available."""
        if self.metrics:
            self.metrics.record(metric_name, value, **tags)


class CLICommandHandler(CommandHandler[TCommand, TResponse]):
    """
    Base class for CLI interface command handlers.

    This class provides common functionality for CLI handlers that interact
    with the application layer through CQRS buses.

    CLI handlers are responsible for:
    - Processing CLI input/output
    - Dispatching commands to application layer
    - Formatting responses for CLI display
    - Handling CLI-specific errors
    """

    def __init__(
        self,
        query_bus: Optional[Any] = None,
        command_bus: Optional[Any] = None,
        logger: Optional[Any] = None,
        metrics: Optional[Any] = None,
    ):
        """
        Initialize CLI command handler.

        Args:
            query_bus: Query bus for CQRS queries
            command_bus: Command bus for CQRS commands
            logger: Optional logger instance
            metrics: Optional metrics collector
        """
        self.logger = logger
        self.metrics = metrics
        self._query_bus = query_bus
        self._command_bus = command_bus

        # Validate required dependencies for CLI handlers
        if not query_bus:
            raise ValueError("QueryBus is required for CLI command handlers")
        if not command_bus:
            raise ValueError("CommandBus is required for CLI command handlers")

    def process_input(self, command) -> Optional[Dict[str, Any]]:
        """
        Process input from CLI arguments, files, or data strings.

        This method provides common input processing functionality
        for CLI handlers.

        Args:
            command: Command object or arguments

        Returns:
            Parsed input data or None
        """
        import json

        input_data = None

        # Process input data from file or direct JSON string
        if hasattr(command, "file") and command.file:
            try:
                with open(command.file) as f:
                    input_data = json.load(f)
                if self.logger:
                    self.logger.debug(f"Loaded input from file: {command.file}")
            except Exception as e:
                if self.logger:
                    self.logger.error(
                        f"Failed to load input from file {command.file}: {e}")
                raise
        elif hasattr(command, "data") and command.data:
            try:
                input_data = json.loads(command.data)
                if self.logger:
                    self.logger.debug("Loaded input from data string")
            except json.JSONDecodeError as e:
                if self.logger:
                    self.logger.error(f"Failed to parse JSON data: {e}")
                raise

        return input_data

    def format_output(self, response: TResponse) -> str:
        """
        Format response for CLI display.

        Args:
            response: Handler response

        Returns:
            Formatted string for CLI output
        """
        # Default JSON formatting - can be overridden by specific handlers
        import json

        if hasattr(response, "__dict__"):
            return json.dumps(response.__dict__, indent=2, default=str)
        else:
            return str(response)

    def _log_info(self, message: str, **kwargs) -> None:
        """Log info message if logger available."""
        if self.logger:
            self.logger.info(message, **kwargs)

    def _log_error(self, message: str, **kwargs) -> None:
        """Log error message if logger available."""
        if self.logger:
            self.logger.error(message, **kwargs)

    def _record_metric(self, metric_name: str, value: Any, **tags) -> None:
        """Record metric if metrics collector available."""
        if self.metrics:
            self.metrics.record(metric_name, value, **tags)
