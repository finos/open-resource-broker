"""SQL Unit of Work implementation using simplified repositories.

Schema authority: orb.infrastructure.storage.sql.models (ORM declarative models).
Use ``alembic upgrade head`` to apply schema migrations.
"""

from typing import Any, Optional

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.unit_of_work import BaseUnitOfWork

# Import new simplified repositories
from orb.infrastructure.storage.repositories.machine_repository import (
    MachineRepositoryImpl as MachineRepository,
)
from orb.infrastructure.storage.repositories.request_repository import (
    RequestRepositoryImpl as RequestRepository,
)
from orb.infrastructure.storage.repositories.template_repository import (
    TemplateRepositoryImpl as TemplateRepository,
)


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

        # The strategies share the caller's engine, so they always operate on the
        # database the engine points at (including a shared in-memory database).
        db_type = engine.url.get_dialect().name  # e.g. "sqlite", "postgresql"
        db_config: dict[str, Any] = {"type": db_type, "engine": engine}

        # The same entity -> table mapping every other SQL entry point uses; the
        # ORM models (not these column dicts) remain the schema authority.
        from orb.infrastructure.storage.sql.entities import create_entity_strategy

        machine_strategy = create_entity_strategy(db_config, "machines")
        request_strategy = create_entity_strategy(db_config, "requests")
        template_strategy = create_entity_strategy(db_config, "templates")

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        # Keep typed references for transaction management
        self._machine_strategy = machine_strategy
        self._request_strategy = request_strategy
        self._template_strategy = template_strategy

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

    def _begin_transaction(self) -> None:
        """Begin SQL transaction."""
        try:
            self.session = Session(self.engine)

            # Begin transaction on storage strategies
            self._machine_strategy.begin_transaction()
            self._request_strategy.begin_transaction()
            self._template_strategy.begin_transaction()

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
                self._machine_strategy.commit_transaction()
                self._request_strategy.commit_transaction()
                self._template_strategy.commit_transaction()

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
                self._machine_strategy.rollback_transaction()
                self._request_strategy.rollback_transaction()
                self._template_strategy.rollback_transaction()

                self.session.rollback()
                self.logger.debug("SQL transaction rolled back on all repositories")
        except Exception as e:
            self.logger.error("Failed to rollback SQL transaction: %s", e)
            raise
        finally:
            if self.session:
                self.session.close()
                self.session = None
