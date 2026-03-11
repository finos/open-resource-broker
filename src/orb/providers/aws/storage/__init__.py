"""AWS DynamoDB storage package."""

from orb.providers.aws.storage.unit_of_work import DynamoDBUnitOfWork

__all__: list[str] = ["DynamoDBUnitOfWork"]
