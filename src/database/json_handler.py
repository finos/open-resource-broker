import json
import os
import shutil
from typing import Any, Dict, Optional, List
from datetime import datetime
from .database_handler_interface import BaseDatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class JSONHandler(BaseDatabaseHandler):
    """
    A handler for managing a JSON-based database backend.
    """

    def __init__(self, db_file: Optional[str] = None):
        """
        Initialize JSONHandler.

        :param db_file: Path to the database file. If None, defaults to {HF_PROVIDER_WORKDIR}/{HF_PROVIDER_NAME}_database.json.
        """
        provider_name = os.environ.get("HF_PROVIDER_NAME", "default")
        workdir = os.environ.get("HF_PROVIDER_WORKDIR", f"./{provider_name}/workdir")
        os.makedirs(workdir, exist_ok=True)

        self.db_file = db_file or os.path.join(workdir, f"{provider_name}_database.json")
        self.validate_database()

        self.data = self._load_data()
        self.initialize_structure()

    def _load_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Load data from the JSON file. If the file is empty or contains invalid JSON,
        initialize it with an empty dictionary and create a backup of the corrupted file.

        :return: The loaded data as a dictionary.
        """
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r") as f:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid database format: Expected a dictionary.")
                    return data
            except (json.JSONDecodeError, ValueError) as e:
                logger.error(f"Invalid JSON in {self.db_file}. Reinitializing database. Error: {e}")

                # Create a backup of the corrupted file
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                backup_path = f"{self.db_file}.backup.{timestamp}"
                shutil.copy(self.db_file, backup_path)
                logger.warning(f"Backup of corrupted database created at {backup_path}.")

        # If file does not exist or is invalid, initialize with an empty dictionary
        logger.warning(f"Database file '{self.db_file}' not found or invalid. Initializing as empty.")
        return {}

    def _save_data(self) -> None:
        """
        Save data to the JSON file.

        :raises IOError: If there is an error writing to the file.
        """
        try:
            with open(self.db_file, "w") as f:
                json.dump(self.data, f, indent=2)
            logger.info(f"Database saved to {self.db_file}.")
        except IOError as e:
            logger.error(f"Failed to save database to {self.db_file}: {e}")
            raise

    def insert(self, table: str, key: str, value: Dict[str, Any]) -> None:
        """
        Insert a new record into the specified table.

        :param table: The name of the table.
        :param key: The record's key.
        :param value: The record's value.
        """
        if table not in self.data:
            self.data[table] = {}
        self.data[table][key] = value
        self._save_data()

    def get(self, table: str, key: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a record from the specified table by its key.

        :param table: The name of the table.
        :param key: The record's key.
        :return: The record's value or None if not found.
        """
        return self.data.get(table, {}).get(key)

    def update(self, table: str, key: str, value: Dict[str, Any]) -> None:
        """
        Update an existing record in the specified table.

        :param table: The name of the table.
        :param key: The record's key.
        :param value: The new value for the record.
        """
        if table in self.data and key in self.data[table]:
            self.data[table][key] = value
            self._save_data()

    def delete(self, table: str, key: str) -> None:
        """
        Delete a record from the specified table by its key.

        :param table: The name of the table.
        :param key: The record's key.
        """
        if table in self.data and key in self.data[table]:
            del self.data[table][key]
            self._save_data()

    def query(self, table: str, conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Query records in a table that match specific conditions.

        :param table: The name of the table.
        :param conditions: A dictionary of conditions to match.
                           Example: {"key1": "value1", "key2": "value2"}
                           All conditions must be satisfied for a match.
        :return: A list of matching records.
        """
        if table not in self.data:
            raise KeyError(f"Table '{table}' does not exist in the database.")
        
        return [
            item for item in self.data[table].values()
            if all(item.get(k) == v for k, v in conditions.items())
        ]

    def scan(self, table: str) -> List[Dict[str, Any]]:
        """
        Scan all records from the specified table.

        :param table: The name of the table to scan.
        :return: A list of records in the table.
        """
        return list(self.data.get(table, {}).values())

    def initialize_structure(self) -> None:
        """
        Initialize the database structure if it doesn't exist.
        
        Ensures that required tables are present.
        """
        if not self.data:
            self.data = {"requests": {}, "machines": {}}
            self._save_data()
            logger.info(f"Initialized database structure in {self.db_file}.")

    def validate_database(self) -> None:
        """
        Validate that the database file contains valid JSON.
        """
        try:
            with open(self.db_file, "r") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            logger.error(f"Database file {self.db_file} contains invalid JSON: {e}")
            raise ValueError("Invalid database file. Please fix or restore from backup.")

    def create_backup(self) -> str:
        """
        Create a backup of the current database file.

        :return: The path of the created backup file.
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_path = f"{self.db_file}.backup.{timestamp}"
        shutil.copy(self.db_file, backup_path)
        logger.info(f"Backup of database created at {backup_path}.")
        return backup_path

    def restore_from_backup(self, backup_path: str) -> None:
        """
        Restore the database from a backup file.

        :param backup_path: The path to the backup file to restore from.
        """
        if not os.path.exists(backup_path):
            raise FileNotFoundError(f"Backup file not found: {backup_path}")

        shutil.copy(backup_path, self.db_file)
        self.data = self._load_data()
        logger.info(f"Database restored from backup: {backup_path}")
