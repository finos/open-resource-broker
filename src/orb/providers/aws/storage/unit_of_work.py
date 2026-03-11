"""DynamoDB Unit of Work implementation using simplified repositories."""

from typing import Optional

from orb.domain.base.dependency_injection import injectable
from orb.infrastructure.storage.base.unit_of_work import BaseUnitOfWork

# Import DynamoDB storage strategy
from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy

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


@injectable
class DynamoDBUnitOfWork(BaseUnitOfWork):
    """DynamoDB-based unit of work implementation using simplified repositories."""

    def __init__(
        self,
        aws_client,
        logger,
        region: Optional[str] = None,
        profile: Optional[str] = None,
        machine_table: str = "machines",
        request_table: str = "requests",
        template_table: str = "templates",
    ) -> None:
        """
        Initialize DynamoDB unit of work with simplified repositories.

        Args:
            aws_client: AWS client instance
            logger: Logger instance
            region: AWS region
            profile: AWS profile name (optional)
            machine_table: DynamoDB table name for machines
            request_table: DynamoDB table name for requests
            template_table: DynamoDB table name for templates
        """
        super().__init__()

        self._logger = logger
        self.aws_client = aws_client
        self.region = region
        self.profile = profile

        # Create storage strategies for each repository
        machine_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=machine_table,
            profile=profile,
        )

        request_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=request_table,
            profile=profile,
        )

        template_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=template_table,
            profile=profile,
        )

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        # Keep typed strategy references for transaction management
        self._machine_strategy = machine_strategy
        self._request_strategy = request_strategy
        self._template_strategy = template_strategy

        self._logger.debug(
            "Initialized DynamoDBUnitOfWork with simplified repositories in region: %s",
            region,
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
        """Begin DynamoDB transaction."""
        try:
            self._machine_strategy.begin_transaction()
            self._request_strategy.begin_transaction()
            self._template_strategy.begin_transaction()

            self._logger.debug("DynamoDB transaction begun on all repositories")
        except Exception as e:
            self._logger.error("Failed to begin DynamoDB transaction: %s", e)
            raise

    def _commit_transaction(self) -> None:
        """Commit DynamoDB transaction."""
        try:
            self._machine_strategy.commit_transaction()
            self._request_strategy.commit_transaction()
            self._template_strategy.commit_transaction()

            self._logger.debug("DynamoDB transaction committed on all repositories")
        except Exception as e:
            self._logger.error("Failed to commit DynamoDB transaction: %s", e)
            raise

    def _rollback_transaction(self) -> None:
        """Rollback DynamoDB transaction."""
        try:
            self._machine_strategy.rollback_transaction()
            self._request_strategy.rollback_transaction()
            self._template_strategy.rollback_transaction()

            self._logger.debug("DynamoDB transaction rolled back on all repositories")
        except Exception as e:
            self._logger.error("Failed to rollback DynamoDB transaction: %s", e)
            raise
