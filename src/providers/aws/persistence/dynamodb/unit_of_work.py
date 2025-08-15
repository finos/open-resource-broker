"""DynamoDB Unit of Work implementation using simplified repositories."""

from typing import Optional

from domain.base.dependency_injection import injectable
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

# Import DynamoDB storage strategy
from providers.aws.persistence.dynamodb.strategy import DynamoDBStorageStrategy


@injectable
class DynamoDBUnitOfWork(BaseUnitOfWork):
    """DynamoDB-based unit of work implementation using simplified repositories."""

    def __init__(
        self,
        aws_client,
        logger,
        region: str,
        profile: Optional[str] = None,
        machine_table: str = "machines",
        request_table: str = "requests",
        template_table: str = "templates",
    ):
        """
        Initialize DynamoDB unit of work with simplified repositories.

        Args:
            aws_client: AWS client instance
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
            aws_client=aws_client,
            region=region,
            table_name=machine_table,
            profile=profile,
        )

        request_strategy = DynamoDBStorageStrategy(
            aws_client=aws_client,
            region=region,
            table_name=request_table,
            profile=profile,
        )

        template_strategy = DynamoDBStorageStrategy(
            aws_client=aws_client,
            region=region,
            table_name=template_table,
            profile=profile,
        )

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        self._self._logger.debug(
            f"Initialized DynamoDBUnitOfWork with simplified repositories in region: {region}"
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
            # DynamoDB transactions are handled at the operation level
            # Begin transaction on storage strategies
            self.machine_repository.storage_strategy.begin_transaction()
            self.request_repository.storage_strategy.begin_transaction()
            self.template_repository.storage_strategy.begin_transaction()

            self._self._logger.debug("DynamoDB transaction begun on all repositories")
        except Exception as e:
            self._self._logger.error(f"Failed to begin DynamoDB transaction: {e}")
            raise

    def _commit_transaction(self) -> None:
        """Commit DynamoDB transaction."""
        try:
            # Commit transaction on storage strategies
            self.machine_repository.storage_strategy.commit_transaction()
            self.request_repository.storage_strategy.commit_transaction()
            self.template_repository.storage_strategy.commit_transaction()

            self._self._logger.debug("DynamoDB transaction committed on all repositories")
        except Exception as e:
            self._self._logger.error(f"Failed to commit DynamoDB transaction: {e}")
            raise

    def _rollback_transaction(self) -> None:
        """Rollback DynamoDB transaction."""
        try:
            # Rollback transaction on storage strategies
            self.machine_repository.storage_strategy.rollback_transaction()
            self.request_repository.storage_strategy.rollback_transaction()
            self.template_repository.storage_strategy.rollback_transaction()

            self._self._logger.debug("DynamoDB transaction rolled back on all repositories")
        except Exception as e:
            self._self._logger.error(f"Failed to rollback DynamoDB transaction: {e}")
            raise
