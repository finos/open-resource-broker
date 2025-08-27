import yaml
import os
from typing import Any, Dict, Optional, List
from .database_handler_interface import BaseDatabaseHandler
from src.helpers.logger import setup_logging

logger = setup_logging()


class YAMLHandler(BaseDatabaseHandler):
    def __init__(self, db_file: Optional[str] = None):
        """
        Initialize YAMLHandler.

        :param db_file: Path to the database file. If None, defaults to {HF_PROVIDER_WORKDIR}/{HF_PROVIDER_NAME}_database.yaml.
        """
        provider_name = os.environ.get("HF_PROVIDER_NAME", "default")
        workdir = os.environ.get("HF_PROVIDER_WORKDIR", f"./{provider_name}/workdir")
        os.makedirs(workdir, exist_ok=True)

        self.db_file = db_file or os.path.join(workdir, f"{provider_name}_database.yaml")
        self.data = self._load_data()
        self.initialize_structure()

    def _load_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Load data from the YAML file. If the file is empty or contains invalid YAML,
        initialize it with an empty dictionary.

        :return: The loaded data as a dictionary.
        """
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r") as f:
                    data = yaml.safe_load(f)
                    if not isinstance(data, dict):
                        raise ValueError("Invalid database format: Expected a dictionary.")
                    return data
            except yaml.YAMLError:
                logger.error(f"Invalid YAML in {self.db_file}. Reinitializing database.")
            except ValueError as e:
                logger.error(f"Error loading database: {e}. Reinitializing database.")

        # If file does not exist or is invalid, initialize with an empty dictionary
        logger.warning(f"Database file '{self.db_file}' not found or invalid. Initializing as empty.")
        return {}

    def _save_data(self) -> None:
        """
        Save data to the YAML file.

        :raises IOError: If there is an error writing to the file.
        """
        try:
            with open(self.db_file, "w") as f:
                yaml.dump(self.data, f, indent=2)
            logger.info(f"Database saved to {self.db_file}.")
        except IOError as e:
            logger.error(f"Failed to save database to {self.db_file}: {e}")
            raise

    def insert(self, table: str, key: str, value: Dict[str, Any]) -> None:
        if table not in self.data:
            self.data[table] = {}
        self.data[table][key] = value
        self._save_data()

    def get(self, table: str, key: str) -> Optional[Dict[str, Any]]:
        return self.data.get(table, {}).get(key)

    def update(self, table: str, key: str, value: Dict[str, Any]) -> None:
        if table in self.data and key in self.data[table]:
            self.data[table][key] = value
            self._save_data()

    def delete(self, table: str, key: str) -> None:
        if table in self.data and key in self.data[table]:
            del self.data[table][key]
            self._save_data()

    def query(self, table: str, conditions: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            item for item in self.data.get(table, {}).values()
            if all(item.get(k) == v for k, v in conditions.items())
        ]

    def scan(self, table: str) -> List[Dict[str, Any]]:
        return list(self.data.get(table, {}).values())

    def initialize_structure(self) -> None:
        """
        Initialize the database structure if it doesn't exist.
        Ensures that required tables are present.
        Example tables include 'requests' and 'machines'.
        """
        if not self.data:
            self.data = {"requests": {}, "machines": {}}
            self._save_data()
            logger.info(f"Initialized database structure in {self.db_file}.")
