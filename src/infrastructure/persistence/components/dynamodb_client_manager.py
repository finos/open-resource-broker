"""DynamoDB client management components for AWS DynamoDB operations."""

from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from .resource_manager import StorageResourceManager as ResourceManager


class DynamoDBClientManager(ResourceManager):
    """
    AWS client manager for DynamoDB operations.

    Handles AWS client initialization, session management, and error handling.
    """

    def __init__(self, aws_client=None, region: str = "us-east-1", profile: Optional[str] = None):
        """
        Initialize DynamoDB client manager.

        Args:
            aws_client: Existing AWS client (optional)
            region: AWS region
            profile: AWS profile name
        """
        super().__init__()
        self.region = region
        self.profile = profile

        # Use provided client or create new one
        if aws_client:
            self.aws_client = aws_client
            self.dynamodb = aws_client.get_client("dynamodb")
            self.dynamodb_resource = aws_client.get_resource("dynamodb")
            self._initialized = True
        else:
            self.aws_client = None
            self.dynamodb = None
            self.dynamodb_resource = None
            self.initialize()

    def initialize(self) -> None:
        """Initialize AWS clients and resources."""
        if self._initialized:
            return

        self._initialize_clients()
        self._initialized = True

    def cleanup(self) -> None:
        """Clean up AWS resources."""
        # DynamoDB clients don't require explicit cleanup
        self.dynamodb = None
        self.dynamodb_resource = None
        self.aws_client = None
        self._initialized = False
        self.logger.debug("DynamoDB client manager cleaned up")

    def is_healthy(self) -> bool:
        """Check if DynamoDB client manager is healthy."""
        try:
            if not self.dynamodb:
                return False

            # Simple health check - list tables (limited response)
            self.dynamodb.list_tables(Limit=1)
            return True
        except Exception as e:
            self.logger.error(f"DynamoDB health check failed: {e}")
            return False

    def get_connection_info(self) -> Dict[str, Any]:
        """Get DynamoDB connection information."""
        return {
            "type": "dynamodb",
            "region": self.region,
            "profile": self.profile,
            "initialized": self._initialized,
            "healthy": self.is_healthy() if self._initialized else False,
            "has_aws_client": self.aws_client is not None,
            "has_dynamodb_client": self.dynamodb is not None,
            "has_dynamodb_resource": self.dynamodb_resource is not None,
        }

    def _initialize_clients(self) -> None:
        """Initialize AWS clients and resources."""
        try:
            # Create session
            if self.profile:
                session = boto3.Session(profile_name=self.profile,
                                        region_name=self.region)
            else:
                session = boto3.Session(region_name=self.region)

            # Create clients
            self.dynamodb = session.client("dynamodb")
            self.dynamodb_resource = session.resource("dynamodb")

            self.logger.info(f"Initialized DynamoDB clients for region {self.region}")

        except Exception as e:
            self.logger.error(f"Failed to initialize DynamoDB clients: {e}")
            raise

    def get_table(self, table_name: str):
        """
        Get DynamoDB table resource.

        Args:
            table_name: Name of the table

        Returns:
            DynamoDB table resource
        """
        try:
            table = self.dynamodb_resource.Table(table_name)
            return table
        except Exception as e:
            self.logger.error(f"Failed to get table {table_name}: {e}")
            raise

    def table_exists(self, table_name: str) -> bool:
        """
        Check if table exists.

        Args:
            table_name: Name of the table

        Returns:
            True if table exists, False otherwise
        """
        try:
            self.dynamodb.describe_table(TableName=table_name)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return False
            else:
                self.logger.error(f"Error checking table existence: {e}")
                raise
        except Exception as e:
            self.logger.error(f"Unexpected error checking table existence: {e}")
            return False

    def create_table(
        self,
        table_name: str,
        key_schema: list,
        attribute_definitions: list,
        billing_mode: str = "PAY_PER_REQUEST",
    ) -> bool:
        """
        Create DynamoDB table.

        Args:
            table_name: Name of the table
            key_schema: Key schema definition
            attribute_definitions: Attribute definitions
            billing_mode: Billing mode (PAY_PER_REQUEST or PROVISIONED)

        Returns:
            True if table created successfully, False otherwise
        """
        try:
            if self.table_exists(table_name):
                self.logger.info(f"Table {table_name} already exists")
                return True

            create_params = {
                "TableName": table_name,
                "KeySchema": key_schema,
                "AttributeDefinitions": attribute_definitions,
                "BillingMode": billing_mode,
            }

            if billing_mode == "PROVISIONED":
                create_params["ProvisionedThroughput"] = {
                    "ReadCapacityUnits": 5,
                    "WriteCapacityUnits": 5,
                }

            self.dynamodb.create_table(**create_params)

            # Wait for table to be created
            waiter = self.dynamodb.get_waiter("table_exists")
            waiter.wait(TableName=table_name)

            self.logger.info(f"Created table: {table_name}")
            return True

        except ClientError as e:
            self.logger.error(f"Failed to create table {table_name}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error creating table {table_name}: {e}")
            return False

    def put_item(self, table_name: str, item: Dict[str, Any]) -> bool:
        """
        Put item to DynamoDB table.

        Args:
            table_name: Name of the table
            item: Item to put

        Returns:
            True if successful, False otherwise
        """
        try:
            table = self.get_table(table_name)
            table.put_item(Item=item)
            return True
        except Exception as e:
            self.logger.error(f"Failed to put item to {table_name}: {e}")
            return False

    def get_item(self, table_name: str, key: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Get item from DynamoDB table.

        Args:
            table_name: Name of the table
            key: Primary key of the item

        Returns:
            Item if found, None otherwise
        """
        try:
            table = self.get_table(table_name)
            response = table.get_item(Key=key)
            return response.get("Item")
        except Exception as e:
            self.logger.error(f"Failed to get item from {table_name}: {e}")
            return None

    def delete_item(self, table_name: str, key: Dict[str, Any]) -> bool:
        """
        Delete item from DynamoDB table.

        Args:
            table_name: Name of the table
            key: Primary key of the item

        Returns:
            True if successful, False otherwise
        """
        try:
            table = self.get_table(table_name)
            table.delete_item(Key=key)
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete item from {table_name}: {e}")
            return False

    def scan_table(
        self,
        table_name: str,
        filter_expression=None,
        expression_attribute_values: Optional[Dict[str, Any]] = None,
    ) -> list:
        """
        Scan DynamoDB table.

        Args:
            table_name: Name of the table
            filter_expression: Filter expression for scan
            expression_attribute_values: Expression attribute values

        Returns:
            List of items
        """
        try:
            table = self.get_table(table_name)

            scan_params = {}
            if filter_expression:
                scan_params["FilterExpression"] = filter_expression
            if expression_attribute_values:
                scan_params["ExpressionAttributeValues"] = expression_attribute_values

            response = table.scan(**scan_params)
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                scan_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = table.scan(**scan_params)
                items.extend(response.get("Items", []))

            return items

        except Exception as e:
            self.logger.error(f"Failed to scan table {table_name}: {e}")
            return []

    def batch_write_items(self, table_name: str, items: list) -> bool:
        """
        Batch write items to DynamoDB table.

        Args:
            table_name: Name of the table
            items: List of items to write

        Returns:
            True if successful, False otherwise
        """
        try:
            table = self.get_table(table_name)

            # DynamoDB batch_writer handles batching automatically
            with table.batch_writer() as batch:
                for item in items:
                    batch.put_item(Item=item)

            return True

        except Exception as e:
            self.logger.error(f"Failed to batch write items to {table_name}: {e}")
            return False

    def handle_client_error(self, error: ClientError, operation: str) -> None:
        """
        Handle and log DynamoDB client errors.

        Args:
            error: ClientError exception
            operation: Operation that failed
        """
        error_code = error.response["Error"]["Code"]
        error_message = error.response["Error"]["Message"]

        if error_code == "ResourceNotFoundException":
            self.logger.error(f"{operation} failed: Table not found - {error_message}")
        elif error_code == "ValidationException":
            self.logger.error(f"{operation} failed: Validation error - {error_message}")
        elif error_code == "ConditionalCheckFailedException":
            self.logger.error(
                f"{operation} failed: Conditional check failed - {error_message}")
        elif error_code == "ProvisionedThroughputExceededException":
            self.logger.error(
                f"{operation} failed: Throughput exceeded - {error_message}")
        else:
            self.logger.error(f"{operation} failed: {error_code} - {error_message}")

    def get_client(self):
        """Get DynamoDB client."""
        return self.dynamodb

    def get_resource(self):
        """Get DynamoDB resource."""
        return self.dynamodb_resource
