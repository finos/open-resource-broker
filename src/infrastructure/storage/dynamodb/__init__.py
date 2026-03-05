"""DynamoDB storage package."""

from infrastructure.storage.dynamodb.unit_of_work import DynamoDBUnitOfWork

__all__: list[str] = ["DynamoDBUnitOfWork"]
