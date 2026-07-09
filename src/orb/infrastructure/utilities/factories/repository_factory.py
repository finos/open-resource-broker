"""Repository factory using storage registry pattern.

This factory creates repositories using the storage registry pattern,
maintaining clean separation of concerns:
- Storage Registry: Handles storage strategies only
- Repository Factory: Creates repositories + injects strategies
- Clean Architecture: No repository knowledge in storage layer
"""

from typing import Optional

from orb.application.events.bus.event_bus import EventBus
from orb.config.manager import ConfigurationManager
from orb.domain.base import UnitOfWorkFactory as AbstractUnitOfWorkFactory
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.domain_interfaces import UnitOfWork
from orb.domain.base.ports import LoggingPort

# Import repository interfaces
from orb.domain.machine.repository import MachineRepository as MachineRepositoryInterface
from orb.domain.request.repository import RequestRepository as RequestRepositoryInterface
from orb.domain.template.repository import TemplateRepository as TemplateRepositoryInterface
from orb.infrastructure.storage.registry import get_storage_registry


@injectable
class RepositoryFactory:
    """Factory for creating repositories using storage registry pattern."""

    def __init__(
        self,
        config_manager: ConfigurationManager,
        logger: LoggingPort,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        """Initialize factory with configuration."""
        self.config_manager = config_manager
        self.logger = logger
        self.event_bus = event_bus
        self._storage_registry = None

    @property
    def storage_registry(self):
        """Lazy load storage registry."""
        if self._storage_registry is None:
            self._storage_registry = get_storage_registry()
        return self._storage_registry

    def create_machine_repository(self) -> MachineRepositoryInterface:
        """Create machine repository with injected storage strategy."""
        from orb.infrastructure.storage.repositories.machine_repository import (
            MachineRepositoryImpl as MachineRepository,
        )

        try:
            storage_type = self.config_manager.get_storage_strategy()
            config = self.config_manager.app_config.model_dump()
            storage_strategy = self.storage_registry.create_strategy(storage_type, config)
            return MachineRepository(storage_strategy)

        except Exception as e:
            self.logger.error("Failed to create machine repository: %s", e)
            raise

    def create_request_repository(self) -> RequestRepositoryInterface:
        """Create request repository with injected storage strategy."""
        from orb.infrastructure.storage.repositories.request_repository import (
            RequestRepositoryImpl as RequestRepository,
        )

        try:
            storage_type = self.config_manager.get_storage_strategy()
            config = self.config_manager.app_config.model_dump()
            storage_strategy = self.storage_registry.create_strategy(storage_type, config)
            return RequestRepository(storage_strategy, event_publisher=self.event_bus)

        except Exception as e:
            self.logger.error("Failed to create request repository: %s", e)
            raise

    def create_template_repository(self) -> TemplateRepositoryInterface:
        """Create template repository with injected storage strategy."""
        from orb.infrastructure.storage.repositories.template_repository import (
            TemplateRepositoryImpl as TemplateRepository,
        )

        storage_type = self.config_manager.get_storage_strategy()
        config = self.config_manager.app_config.model_dump()
        storage_strategy = self.storage_registry.create_strategy(storage_type, config)

        try:
            # Create repository with strategy injection
            return TemplateRepository(storage_strategy)

        except Exception as e:
            self.logger.error("Failed to create template repository: %s", e)
            raise

    def create_unit_of_work(self) -> UnitOfWork:
        """Create unit of work using storage registry."""
        storage_type = self.config_manager.get_storage_strategy()

        try:
            # Use storage registry to create unit of work with config
            return self.storage_registry.create_unit_of_work(storage_type, self.config_manager)  # type: ignore[return-value]

        except Exception as e:
            self.logger.error("Failed to create unit of work: %s", e)
            raise


@injectable
class UnitOfWorkFactory(AbstractUnitOfWorkFactory):
    """Factory for creating unit of work instances."""

    def __init__(self, config_manager: ConfigurationManager, logger: LoggingPort) -> None:
        """Initialize factory with configuration."""
        self.config_manager = config_manager
        self.logger = logger

    @property
    def repository_factory(self):
        """Get repository factory instance."""
        return RepositoryFactory(self.config_manager, self.logger)

    def create(self) -> UnitOfWork:
        """Create unit of work instance."""
        repository_factory = RepositoryFactory(self.config_manager, self.logger)
        return repository_factory.create_unit_of_work()

    def create_unit_of_work(self) -> UnitOfWork:
        """Create unit of work instance (abstract interface implementation)."""
        return self.create()
