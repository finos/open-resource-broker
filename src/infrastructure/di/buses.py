"""
Pure CQRS Bus Implementation.

This module provides clean QueryBus and CommandBus implementations that follow
SOLID principles and CQRS best practices:

- Single Responsibility: Pure routing only
- Open/Closed: Easy to add handlers without changing buses
- Dependency Inversion: No concrete middleware dependencies
- CQRS Purity: Thin buses, handlers own their concerns
- Clean Architecture: Appropriate layer separation

No middleware complexity - handlers own their cross-cutting concerns.
"""

from typing import TypeVar

from application.decorators import (
    get_command_handler_for_type,
    get_query_handler_for_type,
)
from application.interfaces.command_query import Command, Query
from domain.base.ports import LoggingPort
from infrastructure.di.container import DIContainer

TQuery = TypeVar("TQuery", bound=Query)
TCommand = TypeVar("TCommand", bound=Command)
TResult = TypeVar("TResult")


class QueryBus:
    """
    Pure CQRS Query Bus - Thin routing layer only.

    Follows SOLID principles:
    - SRP: Only routes queries to handlers
    - OCP: Easy to add handlers without changing bus
    - DIP: No concrete dependencies on middleware

    Handlers own their cross-cutting concerns (logging, validation, caching).
    """

    def __init__(self, container: DIContainer, logger: LoggingPort):
        """Initialize the instance."""
        self.container = container
        self.logger = logger

    async def execute(self, query: TQuery) -> TResult:
        """
        Execute a query through pure routing with lazy handler discovery.

        Handlers own all cross-cutting concerns:
        - Logging: BaseQueryHandler logs execution
        - Validation: Handlers validate their inputs
        - Caching: Handlers implement caching if needed
        - Error handling: Handlers manage their errors

        Args:
            query: Query to execute

        Returns:
            Query result from handler
        """
        try:
            # Pure routing - get handler and delegate
            handler_class = get_query_handler_for_type(type(query))
            handler = self.container.get(handler_class)
            return await handler.handle(query)

        except KeyError:
            # Try lazy CQRS setup if handler not found and lazy loading is enabled
            if self.container.is_lazy_loading_enabled():
                self.logger.debug(
                    f"Handler not found for query { type(query).__name__}, triggering lazy CQRS setup"
                )
                self._trigger_lazy_cqrs_setup()

                # Try again after lazy setup
                try:
                    handler_class = get_query_handler_for_type(type(query))
                    handler = self.container.get(handler_class)
                    return await handler.handle(query)
                except KeyError:
                    self.logger.error(
                        f"No handler registered for query: { type(query).__name__} (even after lazy setup)"
                    )
                    raise
            else:
                self.logger.error(f"No handler registered for query: {type(query).__name__}")
                raise
        except Exception as e:
            self.logger.error(f"Query execution failed: {str(e)}")
            raise

    def _trigger_lazy_cqrs_setup(self):
        """Trigger lazy CQRS infrastructure setup."""
        try:
            from infrastructure.di.container import _setup_cqrs_infrastructure

            self.logger.info("Triggering lazy CQRS infrastructure setup")
            _setup_cqrs_infrastructure(self.container)
        except Exception as e:
            self.logger.error(f"Failed to trigger lazy CQRS setup: {e}")


class CommandBus:
    """
    Pure CQRS Command Bus - Thin routing layer only.

    Follows SOLID principles:
    - SRP: Only routes commands to handlers
    - OCP: Easy to add handlers without changing bus
    - DIP: No concrete dependencies on middleware

    Handlers own their cross-cutting concerns (logging, validation, events).
    """

    def __init__(self, container: DIContainer, logger: LoggingPort):
        self.container = container
        self.logger = logger

    async def execute(self, command: TCommand) -> TResult:
        """
        Execute a command through pure routing with lazy handler discovery.

        Handlers own all cross-cutting concerns:
        - Logging: BaseCommandHandler logs execution
        - Validation: Handlers validate their inputs
        - Events: Handlers publish domain events
        - Transactions: Handlers manage their transactions

        Args:
            command: Command to execute

        Returns:
            Command result from handler
        """
        try:
            # Pure routing - get handler and delegate
            handler_class = get_command_handler_for_type(type(command))
            handler = self.container.get(handler_class)
            return await handler.handle(command)

        except KeyError:
            # Try lazy CQRS setup if handler not found and lazy loading is enabled
            if self.container.is_lazy_loading_enabled():
                self.logger.debug(
                    f"Handler not found for command { type(command).__name__}, triggering lazy CQRS setup"
                )
                self._trigger_lazy_cqrs_setup()

                # Try again after lazy setup
                try:
                    handler_class = get_command_handler_for_type(type(command))
                    handler = self.container.get(handler_class)
                    return await handler.handle(command)
                except KeyError:
                    self.logger.error(
                        f"No handler registered for command: { type(command).__name__} (even after lazy setup)"
                    )
                    raise
            else:
                self.logger.error(f"No handler registered for command: {type(command).__name__}")
                raise
        except Exception as e:
            self.logger.error(f"Command execution failed: {str(e)}")
            raise

    def _trigger_lazy_cqrs_setup(self):
        """Trigger lazy CQRS infrastructure setup."""
        try:
            from infrastructure.di.container import _setup_cqrs_infrastructure

            self.logger.info("Triggering lazy CQRS infrastructure setup")
            _setup_cqrs_infrastructure(self.container)
        except Exception as e:
            self.logger.error(f"Failed to trigger lazy CQRS setup: {e}")


class BusFactory:
    """Factory for creating clean, configured buses."""

    @staticmethod
    def create_query_bus(container: DIContainer, logger: LoggingPort) -> QueryBus:
        """Create a pure query bus."""
        return QueryBus(container, logger)

    @staticmethod
    def create_command_bus(container: DIContainer, logger: LoggingPort) -> CommandBus:
        """Create a pure command bus."""
        return CommandBus(container, logger)

    @staticmethod
    def create_buses(container: DIContainer, logger: LoggingPort) -> tuple[QueryBus, CommandBus]:
        """Create both query and command buses."""
        query_bus = BusFactory.create_query_bus(container, logger)
        command_bus = BusFactory.create_command_bus(container, logger)
        return query_bus, command_bus
