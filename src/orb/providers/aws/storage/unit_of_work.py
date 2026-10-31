"""DynamoDB Unit of Work implementation using simplified repositories."""

from typing import Optional

from orb.infrastructure.di.injectable import injectable
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

# Import DynamoDB storage strategy
from orb.providers.aws.storage.components import (
    DynamoDBClientManager,
    DynamoDBTransactionManager,
)
from orb.providers.aws.storage.strategy import DynamoDBStorageStrategy


@injectable
class DynamoDBUnitOfWork(BaseUnitOfWork):
    """DynamoDB-based unit of work implementation using simplified repositories.

    All three repositories share one transaction manager. Writes made between
    ``begin`` and ``commit`` are staged and sent as a single
    ``TransactWriteItems`` call, so they are applied to every table or to none.

    Behaviour to be aware of:

    * Staged writes are not visible to reads (``get_by_id``, ``find_*``,
      ``exists``) until the unit of work commits.
    * Save failures surface when the unit of work commits, not at ``save``.
    * A commit of up to 100 writes (and within the request size limit) is one
      atomic transaction. Larger commits are sent as several transactions in a
      fixed order (request-table puts first, other tables next, request-table
      deletes last) and are not atomic as a whole: a failure part-way raises
      ``TransactionError`` stating how many calls were already applied, which
      are not rolled back. Repeating the same writes or deletes is safe.
    * Domain events published inside the ``with`` block are published before
      the commit happens.
    * A unit of work is not reusable after a failed commit; create a new one.
    """

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

        shared_transactions = DynamoDBTransactionManager(
            DynamoDBClientManager(aws_client, region, profile)
        )
        shared_transactions.anchor_table = request_table

        # Create storage strategies for each repository
        machine_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=machine_table,
            profile=profile,
            transaction_manager=shared_transactions,
        )

        request_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=request_table,
            profile=profile,
            transaction_manager=shared_transactions,
        )

        template_strategy = DynamoDBStorageStrategy(
            logger=logger,
            aws_client=aws_client,
            region=region,
            table_name=template_table,
            profile=profile,
            transaction_manager=shared_transactions,
        )

        # Create repositories using simplified implementations
        self.machine_repository = MachineRepository(machine_strategy)
        self.request_repository = RequestRepository(request_strategy)
        self.template_repository = TemplateRepository(template_strategy)

        # Keep typed strategy references for transaction management
        self._machine_strategy = machine_strategy
        self._request_strategy = request_strategy
        self._template_strategy = template_strategy
        self._transactions = shared_transactions

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
        """Begin the shared DynamoDB transaction."""
        try:
            self._transactions.begin_transaction()
            self._logger.debug("DynamoDB transaction begun on all repositories")
        except Exception as e:
            self._logger.error("Failed to begin DynamoDB transaction: %s", e)
            raise

    def _commit_transaction(self) -> None:
        """Commit every staged write in one DynamoDB transaction."""
        try:
            self._transactions.commit_transaction()
            self._logger.debug("DynamoDB transaction committed on all repositories")
        except Exception as e:
            self._logger.error("Failed to commit DynamoDB transaction: %s", e)
            raise

    def _rollback_transaction(self) -> None:
        """Discard all staged writes."""
        try:
            self._transactions.rollback_transaction()
            self._logger.debug("DynamoDB transaction rolled back on all repositories")
        except Exception as e:
            self._logger.error("Failed to rollback DynamoDB transaction: %s", e)
            raise
