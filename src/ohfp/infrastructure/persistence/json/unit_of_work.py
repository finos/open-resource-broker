"""JSON Unit of Work implementation using simplified repositories."""

import os
from pathlib import Path
from typing import Optional

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.base.unit_of_work import BaseUnitOfWork

# Import JSON storage strategy
from infrastructure.persistence.json.strategy import JSONStorageStrategy

# Import new simplified repositories
from infrastructure.persistence.repositories.machine_repository import (
    MachineRepositoryImpl as MachineRepository,
)
from infrastructure.persistence.repositories.request_repository import (
    RequestRepositoryImpl as RequestRepository,
)
from infrastructure.persistence.repositories.template_repository import (
    TemplateRepositoryImpl as TemplateRepository,
)


class JSONUnitOfWork(BaseUnitOfWork):
    """JSON-based unit of work implementation using simplified repositories."""

    def __init__(
        self,
        data_dir: str,
        machine_file: str = "machines.json",
        request_file: str = "requests.json",
        template_file: str = "templates.json",
        legacy_template_file: Optional[str] = None,
        create_dirs: bool = True,
    ) -> None:
        """
        Initialize JSON unit of work with simplified repositories.

        Args:
            data_dir: Directory for JSON files
            machine_file: Machine data file name
            request_file: Request data file name
            template_file: Template data file name
            legacy_template_file: Legacy template file (optional)
            create_dirs: Whether to create directories
        """
        super().__init__()

        self.logger = get_logger(__name__)

        # Ensure data directory exists
        data_path = Path(data_dir)
        if create_dirs and not data_path.exists():
            data_path.mkdir(parents=True, exist_ok=True)
            self.logger.info("Created data directory: %s", data_dir)

        # Create storage strategies for each repository
        machine_strategy = JSONStorageStrategy(
            file_path=os.path.join(data_dir, machine_file),
            create_dirs=create_dirs,
            entity_type="machines",
        )

        request_strategy = JSONStorageStrategy(
            file_path=os.path.join(data_dir, request_file),
            create_dirs=create_dirs,
            entity_type="requests",
        )

        template_path = (
            template_file if os.path.isabs(template_file) else os.path.join(data_dir, template_file)
        )
        template_strategy = JSONStorageStrategy(
            file_path=template_path, create_dirs=create_dirs, entity_type="templates"
        )

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        self.logger.debug(
            "Initialized JSONUnitOfWork with simplified repositories in: %s", data_dir
        )

    @property
    def machines(self):
        """Get machine repository."""
        return self.machine_repository

    @property
    def requests(self):
        """Get request repository."""
        return self.request_repository

    @property
    def templates(self):
        """Get template repository."""
        return self.template_repository

    def _begin_transaction(self) -> None:
        """Begin transaction on all storage strategies."""
        try:
            self.machine_repository.storage_port.begin_transaction()
            self.request_repository.storage_port.begin_transaction()
            self.template_repository.storage_strategy.begin_transaction()
            self.logger.debug("Transaction begun on all repositories")
        except Exception as e:
            self.logger.error("Failed to begin transaction: %s", e)
            raise

    def _commit_transaction(self) -> None:
        """Commit transaction on all storage strategies."""
        try:
            self.machine_repository.storage_port.commit_transaction()
            self.request_repository.storage_port.commit_transaction()
            self.template_repository.storage_strategy.commit_transaction()
            self.logger.debug("Transaction committed on all repositories")
        except Exception as e:
            self.logger.error("Failed to commit transaction: %s", e)
            raise

    def _rollback_transaction(self) -> None:
        """Rollback transaction on all storage strategies."""
        try:
            self.machine_repository.storage_port.rollback_transaction()
            self.request_repository.storage_port.rollback_transaction()
            self.template_repository.storage_strategy.rollback_transaction()
            self.logger.debug("Transaction rolled back on all repositories")
        except Exception as e:
            self.logger.error("Failed to rollback transaction: %s", e)
            raise
