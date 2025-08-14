"""AWS client wrapper with additional functionality."""

import threading
from typing import TYPE_CHECKING, Any, Dict, Optional, TypeVar

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.domain.base.dependency_injection import injectable
from src.domain.base.ports import ConfigurationPort, LoggingPort
from src.providers.aws.exceptions.aws_exceptions import (
    AuthorizationError,
    AWSConfigurationError,
    NetworkError,
)

if TYPE_CHECKING:
    pass

# Type variable for generic function return type
T = TypeVar("T")


@injectable
class AWSClient:
    """Wrapper for AWS service clients with additional functionality."""

    def __init__(self, config: ConfigurationPort, logger: LoggingPort) -> None:
        """
        Initialize AWS client wrapper.

        Args:
            config: Configuration port for accessing configuration
            logger: Logger for logging messages
        """
        self.config = {}
        self._config_manager = config
        self._logger = logger

        # Get region from configuration
        self.region_name = self._get_region_from_config_manager(
            self._config_manager) or "eu-west-1"

        self._logger.debug(f"AWS client region determined: {self.region_name}")

        # Configure retry settings
        self.boto_config = Config(
            region_name=self.region_name,
            retries={
                "max_attempts": self.config.get("AWS_MAX_RETRIES", 3),
                "mode": "adaptive",
            },
            connect_timeout=self.config.get("AWS_CONNECT_TIMEOUT", 5),
            read_timeout=self.config.get("AWS_READ_TIMEOUT", 10),
        )

        # Load performance configuration
        self.perf_config = self._load_performance_config(self._config_manager)

        # Initialize resource cache
        self._resource_cache = {}
        self._cache_lock = threading.RLock()

        # Initialize adaptive batch sizing history
        self._batch_history = {}
        self._batch_sizes = self.perf_config.get("batch_sizes", {}).copy()
        self._batch_lock = threading.RLock()

        # Get profile from config manager
        self.profile_name = self._get_profile_from_config_manager(self._config_manager)

        self._logger.debug(f"AWS client profile determined: {self.profile_name}")

        try:
            # Initialize session
            self.session = boto3.Session(
                region_name=self.region_name, profile_name=self.profile_name
            )

            # Initialize service client attributes but don't create clients yet
            self._ec2_client = None
            self._autoscaling_client = None
            self._service_quotas_client = None
            self._sts_client = None
            self._cost_explorer_client = None
            self._ssm_client = None
            self._account_id = None
            self._credentials_validated = False

            # Single comprehensive INFO log with all important details
            self._logger.info(
                f"AWS client initialized with region: {self.region_name}, profile: {self.profile_name}, "
                + f"retries: {self.boto_config.retries['max_attempts']}, "
                + f"timeouts: connect={self.boto_config.connect_timeout}s, read={self.boto_config.read_timeout}s"
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]

            if error_code in ["UnauthorizedOperation", "InvalidClientTokenId"]:
                raise AuthorizationError(f"AWS authentication failed: {error_message}")
            elif error_code == "RequestTimeout":
                raise NetworkError(f"AWS connection failed: {error_message}")
            else:
                raise AWSConfigurationError(
                    f"AWS client initialization failed: {error_message}")

    def _get_region_from_config_manager(self, config_manager) -> Optional[str]:
        """
        Get AWS region from ConfigurationManager.

        Args:
            config_manager: ConfigurationManager instance

        Returns:
            AWS region or None if not found
        """
        try:
            # Try to get AWS config from ConfigurationManager
            from src.providers.aws.configuration.config import AWSProviderConfig

            aws_config = config_manager.get_typed(AWSProviderConfig)
            if aws_config and aws_config.region:
                self._logger.debug(
                    f"Using region from ConfigurationManager: {aws_config.region}")
                return aws_config.region
        except Exception as e:
            self._logger.debug(
                f"Could not get region from ConfigurationManager: {str(e)}")

        return None

    def _get_profile_from_config_manager(self, config_manager) -> Optional[str]:
        """
        Get AWS profile from ConfigurationManager using provider selection.

        Args:
            config_manager: ConfigurationManager instance

        Returns:
            AWS profile or None if not found
        """
        try:
            # Use provider selection service from DI container
            from src.application.services.provider_selection_service import ProviderSelectionService
            from src.infrastructure.di.container import get_container

            container = get_container()
            selection_service = container.get(ProviderSelectionService)
            selection_result = selection_service.select_active_provider()

            self._logger.debug(
                f"Provider selection result: {selection_result.provider_type}, {selection_result.provider_instance}")

            # Ensure we have an AWS provider
            if selection_result.provider_type != "aws":
                self._logger.debug(
                    f"Selected provider is not AWS: {selection_result.provider_type}")
                return None

            # Get the provider instance configuration
            provider_config = config_manager.get_provider_config()
            if not provider_config:
                self._logger.debug("No provider config found")
                return None

            # Find the selected provider instance
            for provider in provider_config.providers:
                if provider.name == selection_result.provider_instance:
                    self._logger.debug(
                        f"Found provider {provider.name}, checking config...")
                    # Access profile from provider config dict
                    if hasattr(provider, 'config') and provider.config:
                        self._logger.debug(
                            f"Provider has config: {type(provider.config)}")

                        # Handle both dict and object config
                        if isinstance(provider.config, dict):
                            self._logger.debug(
                                f"Config dict contents: {provider.config}")
                            profile = provider.config.get('profile')
                        else:
                            profile = getattr(provider.config, 'profile', None)

                        if profile:
                            self._logger.debug(
                                f"Using profile from selected provider {provider.name}: {profile}")
                            return profile
                        else:
                            self._logger.debug("No profile found in provider config")
                    else:
                        self._logger.debug("Provider has no config attribute")
                    break
            else:
                self._logger.debug(
                    f"Provider {selection_result.provider_instance} not found in config")

        except Exception as e:
            self._logger.debug(
                f"Could not get profile via provider selection: {str(e)}")
            import traceback
            self._logger.debug(f"Traceback: {traceback.format_exc()}")

        # Fallback: try legacy AWSProviderConfig approach
        try:
            from src.providers.aws.configuration.config import AWSProviderConfig

            aws_config = config_manager.get_typed(AWSProviderConfig)
            if aws_config and aws_config.profile:
                self._logger.debug(
                    f"Using profile from legacy AWSProviderConfig: {aws_config.profile}")
                return aws_config.profile
        except Exception as e:
            self._logger.debug(f"Could not get profile from legacy config: {str(e)}")

        return None

    def _load_performance_config(self, config_manager) -> Dict[str, Any]:
        """
        Load performance configuration from ConfigurationManager.

        Args:
            config_manager: ConfigurationManager instance

        Returns:
            Performance configuration dictionary
        """
        try:
            # Try to get performance config from ConfigurationManager
            from src.config import PerformanceConfig

            perf_config = config_manager.get_typed(PerformanceConfig)
            if perf_config:
                self._logger.debug(
                    "Loaded performance configuration from ConfigurationManager")
                return {
                    "enable_batching": perf_config.enable_batching,
                    "batch_sizes": {
                        "terminate_instances": perf_config.batch_sizes.terminate_instances,
                        "create_tags": perf_config.batch_sizes.create_tags,
                        "describe_instances": perf_config.batch_sizes.describe_instances,
                        "run_instances": perf_config.batch_sizes.run_instances,
                    },
                    "enable_parallel": perf_config.enable_parallel,
                    "max_workers": perf_config.max_workers,
                    "enable_caching": perf_config.enable_caching,
                    "cache_ttl": perf_config.cache_ttl,
                }
        except Exception as e:
            self._logger.debug(
                f"Could not load performance config from ConfigurationManager: {str(e)}"
            )

        # Default configuration
        return {
            "enable_batching": True,
            "batch_sizes": {
                "terminate_instances": 25,
                "create_tags": 20,
                "describe_instances": 25,
                "run_instances": 10,
            },
            "enable_parallel": True,
            "max_workers": 10,
            "enable_caching": True,
            "cache_ttl": 300,
        }

    # Property getters for lazy initialization of AWS service clients
    @property
    def ec2_client(self):
        """Lazy initialization of EC2 client."""
        if self._ec2_client is None:
            self._logger.debug("Initializing EC2 client on first use")
            self._ec2_client = self.session.client("ec2", config=self.boto_config)
        return self._ec2_client

    @property
    def sts_client(self):
        """Lazy initialization of STS client."""
        if self._sts_client is None:
            self._logger.debug("Initializing STS client on first use")
            self._sts_client = self.session.client("sts", config=self.boto_config)
        return self._sts_client

    @property
    def autoscaling_client(self):
        """Lazy initialization of Auto Scaling client."""
        if self._autoscaling_client is None:
            self._logger.debug("Initializing Auto Scaling client on first use")
            self._autoscaling_client = self.session.client(
                "autoscaling", config=self.boto_config)
        return self._autoscaling_client

    @property
    def ssm_client(self):
        """Lazy initialization of SSM client."""
        if self._ssm_client is None:
            self._logger.debug("Initializing SSM client on first use")
            self._ssm_client = self.session.client("ssm", config=self.boto_config)
        return self._ssm_client

    @property
    def iam_client(self):
        """Lazy initialization of IAM client."""
        if not hasattr(self, "_iam_client") or self._iam_client is None:
            self._logger.debug("Initializing IAM client on first use")
            self._iam_client = self.session.client("iam", config=self.boto_config)
        return self._iam_client

    @property
    def elbv2_client(self):
        """Lazy initialization of ELBv2 client."""
        if not hasattr(self, "_elbv2_client") or self._elbv2_client is None:
            self._logger.debug("Initializing ELBv2 client on first use")
            self._elbv2_client = self.session.client("elbv2", config=self.boto_config)
        return self._elbv2_client
