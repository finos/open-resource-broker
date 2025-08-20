"""DynamoDB conversion components for domain to DynamoDB mapping."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from boto3.dynamodb.conditions import Attr

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.components.resource_manager import DataConverter


class DynamoDBConverter(DataConverter):
    """
    DynamoDB converter for converting between domain objects and DynamoDB items.

    Handles type conversion, Decimal handling, and DynamoDB-specific data types.
    """

    def __init__(self, partition_key: str = "id", sort_key: Optional[str] = None) -> None:
        """
        Initialize DynamoDB converter.

        Args:
            partition_key: Name of the partition key
            sort_key: Name of the sort key (optional)
        """
        self.partition_key = partition_key
        self.sort_key = sort_key
        self.logger = get_logger(__name__)

    def to_storage_format(self, domain_data: Dict[str, Any]) -> Any:
        """Convert domain data to DynamoDB format (implements DataConverter interface)."""
        # Extract entity_id from domain_data if present
        entity_id = domain_data.get(self.partition_key, domain_data.get("id", "unknown"))
        return self.to_dynamodb_item(entity_id, domain_data)

    def from_storage_format(self, storage_data: Any) -> Dict[str, Any]:
        """Convert DynamoDB data to domain format (implements DataConverter interface)."""
        return self.from_dynamodb_item(storage_data)

    def prepare_for_query(self, criteria: Dict[str, Any]) -> Any:
        """Prepare domain criteria for DynamoDB query (implements DataConverter interface)."""
        return self.build_filter_expression(criteria)

    def to_dynamodb_item(self, entity_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert domain data to DynamoDB item.

        Args:
            entity_id: Entity identifier
            data: Domain data dictionary

        Returns:
            DynamoDB-compatible item
        """
        try:
            item = {self.partition_key: entity_id}

            # Add sort key if specified
            if self.sort_key and self.sort_key in data:
                item[self.sort_key] = self._convert_to_dynamodb_type(data[self.sort_key])

            # Convert all other fields
            for key, value in data.items():
                if key == self.partition_key or key == self.sort_key:
                    continue  # Already handled

                item[key] = self._convert_to_dynamodb_type(value)

            # Add timestamps
            now = datetime.utcnow().isoformat()
            if "created_at" not in item:
                item["created_at"] = now
            item["updated_at"] = now

            self.logger.debug("Converted domain data to DynamoDB item: %s", entity_id)
            return item

        except Exception as e:
            self.logger.error("Failed to convert to DynamoDB item: %s", e)
            raise

    def from_dynamodb_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert DynamoDB item to domain data.

        Args:
            item: DynamoDB item

        Returns:
            Domain data dictionary
        """
        try:
            if not item:
                return {}

            domain_data = {}

            for key, value in item.items():
                domain_data[key] = self._convert_from_dynamodb_type(value)

            self.logger.debug("Converted DynamoDB item to domain data")
            return domain_data

        except Exception as e:
            self.logger.error("Failed to convert from DynamoDB item: %s", e)
            raise

    def from_dynamodb_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convert multiple DynamoDB items to domain data.

        Args:
            items: List of DynamoDB items

        Returns:
            List of domain data dictionaries
        """
        try:
            return [self.from_dynamodb_item(item) for item in items]
        except Exception as e:
            self.logger.error("Failed to convert DynamoDB items: %s", e)
            raise

    def _convert_to_dynamodb_type(self, value: Any) -> Any:
        """
        Convert value to DynamoDB-compatible type.

        Args:
            value: Value to convert

        Returns:
            DynamoDB-compatible value
        """
        if value is None:
            return None

        # Handle enums
        if isinstance(value, Enum):
            return value.value

        # Handle datetime
        if isinstance(value, datetime):
            return value.isoformat()

        # Handle numeric types - convert to Decimal for DynamoDB
        if isinstance(value, (int, float)):
            return Decimal(str(value))

        # Handle boolean
        if isinstance(value, bool):
            return value

        # Handle strings
        if isinstance(value, str):
            return value

        # Handle lists
        if isinstance(value, list):
            return [self._convert_to_dynamodb_type(item) for item in value]

        # Handle dictionaries
        if isinstance(value, dict):
            return {k: self._convert_to_dynamodb_type(v) for k, v in value.items()}

        # Handle sets
        if isinstance(value, set):
            # Convert to list for DynamoDB
            return [self._convert_to_dynamodb_type(item) for item in value]

        # Default: convert to string
        return str(value)

    def _convert_from_dynamodb_type(self, value: Any) -> Any:
        """
        Convert value from DynamoDB type to domain type.

        Args:
            value: DynamoDB value

        Returns:
            Domain-compatible value
        """
        if value is None:
            return None

        # Handle Decimal (convert back to int/float)
        if isinstance(value, Decimal):
            if value % 1 == 0:
                return int(value)
            else:
                return float(value)

        # Handle datetime strings
        if isinstance(value, str):
            # Try to parse as ISO datetime
            from contextlib import suppress

            with suppress(ValueError, TypeError):
                if "T" in value and ("Z" in value or "+" in value or value.endswith("00")):
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value

        # Handle lists
        if isinstance(value, list):
            return [self._convert_from_dynamodb_type(item) for item in value]

        # Handle dictionaries
        if isinstance(value, dict):
            return {k: self._convert_from_dynamodb_type(v) for k, v in value.items()}

        # Return other types as-is
        return value

    def get_key(self, entity_id: str, sort_key_value: Optional[str] = None) -> Dict[str, Any]:
        """
        Get DynamoDB key for entity.

        Args:
            entity_id: Entity identifier
            sort_key_value: Sort key value (if applicable)

        Returns:
            DynamoDB key dictionary
        """
        key = {self.partition_key: entity_id}

        if self.sort_key and sort_key_value is not None:
            key[self.sort_key] = sort_key_value

        return key

    def build_filter_expression(self, criteria: Dict[str, Any]):
        """
        Build DynamoDB filter expression from criteria.

        Args:
            criteria: Search criteria

        Returns:
            Tuple of (filter_expression, expression_attribute_values)
        """
        if not criteria:
            return None, None

        filter_expressions = []
        expression_attribute_values = {}

        for key, value in criteria.items():
            if isinstance(value, dict):
                # Handle special operators
                if "$eq" in value:
                    filter_expressions.append(Attr(key).eq(value["$eq"]))
                elif "$ne" in value:
                    filter_expressions.append(Attr(key).ne(value["$ne"]))
                elif "$in" in value:
                    filter_expressions.append(Attr(key).is_in(value["$in"]))
                elif "$gt" in value:
                    filter_expressions.append(Attr(key).gt(value["$gt"]))
                elif "$gte" in value:
                    filter_expressions.append(Attr(key).gte(value["$gte"]))
                elif "$lt" in value:
                    filter_expressions.append(Attr(key).lt(value["$lt"]))
                elif "$lte" in value:
                    filter_expressions.append(Attr(key).lte(value["$lte"]))
                elif "$contains" in value:
                    filter_expressions.append(Attr(key).contains(value["$contains"]))
                elif "$begins_with" in value:
                    filter_expressions.append(Attr(key).begins_with(value["$begins_with"]))
                else:
                    # Default equality
                    filter_expressions.append(Attr(key).eq(value))
            else:
                # Simple equality
                converted_value = self._convert_to_dynamodb_type(value)
                filter_expressions.append(Attr(key).eq(converted_value))

        # Combine expressions with AND
        if len(filter_expressions) == 1:
            filter_expression = filter_expressions[0]
        else:
            filter_expression = filter_expressions[0]
            for expr in filter_expressions[1:]:
                filter_expression = filter_expression & expr

        return filter_expression, expression_attribute_values

    def prepare_batch_items(self, entities: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Prepare multiple entities for batch operations.

        Args:
            entities: Dictionary of entity_id -> data

        Returns:
            List of DynamoDB items for batch operations
        """
        try:
            items = []

            for entity_id, data in entities.items():
                item = self.to_dynamodb_item(entity_id, data)
                items.append(item)

            self.logger.debug("Prepared %s items for batch operation", len(entities))
            return items

        except Exception as e:
            self.logger.error("Failed to prepare batch items: %s", e)
            raise

    def extract_entity_id(self, item: Dict[str, Any]) -> Optional[str]:
        """
        Extract entity ID from DynamoDB item.

        Args:
            item: DynamoDB item

        Returns:
            Entity ID if found, None otherwise
        """
        return item.get(self.partition_key)
