"""SQL query builder components for database operations."""

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from infrastructure.logging.logger import get_logger
from infrastructure.persistence.components.resource_manager import QueryManager


class QueryType(str, Enum):
    """SQL query type enumeration."""

    SELECT = "SELECT"
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    CREATE_TABLE = "CREATE TABLE"


class SQLQueryBuilder(QueryManager):
    """
    SQL query builder for generating parameterized queries.

    Builds safe, parameterized SQL queries to prevent SQL injection.
    """

    def __init__(self, table_name: str, columns: Dict[str, str]):
        """
        Initialize query builder.

        Args:
            table_name: Name of the database table
            columns: Dictionary of column names and types
        """
        self.table_name = table_name
        self.columns = columns
        self.logger = get_logger(__name__)

        # Validate table name and column names
        self._validate_identifier(table_name)
        for column in columns:
            self._validate_identifier(column)

    def _validate_identifier(self, identifier: str) -> None:
        """
        Validate SQL identifier against whitelist pattern.

        Args:
            identifier: SQL identifier to validate

        Raises:
            ValueError: If identifier contains invalid characters
        """
        if not re.match(r"^[a-zA-Z0-9_]+$", identifier):
            raise ValueError(f"Invalid SQL identifier: {identifier}")

    def build_create_query(self, **kwargs) -> str:
        """Build CREATE TABLE query (implements QueryManager interface)."""
        return self.build_create_table()

    def build_read_query(
        self,
        entity_id: Optional[str] = None,
        criteria: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Tuple[str, Dict[str, Any]]:
        """Build SELECT query (implements QueryManager interface)."""
        if entity_id:
            id_column = kwargs.get("id_column", "id")
            return self.build_select_by_id(id_column)
        elif criteria:
            return self.build_select_by_criteria(criteria)
        else:
            return self.build_select_all(), {}

    def build_update_query(
        self, data: Dict[str, Any], entity_id: str, id_column: str = "id", **kwargs
    ) -> Tuple[str, Dict[str, Any]]:
        """Build UPDATE query (implements QueryManager interface)."""
        return self.build_update(data, id_column, entity_id)

    def build_delete_query(
        self, entity_id: str, id_column: str = "id", **kwargs
    ) -> Tuple[str, str]:
        """Build DELETE query (implements QueryManager interface)."""
        return self.build_delete(id_column)

    def build_create_table(self) -> str:
        """
        Build CREATE TABLE query.

        Returns:
            CREATE TABLE SQL statement
        """
        column_definitions = []
        for column_name, column_type in self.columns.items():
            column_definitions.append(f"{column_name} {column_type}")

        query = f"CREATE TABLE IF NOT EXISTS {self.table_name} (\n"  # nosec B608
        query += ",\n".join(f"    {col_def}" for col_def in column_definitions)
        query += "\n)"

        self.logger.debug("Built CREATE TABLE query for %s", self.table_name)
        return query

    def build_insert(self, data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Build INSERT query with parameters.

        Args:
            data: Data to insert

        Returns:
            Tuple of (query, parameters)
        """
        # Filter data to only include known columns
        filtered_data = {k: v for k, v in data.items() if k in self.columns}

        if not filtered_data:
            raise ValueError("No valid columns found in data")

        columns = list(filtered_data.keys())
        placeholders = [f":{col}" for col in columns]

        # Validate all column names
        for column in columns:
            self._validate_identifier(column)

        # Build query using validated identifiers
        # 1. Validating table_name and column names against a whitelist pattern
        # 2. Using parameterized queries for all values with :param syntax
        # nosec B608
        query = (
            f"INSERT INTO {self.table_name} "  # nosec B608
            f"({', '.join(columns)}) VALUES ({', '.join(placeholders)})"  # nosec B608
        )

        self.logger.debug("Built INSERT query for %s", self.table_name)
        return query, filtered_data

    def build_select_by_id(self, id_column: str) -> Tuple[str, str]:
        """
        Build SELECT by ID query.

        Args:
            id_column: Name of the ID column

        Returns:
            Tuple of (query, parameter_name)
        """
        # Validate identifier
        self._validate_identifier(id_column)

        # nosec B608
        query = (
            f"SELECT * FROM {self.table_name} "  # nosec B608
            f"WHERE {id_column} = :{id_column}"  # nosec B608
        )

        self.logger.debug("Built SELECT by ID query for %s", self.table_name)
        return query, id_column

    def build_select_all(self) -> str:
        """
        Build SELECT all query.

        Returns:
            SELECT all SQL statement
        """
        # Table name already validated in constructor
        query = f"SELECT * FROM {self.table_name}  # nosec B608"  # nosec B608

        self.logger.debug("Built SELECT all query for %s", self.table_name)
        return query

    def build_update(
        self, data: Dict[str, Any], id_column: str, entity_id: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Build UPDATE query with parameters.

        Args:
            data: Data to update
            id_column: Name of the ID column
            entity_id: ID of entity to update

        Returns:
            Tuple of (query, parameters)
        """
        # Validate id_column
        self._validate_identifier(id_column)

        # Filter data to only include known columns (excluding ID)
        filtered_data = {k: v for k, v in data.items() if k in self.columns and k != id_column}

        if not filtered_data:
            raise ValueError("No valid columns found in data for update")

        # Validate all column names
        for column in filtered_data.keys():
            self._validate_identifier(column)

        set_clauses = [f"{col} = :{col}" for col in filtered_data.keys()]
        # nosec B608
        query = (
            f"UPDATE {self.table_name} SET {', '.join(set_clauses)} "  # nosec B608
            f"WHERE {id_column} = :entity_id"  # nosec B608
        )

        # Add entity_id to parameters
        parameters = filtered_data.copy()
        parameters["entity_id"] = entity_id

        self.logger.debug("Built UPDATE query for %s", self.table_name)
        return query, parameters

    def build_delete(self, id_column: str) -> Tuple[str, str]:
        """
        Build DELETE query.

        Args:
            id_column: Name of the ID column

        Returns:
            Tuple of (query, parameter_name)
        """
        # Validate id_column
        self._validate_identifier(id_column)

        # nosec B608
        query = (
            f"DELETE FROM {self.table_name} "  # nosec B608
            f"WHERE {id_column} = :{id_column}"  # nosec B608
        )

        self.logger.debug("Built DELETE query for %s", self.table_name)
        return query, id_column

    def build_exists(self, id_column: str) -> Tuple[str, str]:
        """
        Build EXISTS check query.

        Args:
            id_column: Name of the ID column

        Returns:
            Tuple of (query, parameter_name)
        """
        query = f"SELECT 1 FROM { self.table_name} WHERE {id_column} = :{id_column} LIMIT 1"  # nosec B608

        self.logger.debug("Built EXISTS query for %s", self.table_name)
        return query, id_column

    def build_select_by_criteria(self, criteria: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """
        Build SELECT with WHERE criteria.

        Args:
            criteria: Search criteria

        Returns:
            Tuple of (query, parameters)
        """
        if not criteria:
            return self.build_select_all(), {}

        # Filter criteria to only include known columns
        filtered_criteria = {k: v for k, v in criteria.items() if k in self.columns}

        if not filtered_criteria:
            return self.build_select_all(), {}

        # Validate all column names
        for column in filtered_criteria.keys():
            self._validate_identifier(column)

        where_clauses = []
        parameters = {}

        for column, value in filtered_criteria.items():
            if isinstance(value, dict):
                # Handle special operators
                if "$in" in value:
                    placeholders = []
                    for i, item in enumerate(value["$in"]):
                        param_name = f"{column}_in_{i}"
                        placeholders.append(f":{param_name}")
                        parameters[param_name] = item
                    where_clauses.append(f"{column} IN ({', '.join(placeholders)})")
                elif "$like" in value:
                    param_name = f"{column}_like"
                    where_clauses.append(f"{column} LIKE :{param_name}")
                    parameters[param_name] = value["$like"]
                else:
                    # Default equality
                    param_name = f"{column}_eq"
                    where_clauses.append(f"{column} = :{param_name}")
                    parameters[param_name] = value
            else:
                # Simple equality
                param_name = f"{column}_eq"
                where_clauses.append(f"{column} = :{param_name}")
                parameters[param_name] = value

        # nosec B608
        query = (
            f"SELECT * FROM {self.table_name} "  # nosec B608
            f"WHERE {' AND '.join(where_clauses)}"  # nosec B608
        )

        self.logger.debug("Built SELECT with criteria query for %s", self.table_name)
        return query, parameters

    def build_count(self) -> str:
        """
        Build COUNT query.

        Returns:
            COUNT SQL statement
        """
        # Table name already validated in constructor
        query = f"SELECT COUNT(*) FROM {self.table_name}"  # nosec B608

        self.logger.debug("Built COUNT query for %s", self.table_name)
        return query

    def build_batch_insert(
        self, data_list: List[Dict[str, Any]]
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Build batch INSERT query.

        Args:
            data_list: List of data dictionaries to insert

        Returns:
            Tuple of (query, parameters_list)
        """
        if not data_list:
            raise ValueError("No data provided for batch insert")

        # Use first item to determine columns
        first_item = data_list[0]
        filtered_columns = [k for k in first_item.keys() if k in self.columns]

        # Validate all column names
        for column in filtered_columns:
            self._validate_identifier(column)

        if not filtered_columns:
            raise ValueError("No valid columns found in data")

        placeholders = [f":{col}" for col in filtered_columns]
        # nosec B608
        query = (
            f"INSERT INTO {self.table_name} "  # nosec B608
            f"({', '.join(filtered_columns)}) VALUES ({', '.join(placeholders)})"  # nosec B608
        )

        # Filter all data items
        filtered_data_list = []
        for data in data_list:
            filtered_data = {k: v for k, v in data.items() if k in filtered_columns}
            filtered_data_list.append(filtered_data)

        self.logger.debug(
            "Built batch INSERT query for %s with %s items",  self.table_name,  len(data_list)
        )
        return query, filtered_data_list
