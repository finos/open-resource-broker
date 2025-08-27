import os
from src.database.json_handler import JSONHandler
from src.database.dynamodb_handler import DynamoDBHandler
from src.database.sqlite_handler import SQLiteHandler
from src.database.database_handler import DatabaseHandler
from src.config.provider_config_manager import ProviderConfigManager
from src.helpers.logger import setup_logging

logger = setup_logging()


class DatabaseFactory:
    """
    Factory class to create and manage database handlers dynamically based on configuration.
    """

    @staticmethod
    def create_database_handler() -> DatabaseHandler:
        """
        Create a DatabaseHandler instance with the appropriate backend.

        :return: A DatabaseHandler instance configured with the appropriate backend.
        :raises ValueError: If the database type specified in the configuration is unsupported.
        """
        config = ProviderConfigManager.get_config()
        database_type = config.DATABASE_TYPE.lower()

        if database_type == "json":
            file_path = os.path.join(config.DATABASE_PATH, config.DATABASE_FILE_NAME)
            backend = JSONHandler(file_path)
        elif database_type == "sqlite":
            file_path = os.path.join(config.DATABASE_PATH, config.DATABASE_FILE_NAME)
            backend = SQLiteHandler(file_path)
        elif database_type == "dynamodb":
            backend = DynamoDBHandler(config.AWS_REGION, config.DATABASE_TABLE)
        else:
            raise ValueError(f"Unsupported database type: {database_type}")

        return DatabaseHandler(backend)
