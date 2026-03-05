"""Bulk query handlers for CQRS compliance."""

from application.base.handlers import BaseQueryHandler
from application.decorators import query_handler
from application.dto.bulk_queries import (
    GetMultipleMachinesQuery,
    GetMultipleRequestsQuery,
    GetMultipleTemplatesQuery,
)
from application.dto.bulk_responses import (
    BulkMachineResponse,
    BulkRequestResponse,
    BulkTemplateResponse,
)
from domain.base import UnitOfWorkFactory
from domain.base.exceptions import EntityNotFoundError
from domain.base.ports import ContainerPort, ErrorHandlingPort, LoggingPort


@query_handler(GetMultipleRequestsQuery)
class GetMultipleRequestsHandler(BaseQueryHandler[GetMultipleRequestsQuery, BulkRequestResponse]):
    """Handler for bulk request retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container

        from application.factories.request_dto_factory import RequestDTOFactory
        from application.services.request_query_service import RequestQueryService

        self._query_service = RequestQueryService(uow_factory, logger)
        self._dto_factory = RequestDTOFactory()

    async def execute_query(self, query: GetMultipleRequestsQuery) -> BulkRequestResponse:
        """Execute bulk request retrieval."""
        requests = []
        not_found_ids = []

        for request_id in query.request_ids:
            try:
                request = await self._query_service.get_request(request_id)
                machines = []
                if query.include_machines:
                    machines = await self._query_service.get_machines_for_request(request)

                request_dto = self._dto_factory.create_from_domain(request, machines)
                requests.append(request_dto)
            except EntityNotFoundError:
                not_found_ids.append(request_id)

        return BulkRequestResponse(
            requests=requests,
            found_count=len(requests),
            not_found_ids=not_found_ids,
            total_requested=len(query.request_ids),
        )


@query_handler(GetMultipleTemplatesQuery)
class GetMultipleTemplatesHandler(
    BaseQueryHandler[GetMultipleTemplatesQuery, BulkTemplateResponse]
):
    """Handler for bulk template retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container

        from application.factories.template_dto_factory import (  # type: ignore
            TemplateDTOFactory,  # type: ignore[import]
        )
        from application.services.template_query_service import (  # type: ignore
            TemplateQueryService,  # type: ignore[import]
        )

        self._query_service = TemplateQueryService(uow_factory, logger)
        self._dto_factory = TemplateDTOFactory()

    async def execute_query(self, query: GetMultipleTemplatesQuery) -> BulkTemplateResponse:
        """Execute bulk template retrieval."""
        templates = []
        not_found_ids = []

        for template_id in query.template_ids:
            try:
                template = await self._query_service.get_template(template_id)
                if query.active_only and not template.active:
                    not_found_ids.append(template_id)
                    continue

                template_dto = self._dto_factory.create_from_domain(template)
                templates.append(template_dto)
            except EntityNotFoundError:
                not_found_ids.append(template_id)

        return BulkTemplateResponse(
            templates=templates,
            found_count=len(templates),
            not_found_ids=not_found_ids,
            total_requested=len(query.template_ids),
        )


@query_handler(GetMultipleMachinesQuery)
class GetMultipleMachinesHandler(BaseQueryHandler[GetMultipleMachinesQuery, BulkMachineResponse]):
    """Handler for bulk machine retrieval."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        logger: LoggingPort,
        error_handler: ErrorHandlingPort,
        container: ContainerPort,
    ) -> None:
        super().__init__(logger, error_handler)
        self.uow_factory = uow_factory
        self._container = container

        from application.factories.machine_dto_factory import (  # type: ignore
            MachineDTOFactory,  # type: ignore[import]
        )
        from application.services.machine_query_service import (  # type: ignore
            MachineQueryService,  # type: ignore[import]
        )

        self._query_service = MachineQueryService(uow_factory, logger)
        self._dto_factory = MachineDTOFactory()

    async def execute_query(self, query: GetMultipleMachinesQuery) -> BulkMachineResponse:
        """Execute bulk machine retrieval."""
        machines = []
        not_found_ids = []

        for machine_id in query.machine_ids:
            try:
                machine = await self._query_service.get_machine(machine_id)
                request = None
                if query.include_requests and machine.request_id:
                    from application.services.request_query_service import RequestQueryService

                    request_service = RequestQueryService(self.uow_factory, self.logger)
                    request = await request_service.get_request(machine.request_id)

                machine_dto = self._dto_factory.create_from_domain(machine, request)
                machines.append(machine_dto)
            except EntityNotFoundError:
                not_found_ids.append(machine_id)

        return BulkMachineResponse(
            machines=machines,
            found_count=len(machines),
            not_found_ids=not_found_ids,
            total_requested=len(query.machine_ids),
        )
