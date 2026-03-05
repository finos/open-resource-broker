"""Query handler service registrations for dependency injection.

All query handlers are now automatically discovered and registered via
@query_handler decorators through the Handler Discovery System.
"""

from domain.base.ports.logging_port import LoggingPort
from infrastructure.di.buses import QueryBus
from infrastructure.di.container import DIContainer


def register_query_handler_services(container: DIContainer) -> None:
    """Register query handler services."""

    # Register template query handlers
    _register_template_query_handlers(container)

    # Register request query handlers
    _register_request_query_handlers(container)

    # Register machine query handlers
    _register_machine_query_handlers(container)

    # Register system query handlers
    _register_system_query_handlers(container)

    # Register specialized query handlers
    _register_specialized_query_handlers(container)


def _register_template_query_handlers(container: DIContainer) -> None:
    """Register template-related query handlers."""

    # All template query handlers are now automatically discovered and registered
    # via @query_handler decorators through the Handler Discovery System


def _register_request_query_handlers(container: DIContainer) -> None:
    """Register request-related query handlers."""

    # All request query handlers are now automatically discovered and registered
    # via @query_handler decorators through the Handler Discovery System


def _register_machine_query_handlers(container: DIContainer) -> None:
    """Register machine-related query handlers."""

    # All machine query handlers are now automatically discovered and registered
    # via @query_handler decorators through the Handler Discovery System


def _register_system_query_handlers(container: DIContainer) -> None:
    """Register system-related query handlers."""

    # All system query handlers are now automatically discovered and registered
    # via @query_handler decorators through the Handler Discovery System


def _register_specialized_query_handlers(container: DIContainer) -> None:
    """Register specialized query handlers."""

    # All specialized query handlers are now automatically discovered and registered
    # via @query_handler decorators through the Handler Discovery System


def register_query_handlers_with_bus(container: DIContainer) -> None:
    """Register query handlers with the query bus."""

    try:
        query_bus = container.get(QueryBus)
        logger = container.get(LoggingPort)

        # Register template query handlers
        try:
            from application.dto.queries import (
                GetTemplateQuery,
                ListTemplatesQuery,
                ValidateTemplateQuery,
            )
            from application.queries.template_query_handlers import (
                GetTemplateHandler,
                ListTemplatesHandler,
                ValidateTemplateHandler,
            )

            query_bus.register(GetTemplateQuery, container.get(GetTemplateHandler))
            query_bus.register(ListTemplatesQuery, container.get(ListTemplatesHandler))
            query_bus.register(ValidateTemplateQuery, container.get(ValidateTemplateHandler))

        except ImportError as e:
            logger.debug("Template query handlers not available for bus registration: %s", e)

        # Register request query handlers
        try:
            from application.dto.queries import (
                GetRequestQuery,
                ListActiveRequestsQuery,
                ListReturnRequestsQuery,
            )
            from application.queries.request_query_handlers import (
                GetRequestHandler,
                ListActiveRequestsHandler,
                ListReturnRequestsHandler,
            )

            query_bus.register(GetRequestQuery, container.get(GetRequestHandler))
            query_bus.register(ListActiveRequestsQuery, container.get(ListActiveRequestsHandler))
            query_bus.register(ListReturnRequestsQuery, container.get(ListReturnRequestsHandler))

        except ImportError as e:
            logger.debug("Request query handlers not available for bus registration: %s", e)

        # Register machine query handlers
        try:
            from application.dto.queries import GetMachineQuery, ListMachinesQuery
            from application.queries.machine_query_handlers import (
                GetMachineHandler,
                ListMachinesHandler,
            )

            query_bus.register(GetMachineQuery, container.get(GetMachineHandler))
            query_bus.register(ListMachinesQuery, container.get(ListMachinesHandler))

            from application.machine.queries import (
                ConvertBatchMachineStatusQuery,
                ConvertMachineStatusQuery,
                ValidateProviderStateQuery,
            )
            from application.queries.machine_query_handlers import (
                ConvertBatchMachineStatusQueryHandler,
                ConvertMachineStatusQueryHandler,
                ValidateProviderStateQueryHandler,
            )

            query_bus.register(
                ConvertMachineStatusQuery, container.get(ConvertMachineStatusQueryHandler)
            )
            query_bus.register(
                ConvertBatchMachineStatusQuery, container.get(ConvertBatchMachineStatusQueryHandler)
            )
            query_bus.register(
                ValidateProviderStateQuery, container.get(ValidateProviderStateQueryHandler)
            )

        except ImportError as e:
            logger.debug("Machine query handlers not available for bus registration: %s", e)

        # Register system query handlers
        try:
            from application.provider.queries import GetProviderMetricsQuery
            from application.queries.provider_handlers import GetProviderMetricsHandler
            from application.queries.system import (
                GetProviderConfigQuery,
                GetSystemStatusQuery,
                ValidateProviderConfigQuery,
            )
            from application.queries.system_handlers import (
                GetProviderConfigHandler,
                GetSystemStatusHandler,
                ValidateProviderConfigHandler,
            )

            query_bus.register(GetProviderConfigQuery, container.get(GetProviderConfigHandler))
            query_bus.register(
                ValidateProviderConfigQuery,
                container.get(ValidateProviderConfigHandler),
            )
            query_bus.register(GetSystemStatusQuery, container.get(GetSystemStatusHandler))
            query_bus.register(GetProviderMetricsQuery, container.get(GetProviderMetricsHandler))

        except ImportError as e:
            logger.debug("System query handlers not available for bus registration: %s", e)

        # Register specialized query handlers
        try:
            from application.dto.queries import (
                GetActiveMachineCountQuery,
                GetMachineHealthQuery,
                GetRequestSummaryQuery,
            )
            from application.queries.specialized_handlers import (
                GetActiveMachineCountHandler,
                GetMachineHealthHandler,
                GetRequestSummaryHandler,
            )

            query_bus.register(
                GetActiveMachineCountQuery, container.get(GetActiveMachineCountHandler)
            )
            query_bus.register(GetRequestSummaryQuery, container.get(GetRequestSummaryHandler))
            query_bus.register(GetMachineHealthQuery, container.get(GetMachineHealthHandler))

        except ImportError as e:
            logger.debug("Specialized query handlers not available for bus registration: %s", e)

        logger.info("Query handlers registered with query bus successfully")

    except Exception as e:
        logger = container.get(LoggingPort)
        logger.error("Failed to register query handlers with bus: %s", e, exc_info=True)
        raise
