"""SQL Unit of Work implementation using simplified repositories."""

from typing import Dict, Optional

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.base.unit_of_work import BaseUnitOfWork

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

# Import SQL storage strategy
from infrastructure.persistence.sql.strategy import SQLStorageStrategy


class SQLUnitOfWork(BaseUnitOfWork):
    """SQL-based unit of work implementation using simplified repositories."""

    def __init__(self, engine: Engine) -> None:
        """
        Initialize SQL unit of work with simplified repositories.

        Args:
            engine: SQLAlchemy engine
        """
        super().__init__()

        self.logger = get_logger(__name__)
        self.engine = engine
        self.session: Optional[Session] = None

        # Create storage strategies for each repository
        machine_strategy = SQLStorageStrategy(
            config=None,  # Will be set from engine
            table_name="machines",
            columns=self._get_machine_columns(),
        )
        machine_strategy.engine = engine

        request_strategy = SQLStorageStrategy(
            config=None,  # Will be set from engine
            table_name="requests",
            columns=self._get_request_columns(),
        )
        request_strategy.engine = engine

        template_strategy = SQLStorageStrategy(
            config=None,  # Will be set from engine
            table_name="templates",
            columns=self._get_template_columns(),
        )
        template_strategy.engine = engine

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        self.logger.debug("Initialized SQLUnitOfWork with simplified repositories")

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

    def _get_machine_columns(self) -> Dict[str, str]:
        """Get machine table column definitions."""
        return {
            "machine_id": "VARCHAR(255) PRIMARY KEY",
            "instance_id": "VARCHAR(255)",
            "template_id": "VARCHAR(255)",
            "request_id": "VARCHAR(255)",
            "status": "VARCHAR(50)",
            "instance_type": "VARCHAR(50)",
            "availability_zone": "VARCHAR(50)",
            "private_ip": "VARCHAR(45)",
            "public_ip": "VARCHAR(45)",
            "launch_time": "TIMESTAMP",
            "termination_time": "TIMESTAMP",
            "tags": "TEXT",
            "metadata": "TEXT",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
        }

    def _get_request_columns(self) -> Dict[str, str]:
        """Get request table column definitions."""
        return {
            "request_id": "VARCHAR(255) PRIMARY KEY",
            "template_id": "VARCHAR(255)",
            "machine_count": "INTEGER",
            "request_type": "VARCHAR(50)",
            "status": "VARCHAR(50)",
            "machine_ids": "TEXT",
            "timeout": "INTEGER",
            "tags": "TEXT",
            "metadata": "TEXT",
            "error_message": "TEXT",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
            "completed_at": "TIMESTAMP",
        }

    def _get_template_columns(self) -> Dict[str, str]:
        """Get template table column definitions."""
        return {
            "template_id": "VARCHAR(255) PRIMARY KEY",
            "name": "VARCHAR(255)",
            "description": "TEXT",
            "image_id": "VARCHAR(255)",
            "instance_type": "VARCHAR(50)",
            "key_name": "VARCHAR(255)",
            "security_group_ids": "TEXT",
            "subnet_ids": "TEXT",
            "user_data": "TEXT",
            "tags": "TEXT",
            "metadata": "TEXT",
            "provider_api": "VARCHAR(255)",
            "is_active": "BOOLEAN",
            "created_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP",
        }

    def _begin_transaction(self) -> None:
        """Begin SQL transaction."""
        try:
            self.session = Session(self.engine)

            # Set session on all storage strategies
            self.machine_repository.storage_strategy.session = self.session
            self.request_repository.storage_strategy.session = self.session
            self.template_repository.storage_strategy.session = self.session

            # Begin transaction on storage strategies
            self.machine_repository.storage_strategy.begin_transaction()
            self.request_repository.storage_strategy.begin_transaction()
            self.template_repository.storage_strategy.begin_transaction()

            self.logger.debug("SQL transaction begun on all repositories")
        except Exception as e:
            self.logger.error("Failed to begin SQL transaction: %s", e)
            if self.session:
                self.session.close()
                self.session = None
            raise

    def _commit_transaction(self) -> None:
        """Commit SQL transaction."""
        try:
            if self.session:
                # Commit transaction on storage strategies
                self.machine_repository.storage_strategy.commit_transaction()
                self.request_repository.storage_strategy.commit_transaction()
                self.template_repository.storage_strategy.commit_transaction()

                self.session.commit()
                self.logger.debug("SQL transaction committed on all repositories")
        except Exception as e:
            self.logger.error("Failed to commit SQL transaction: %s", e)
            raise
        finally:
            if self.session:
                self.session.close()
                self.session = None

    def _rollback_transaction(self) -> None:
        """Rollback SQL transaction."""
        try:
            if self.session:
                # Rollback transaction on storage strategies
                self.machine_repository.storage_strategy.rollback_transaction()
                self.request_repository.storage_strategy.rollback_transaction()
                self.template_repository.storage_strategy.rollback_transaction()

                self.session.rollback()
                self.logger.debug("SQL transaction rolled back on all repositories")
        except Exception as e:
            self.logger.error("Failed to rollback SQL transaction: %s", e)
            raise
        finally:
            if self.session:
                self.session.close()
                self.session = None
