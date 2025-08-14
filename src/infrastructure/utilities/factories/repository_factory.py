"""Repository factory using storage registry pattern.

This factory creates repositories using the storage registry pattern,
maintaining clean separation of concerns:
- Storage Registry: Handles storage strategies only
- Repository Factory: Creates repositories + injects strategies
- Clean Architecture: No repository knowledge in storage layer
"""

from typing import Any

from src.config.manager import ConfigurationManager
from src.domain.base import UnitOfWorkFactory as AbstractUnitOfWorkFactory
from src.domain.base.dependency_injection import injectable
from src.domain.base.domain_interfaces import UnitOfWork
from src.domain.base.ports import LoggingPort

# Import repository interfaces
from src.domain.machine.repository import (
    MachineRepository as MachineRepositoryInterface,
)
from src.domain.request.repository import (
    RequestRepository as RequestRepositoryInterface,
)
from src.domain.template.repository import (
    TemplateRepository as TemplateRepositoryInterface,
)
from src.infrastructure.registry.storage_registry import get_storage_registry


@injectable
class RepositoryFactory:
    """Factory for creating repositories using storage registry pattern."""

    def __init__(self, config_manager: ConfigurationManager, logger: LoggingPort):
        """Initialize factory with configuration."""
        self.config_manager = config_manager
        self.logger = logger
        self._storage_registry = None

    @property
    def storage_registry(self):
        """Lazy load storage registry."""
        if self._storage_registry is None:
            self._storage_registry = get_storage_registry()
        return self._storage_registry

    def create_machine_repository(self) -> MachineRepositoryInterface:
        """Create machine repository with injected storage strategy."""
        from src.infrastructure.persistence.repositories.machine_repository import (
            MachineRepositoryImpl as MachineRepository,
        )

        storage_type = self.config_manager.get_storage_strategy()
        config = self.config_manager.get_app_config()

        try:
            # Get storage strategy from registry
            storage_strategy = self.storage_registry.create_strategy(storage_type, config)

            # Create repository with strategy injection
            return MachineRepository(storage_strategy)

        except Exception as e:
            self.logger.error(f"Failed to create machine repository: {e}")
            raise

    def create_request_repository(self) -> RequestRepositoryInterface:
        """Create request repository with injected storage strategy."""
        from src.infrastructure.persistence.repositories.request_repository import (
            RequestRepositoryImpl as RequestRepository,
        )

        storage_type = self.config_manager.get_storage_strategy()
        config = self.config_manager.get_app_config()

        try:
            # Get storage strategy from registry
            storage_strategy = self.storage_registry.create_strategy(storage_type, config)

            # Create repository with strategy injection
            return RequestRepository(storage_strategy)

        except Exception as e:
            self.logger.error(f"Failed to create request repository: {e}")
            raise

    def create_template_repository(self) -> TemplateRepositoryInterface:
        """Create template repository with injected storage strategy."""
        from src.infrastructure.persistence.repositories.template_repository import (
            TemplateRepositoryImpl as TemplateRepository,
        )

        storage_type = self.config_manager.get_storage_strategy()
        config = self.config_manager.get_app_config()

        try:
            # Get storage strategy from registry
            storage_strategy = self.storage_registry.create_strategy(storage_type, config)

            # Create repository with strategy injection
            return TemplateRepository(storage_strategy)

        except Exception as e:
            self.logger.error(f"Failed to create template repository: {e}")
            raise

    def create_unit_of_work(self) -> UnitOfWork:
        """Create unit of work using storage registry."""
        storage_type = self.config_manager.get_storage_strategy()

        try:
            # Use storage registry to create unit of work
            return self.storage_registry.create_unit_of_work(storage_type)

        except Exception as e:
            self.logger.error(f"Failed to create unit of work: {e}")
            raise


@injectable
class UnitOfWorkFactory(AbstractUnitOfWorkFactory):
    """Factory for creating unit of work instances."""

    def __init__(self, config_manager: ConfigurationManager, logger: LoggingPort):
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


@injectable
class RepositoryFactoryWithStrategies:
    """
    Repository factory that provides repositories with caching.

    This class creates repositories once and caches them for reuse.
    """

    def __init__(self, config_manager: ConfigurationManager, logger: LoggingPort):
        """Initialize factory with optional configuration manager."""
        self.logger = logger
        self._repositories = {}
        self._config_manager = config_manager

    def _get_config_manager(self):
        """Get configuration manager."""
        if self._config_manager is None:
            from src.config.manager import get_config_manager

            self._config_manager = get_config_manager()
        return self._config_manager

    def get_machine_repository(self) -> MachineRepositoryInterface:
        """Get machine repository (cached)."""
        if "machine" not in self._repositories:
            config_manager = self._get_config_manager()
            factory = RepositoryFactory(config_manager, self.logger)
            self._repositories["machine"] = factory.create_machine_repository()
        return self._repositories["machine"]

    def get_request_repository(self) -> RequestRepositoryInterface:
        """Get request repository (cached)."""
        if "request" not in self._repositories:
            config_manager = self._get_config_manager()
            factory = RepositoryFactory(config_manager, self.logger)
            self._repositories["request"] = factory.create_request_repository()
        return self._repositories["request"]

    def get_template_repository(self) -> TemplateRepositoryInterface:
        """Get template repository (cached)."""
        if "template" not in self._repositories:
            config_manager = self._get_config_manager()
            factory = RepositoryFactory(config_manager, self.logger)
            self._repositories["template"] = factory.create_template_repository()
        return self._repositories["template"]

    def get_repository(self, repository_interface: type) -> Any:
        """Get repository by interface type."""
        # Map interface types to repository names
        if repository_interface == MachineRepositoryInterface:
            return self.get_machine_repository()
        elif repository_interface == RequestRepositoryInterface:
            return self.get_request_repository()
        elif repository_interface == TemplateRepositoryInterface:
            return self.get_template_repository()
        else:
            raise ValueError(f"Unknown repository interface: {repository_interface}")
