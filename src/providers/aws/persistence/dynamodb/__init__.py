"""DynamoDB persistence package."""

from providers.aws.persistence.dynamodb.unit_of_work import DynamoDBUnitOfWork

__all__: list[str] = ["DynamoDBUnitOfWork"]
