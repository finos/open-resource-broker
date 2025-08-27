import json
import os
from ..models.config.provider_config import ProviderConfig
from ..helpers.utils import ensure_directory_exists
from src.helpers.logger import setup_logging

logger = setup_logging()


class ProviderConfigManager:
    """
    Singleton class to manage provider configuration.
    Loads configuration from a configurable JSON file and provides access to its values.
    Ensures required directories are validated and created dynamically.
    """
    _instance = None
    _config: ProviderConfig = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ProviderConfigManager, cls).__new__(cls)
            cls._instance._load_config()
        return cls._instance

    def _load_config(self) -> None:
        """
        Load configuration from the JSON file specified in the environment or defaults.
        """
        # Retrieve environment variables for HostFactory directories and provider name
        provider_name = os.environ.get('HF_PROVIDER_NAME', 'default')
        confdir = os.environ.get('HF_PROVIDER_CONFDIR', f"/etc/hostfactory/providers/{provider_name}")
        workdir = os.environ.get('HF_PROVIDER_WORKDIR', f"/var/hostfactory/workdir/providers/{provider_name}")
        logdir = os.environ.get('HF_PROVIDER_LOGDIR', f"/var/hostfactory/logs/providers/{provider_name}")

        # Ensure directories exist
        ensure_directory_exists(confdir)
        ensure_directory_exists(workdir)
        ensure_directory_exists(logdir)

        # Determine configuration file path
        config_file = os.environ.get('AWSPROV_CONFIG_PATH', os.path.join(confdir, 'awsprov_config.json'))
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        # Load configuration file
        try:
            with open(config_file, 'r') as f:
                config_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in configuration file {config_file}. Error: {str(e)}")

        # Set default paths based on HostFactory environment variables
        config_data.setdefault("DATABASE_PATH", workdir)
        config_data.setdefault("LOG_DIR", logdir)

        # Set default log level and destination
        config_data.setdefault("LOG_LEVEL", "INFO")
        config_data.setdefault("LOG_DESTINATION", "both")

        # Dynamically create ProviderConfig instance using from_dict to handle known and arbitrary fields
        self._config = ProviderConfig.from_dict(config_data)
        
        # Validate the configuration
        self._config.validate()

        logger.info(f"Configuration loaded successfully from {config_file}")

    @staticmethod
    def _ensure_directory(path: str) -> None:
        """
        Ensure that a directory exists. If it does not exist, create it.

        :param path: The directory path to check or create.
        """
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
            logger.info(f"Created directory: {path}")

    @classmethod
    def get(cls, key: str, default=None):
        """
        Retrieve a specific configuration value by key.

        :param key: The name of the configuration key.
        :param default: The default value to return if the key is not found.
        :return: The value of the configuration key or the default value.
        """
        if cls._instance is None:
            cls()
        
        # Check both known attributes and additional options dynamically
        value = getattr(cls._instance._config, key, cls._instance._config.additional_options.get(key, default))
        if value is None:
            logger.warning(f"Configuration key '{key}' not found, using default value: {default}")
        return value

    @classmethod
    def get_config(cls) -> ProviderConfig:
        """
        Retrieve the entire ProviderConfig object.

        :return: The ProviderConfig object.
        """
        if cls._instance is None:
            cls()
        
        return cls._instance._config

    @classmethod
    def reload(cls) -> None:
        """
        Reload the configuration from the JSON file.

        This method resets the singleton instance, reinitializes directories,
        and reloads the configuration dynamically at runtime.
        
        :raises FileNotFoundError: If the configuration file is missing.
        :raises ValueError: If validation fails for any required fields.
        """
        # Reset singleton instance
        cls._instance = None

        # Reinitialize configuration
        instance = cls()  # This will call __new__ and _load_config()
        
        # Log reloading success
        logger.info("Provider configuration reloaded successfully.")

    def __str__(self) -> str:
        return f"ProviderConfigManager(config={self._config})"

    def __repr__(self) -> str:
        return self.__str__()
