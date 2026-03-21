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

from typing import Any

from orb.application.decorators import (
    get_command_handler_for_type,
    get_query_handler_for_type,
)
from orb.application.interfaces.command_query import Command, Query
from orb.application.ports.command_bus_port import CommandBusPort
from orb.application.ports.query_bus_port import QueryBusPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.di.container import DIContainer


class QueryBus(QueryBusPort):
    """
    Pure CQRS Query Bus - Thin routing layer only.

    Implements QueryBusPort to satisfy Dependency Inversion Principle.

    Follows SOLID principles:
    - SRP: Only routes queries to handlers
    - OCP: Easy to add handlers without changing bus
    - DIP: Implements port interface, no concrete dependencies on middleware

    Handlers own their cross-cutting concerns (logging, validation, caching).
    """

    def __init__(self, container: DIContainer, logger: LoggingPort) -> None:
        """Initialize the instance."""
        self.container = container
        self.logger = logger

    async def execute(self, query: Query) -> Any:
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
                    "Handler not found for query %s, triggering lazy CQRS setup",
                    type(query).__name__,
                )
                self._trigger_lazy_cqrs_setup()

                # Try again after lazy setup
                try:
                    handler_class = get_query_handler_for_type(type(query))
                    handler = self.container.get(handler_class)
                    return await handler.handle(query)
                except KeyError:
                    self.logger.error(
                        "No handler registered for query: %s (even after lazy setup)",
                        type(query).__name__,
                    )
                    raise
            else:
                self.logger.error("No handler registered for query: %s", type(query).__name__)
                raise
        except Exception as e:
            self.logger.error("Query execution failed: %s", str(e))
            raise

    def execute_sync(self, query: Query) -> Any:
        """Execute query synchronously for sync contexts.

        Uses asyncio.run() when no event loop is running. When called from
        within a running loop (e.g. Jupyter, some test frameworks), raises
        RuntimeError with a clear message rather than deadlocking.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            raise RuntimeError(
                "execute_sync() called from within a running event loop. "
                "Use 'await execute()' instead, or run from a sync context."
            )

        return asyncio.run(self.execute(query))

    def _trigger_lazy_cqrs_setup(self) -> None:
        """Trigger lazy CQRS infrastructure setup."""
        try:
            from orb.bootstrap.services import setup_cqrs_infrastructure

            self.logger.info("Triggering lazy CQRS infrastructure setup")
            setup_cqrs_infrastructure(self.container)
        except Exception as e:
            self.logger.error("Failed to trigger lazy CQRS setup: %s", e)

    def register(self, query_type: type, handler: Any) -> None:
        """Register a query handler for a specific query type."""
        self.container.register_instance(type(handler), handler)


class CommandBus(CommandBusPort):
    """
    Pure CQRS Command Bus - Thin routing layer only.

    Implements CommandBusPort to satisfy Dependency Inversion Principle.

    Follows SOLID principles:
    - SRP: Only routes commands to handlers
    - OCP: Easy to add handlers without changing bus
    - DIP: Implements port interface, no concrete dependencies on middleware

    Handlers own their cross-cutting concerns (logging, validation, events).
    """

    def __init__(self, container: DIContainer, logger: LoggingPort) -> None:
        self.container = container
        self.logger = logger

    async def execute(self, command: Command) -> Any:
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
                    "Handler not found for command %s, triggering lazy CQRS setup",
                    type(command).__name__,
                )
                self._trigger_lazy_cqrs_setup()

                # Try again after lazy setup
                try:
                    handler_class = get_command_handler_for_type(type(command))
                    handler = self.container.get(handler_class)
                    return await handler.handle(command)
                except KeyError:
                    self.logger.error(
                        "No handler registered for command: %s (even after lazy setup)",
                        type(command).__name__,
                    )
                    raise
            else:
                self.logger.error("No handler registered for command: %s", type(command).__name__)
                raise
        except Exception as e:
            self.logger.error("Command execution failed: %s", str(e))
            raise

    def _trigger_lazy_cqrs_setup(self) -> None:
        """Trigger lazy CQRS infrastructure setup."""
        try:
            from orb.bootstrap.services import setup_cqrs_infrastructure

            self.logger.info("Triggering lazy CQRS infrastructure setup")
            setup_cqrs_infrastructure(self.container)
        except Exception as e:
            self.logger.error("Failed to trigger lazy CQRS setup: %s", e)

    def register(self, command_type: type, handler: Any) -> None:
        """Register a command handler for a specific command type."""
        self.container.register_instance(type(handler), handler)


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
