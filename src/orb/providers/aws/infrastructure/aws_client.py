"""AWS client wrapper with additional functionality."""

import threading
from typing import TYPE_CHECKING, Any, Optional, TypeVar

from botocore.config import Config
from botocore.exceptions import ClientError

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import ConfigurationPort, LoggingPort
from orb.monitoring.metrics import MetricsCollector
from orb.providers.aws.exceptions.aws_exceptions import (
    AuthorizationError,
    AWSConfigurationError,
    NetworkError,
)
from orb.providers.aws.infrastructure.instrumentation.botocore_metrics import BotocoreMetricsHandler

if TYPE_CHECKING:
    from orb.providers.aws.configuration.config import AWSProviderConfig

# Type variable for generic function return type
T = TypeVar("T")


@injectable
class AWSClient:
    """Wrapper for AWS service clients with additional functionality."""

    def __init__(
        self,
        config: ConfigurationPort,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
        metrics: Optional[MetricsCollector] = None,
        active_provider_name_resolver: Optional[Any] = None,
    ) -> None:
        """
        Initialize AWS client wrapper with optional metrics collection.

        Args:
            config: Configuration port for accessing configuration
            logger: Logger for logging messages
            provider_name: Specific provider instance name (for multi-provider support)
            metrics: Optional metrics collector for AWS API instrumentation
            active_provider_name_resolver: Optional callable returning the active provider
                instance name. Replaces the service-locator pattern: when provider_name
                is None this callable is invoked instead of get_container().
        """
        self._config_manager = config
        self._logger = logger
        self._provider_name = provider_name
        self._active_provider_name_resolver = active_provider_name_resolver
        self._aws_config: Optional[AWSProviderConfig] = None
        # Tracks whether we've attempted provider selection; we cache failures too.
        # This avoids repeated DI lookups and log spam when selection is unavailable.
        self._aws_config_loaded = False

        # Get region from configuration
        self.region_name = self._get_region_from_config_manager() or "eu-west-1"

        self._logger.debug("AWS client region determined: %s", self.region_name)

        aws_provider_config = self._get_selected_aws_provider_config()
        max_attempts = (
            int(aws_provider_config.request_retry_attempts) + 1 if aws_provider_config else 1
        )
        connect_timeout = int(aws_provider_config.aws_connect_timeout) if aws_provider_config else 5
        read_timeout = int(aws_provider_config.aws_read_timeout) if aws_provider_config else 10

        # Configure retry settings
        self.boto_config = Config(
            region_name=self.region_name,
            retries={
                "max_attempts": max_attempts,
                "mode": "adaptive",
            },
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )

        # Load performance configuration
        self.perf_config = self._load_performance_config()

        # Initialize resource cache
        self._resource_cache: dict[str, Any] = {}
        self._cache_lock = threading.RLock()

        # Initialize adaptive batch sizing history
        self._batch_history: dict[str, Any] = {}
        self._batch_sizes = self.perf_config.get("batch_sizes", {}).copy()
        self._batch_lock = threading.RLock()

        # Get profile from config manager
        self.profile_name = self._get_profile_from_config_manager()

        self._logger.debug("AWS client profile determined: %s", self.profile_name)

        try:
            # Initialize session
            from orb.providers.aws.session_factory import AWSSessionFactory

            self.session = AWSSessionFactory.create_session(
                profile=self.profile_name, region=self.region_name
            )

            # Initialize service client attributes but don't create clients yet
            self._ec2_client = None
            self._autoscaling_client = None
            self._sts_client = None
            self._cost_explorer_client = None
            self._ssm_client = None
            self._account_id = None
            self._credentials_validated = False

            # Initialize metrics handler if metrics collector is available and AWS metrics are enabled
            self._metrics_handler: Optional[BotocoreMetricsHandler] = None
            if metrics and self._should_enable_aws_metrics():
                aws_metrics_cfg = (
                    metrics.config.get("provider_metrics", {}) if hasattr(metrics, "config") else {}
                )
                self._metrics_handler = BotocoreMetricsHandler(metrics, logger, aws_metrics_cfg)
                self._metrics_handler.register_events(self.session)
                logger.info("AWS API metrics collection enabled")
            else:
                logger.debug(
                    "AWS API metrics collection disabled - no MetricsCollector provided or AWS_METRICS_ENABLED=false"
                )

            # Single comprehensive INFO log with all important details
            self._logger.info(
                "AWS client initialized with region: %s, profile: %s, retries: %d, timeouts: connect=%ds, read=%ds",
                self.region_name,
                self.profile_name or "credential-chain",
                max_attempts,
                connect_timeout,
                read_timeout,
            )

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            error_message = e.response["Error"]["Message"]

            if error_code in ["UnauthorizedOperation", "InvalidClientTokenId"]:
                raise AuthorizationError(f"AWS authentication failed: {error_message}")
            elif error_code == "RequestTimeout":
                raise NetworkError(f"AWS connection failed: {error_message}")
            else:
                raise AWSConfigurationError(f"AWS client initialization failed: {error_message}")

    def _get_region_from_config_manager(self) -> Optional[str]:
        """
        Get AWS region from ConfigurationManager.

        Returns:
            AWS region or None if not found
        """
        aws_provider_config = self._get_selected_aws_provider_config()
        if aws_provider_config and aws_provider_config.region:
            self._logger.debug(
                "Using region from selected AWS config: %s", aws_provider_config.region
            )
            return aws_provider_config.region

        return None

    def _get_profile_from_config_manager(self) -> Optional[str]:
        """
        Get AWS profile from ConfigurationManager using provider selection.

        Returns:
            AWS profile or None if not found
        """
        aws_provider_config = self._get_selected_aws_provider_config()
        if aws_provider_config and aws_provider_config.profile:
            self._logger.debug(
                "Using profile from selected AWS config: %s",
                aws_provider_config.profile,
            )
            return aws_provider_config.profile

        return None

    def _get_selected_aws_provider_config(self) -> Optional["AWSProviderConfig"]:
        """Get selected AWS provider configuration.

        Primary path: use provider registry to pick the active instance and
        build AWSProviderConfig from its config payload.
        Fallback path: use the legacy ConfigurationManager.get_typed(AWSProviderConfig) resolution.

        This function attempts to get config only once and stores _aws_config_loaded=True
        even if retrieval failed.
        """
        if self._aws_config_loaded:
            return self._aws_config

        self._aws_config_loaded = True

        try:
            from orb.providers.aws.configuration.config import AWSProviderConfig
        except Exception as e:
            self._logger.debug("Could not import AWSProviderConfig: %s", str(e))
            return None

        try:
            # Use specific provider name if provided, otherwise use provider selection
            if self._provider_name:
                provider_name = self._provider_name
                self._logger.debug("Using specific provider: %s", provider_name)
            # Use injected resolver if available, otherwise fall through to legacy path
            elif self._active_provider_name_resolver is not None:
                try:
                    resolved = self._active_provider_name_resolver()
                    if resolved:
                        provider_name = resolved
                        self._logger.debug(
                            "Resolved active provider name via injected resolver: %s",
                            provider_name,
                        )
                    else:
                        self._logger.debug(
                            "Injected resolver returned no provider name; skipping primary path"
                        )
                        return None
                except Exception as e:
                    self._logger.debug("Injected resolver raised an exception: %s", str(e))
                    return None
            else:
                # No resolver and no explicit provider_name — skip primary path
                self._logger.debug(
                    "No provider_name and no resolver provided; skipping primary config path"
                )
                return None

            # Get the provider instance configuration
            provider_config = self._config_manager.get_provider_config()
            if not provider_config:
                self._logger.debug("No provider config found")
            else:
                # Find the specified provider instance
                for provider in provider_config.providers:
                    if provider.name == provider_name:
                        self._logger.debug(
                            "Found provider %s, building AWSProviderConfig", provider.name
                        )
                        if not hasattr(provider, "config") or not provider.config:
                            self._logger.debug("Provider has no config attribute")
                            break

                        if isinstance(provider.config, AWSProviderConfig):
                            self._aws_config = provider.config
                            return self._aws_config

                        if isinstance(provider.config, dict):
                            self._logger.debug("Config dict contents: %s", provider.config)
                            self._aws_config = AWSProviderConfig(**provider.config)
                            return self._aws_config

                        config_data = None
                        if hasattr(provider.config, "model_dump"):
                            config_data = provider.config.model_dump()
                        elif hasattr(provider.config, "dict"):
                            config_data = provider.config.dict()
                        elif hasattr(provider.config, "__dict__"):
                            config_data = provider.config.__dict__

                        if isinstance(config_data, dict):
                            self._aws_config = AWSProviderConfig(**config_data)
                            return self._aws_config

                        self._logger.debug(
                            "Unsupported provider config type: %s", type(provider.config)
                        )
                        break
                else:
                    self._logger.debug(
                        "Provider %s not found in config",
                        provider_name,
                    )
        except AWSConfigurationError:
            raise
        except Exception as e:
            self._logger.debug("Could not get AWS config via provider selection: %s", str(e))

        # Fallback: try legacy AWSProviderConfig approach
        try:
            aws_provider_config = self._config_manager.get_typed(AWSProviderConfig)  # type: ignore[call-arg]
            self._aws_config = aws_provider_config
            return aws_provider_config
        except Exception as e:
            self._logger.debug("Could not get AWS config from legacy config: %s", str(e))

        return None

    def _load_performance_config(self) -> dict[str, Any]:
        """
        Load performance configuration from ConfigurationManager.

        Batch sizes are sourced from AWSProviderConfig (AWS-specific).

        Returns:
            Performance configuration dictionary
        """
        from orb.providers.aws.configuration.batch_sizes_config import AWSBatchSizesConfig

        # Resolve batch sizes from AWSProviderConfig if available, else use defaults
        batch_sizes: dict[str, int] = AWSBatchSizesConfig().model_dump()  # type: ignore[call-arg]
        aws_provider_config = self._get_selected_aws_provider_config()
        if aws_provider_config is not None and hasattr(aws_provider_config, "batch_sizes"):
            batch_sizes = aws_provider_config.batch_sizes.model_dump()

        try:
            from orb.config import PerformanceConfig

            perf_config = self._config_manager.get_typed(PerformanceConfig)  # type: ignore[call-arg]
            if perf_config:
                self._logger.debug("Loaded performance configuration from ConfigurationManager")
                return {
                    "enable_batching": perf_config.enable_batching,
                    "batch_sizes": batch_sizes,
                    "enable_parallel": perf_config.enable_parallel,
                    "max_workers": perf_config.max_workers,
                }
        except Exception as e:
            self._logger.debug(
                "Could not load performance config from ConfigurationManager: %s",
                str(e),
            )

        # Default configuration
        return {
            "enable_batching": True,
            "batch_sizes": batch_sizes,
            "enable_parallel": True,
            "max_workers": 10,
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
            self._autoscaling_client = self.session.client("autoscaling", config=self.boto_config)
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

    def _should_enable_aws_metrics(self) -> bool:
        """Check if AWS metrics should be enabled based on configuration."""
        try:
            # Get metrics configuration from ConfigurationPort
            metrics_config = self._config_manager.get_metrics_config()
            aws_cfg = (
                metrics_config.get("provider_metrics", {})
                if isinstance(metrics_config, dict)
                else {}
            )
            aws_metrics_enabled = aws_cfg.get("provider_metrics_enabled", True)
            self._logger.debug("provider_metrics_enabled flag value: %s", aws_metrics_enabled)
            return aws_metrics_enabled
        except Exception as e:
            self._logger.debug("Could not check provider_metrics_enabled flag: %s", e)
            return True  # Default to enabled

    def get_metrics_stats(self) -> dict:
        """Get metrics collection statistics."""
        if self._metrics_handler:
            stats = self._metrics_handler.get_stats()
            stats["metrics_enabled"] = True
            return stats
        return {"metrics_enabled": False}

    def execute_with_circuit_breaker(
        self, service: str, operation: str, func: Any, *args: Any, **kwargs: Any
    ) -> Any:
        """Execute an AWS operation with circuit breaker protection."""
        return func(*args, **kwargs)
