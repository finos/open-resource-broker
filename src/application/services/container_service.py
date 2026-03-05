"""Container service for application layer DI container access."""

from typing import Any, Optional

from domain.base.ports.container_port import ContainerPort
from domain.base.ports.logging_port import LoggingPort


class ContainerService:
    """Application service interface for DI container access."""

    def __init__(self, container: ContainerPort, logger: LoggingPort):
        self._container = container
        self._logger = logger

    def get_service(self, service_type: type) -> Any:
        """Get service from DI container."""
        return self._container.get(service_type)

    def get_optional_service(self, service_type: type) -> Optional[Any]:
        """Get optional service from DI container."""
        if self._container.has(service_type):
            return self._container.get(service_type)
        return None
