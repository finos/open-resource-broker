"""AMI resolver with caching capabilities."""

import os
from typing import Optional

from config.schemas.performance_schema import PerformanceConfig
from domain.base.dependency_injection import injectable
from domain.base.exceptions import InfrastructureError
from domain.base.ports import ConfigurationPort, LoggingPort
from domain.base.ports.template_resolver_port import TemplateResolverPort
from providers.aws.configuration.template_extension import AMIResolutionConfig
from providers.aws.infrastructure.aws_client import AWSClient
from providers.aws.infrastructure.template.ami_cache import RuntimeAMICache


@injectable
class CachingAMIResolver(TemplateResolverPort):
    """
    AMI resolver with caching and fallback capabilities.

    Resolves SSM parameters to actual AMI IDs with runtime caching
    to avoid duplicate AWS calls. Uses configuration system for
    cache settings and path resolution.
    """

    def __init__(
        self, aws_client: AWSClient, config: ConfigurationPort, logger: LoggingPort
    ) -> None:
        """
        Initialize AMI resolver.

        Args:
            aws_client: AWS client for SSM operations
            config: Configuration port for accessing configuration
            logger: Logger for logging messages
        """
        self._aws_client = aws_client
        self._logger = logger

        # Get AMI resolution configuration from template extension
        try:
            self._ami_config = config.get_typed(AMIResolutionConfig)
        except Exception as e:
            self._logger.warning("Failed to get AMI resolution config: %s", str(e))
            self._ami_config = AMIResolutionConfig(enabled=True, fallback_on_failure=True)

        # Get performance configuration for caching settings
        try:
            perf_config = config.get_typed(PerformanceConfig)
            self._cache_enabled = perf_config.caching.ami_resolution.enabled
            cache_ttl_seconds = perf_config.caching.ami_resolution.ttl_seconds
        except Exception as e:
            self._logger.warning("Failed to get performance config: %s", str(e))
            self._cache_enabled = True
            cache_ttl_seconds = 3600

        # Initialize cache with configuration-driven settings
        cache_file = None
        if self._cache_enabled:
            cache_file = self._resolve_cache_path(config)

        self._cache = RuntimeAMICache(
            persistent_file=cache_file, ttl_minutes=cache_ttl_seconds // 60
        )

        self._logger.debug(
            "AMI resolver initialized: "
            f"enabled={self._ami_config.enabled}, "
            f"cache_enabled={self._cache_enabled}, "
            f"cache_file={cache_file}"
        )

    def _resolve_cache_path(self, config: ConfigurationPort) -> str:
        """Resolve cache file path using configuration system."""
        try:
            work_dir = config.get_work_dir()
            cache_dir = os.path.join(work_dir, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, "ami_cache.json")
        except Exception:
            # Fallback to environment variable or current directory
            workdir = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            cache_dir = os.path.join(workdir, "cache")
            os.makedirs(cache_dir, exist_ok=True)
            return os.path.join(cache_dir, "ami_cache.json")

    def resolve_with_fallback(self, ami_id_or_parameter: str) -> str:
        """
        Resolve AMI ID with caching and fallback.

        Args:
            ami_id_or_parameter: AMI ID, SSM parameter, or alias

        Returns:
            Resolved AMI ID or original parameter if resolution fails and fallback enabled

        Raises:
            InfrastructureError: If resolution fails and fallback disabled
        """
        self._logger.debug("resolve_with_fallback called with: %s", ami_id_or_parameter)

        # Skip resolution if disabled
        if not self._ami_config.enabled:
            self._logger.debug(
                "AMI resolution disabled, returning original: %s", ami_id_or_parameter
            )
            return ami_id_or_parameter

        # Return as-is if already an AMI ID
        if ami_id_or_parameter.startswith("ami-"):
            self._logger.debug("Already AMI ID, returning: %s", ami_id_or_parameter)
            return ami_id_or_parameter

        # Skip if not an SSM parameter
        if not ami_id_or_parameter.startswith("/aws/service/"):
            self._logger.debug("Not SSM parameter, returning original: %s", ami_id_or_parameter)
            return ami_id_or_parameter

        self._logger.debug("Passed all early checks, proceeding with resolution")

        # Check cache first if enabled
        if self._cache_enabled:
            self._logger.debug("Cache enabled, checking cache for: %s", ami_id_or_parameter)
            # Return cached result if available
            cached_ami = self._cache.get(ami_id_or_parameter)
            if cached_ami:
                self._logger.info(
                    "AMI served from cache: %s -> %s", ami_id_or_parameter, cached_ami
                )
                return cached_ami

            # Skip if previously failed
            if self._cache.is_failed(ami_id_or_parameter):
                self._logger.debug(
                    "Previously failed parameter found in cache, clearing and retrying: %s",
                    ami_id_or_parameter,
                )
                # Clear the failed entry and retry
                self._cache._failed.discard(ami_id_or_parameter)
        else:
            self._logger.debug("Cache disabled")

        self._logger.debug("About to attempt resolution for: %s", ami_id_or_parameter)

        # Attempt resolution
        self._logger.debug("Resolving SSM parameter: %s", ami_id_or_parameter)
        try:
            self._logger.debug("Calling _resolve_ssm_parameter for: %s", ami_id_or_parameter)
            ami_id = self._resolve_ssm_parameter(ami_id_or_parameter)
            self._logger.debug("_resolve_ssm_parameter returned: %s", ami_id)

            # Cache successful resolution
            if self._cache_enabled:
                self._cache.set(ami_id_or_parameter, ami_id)

            self._logger.info("AMI resolved from SSM: %s -> %s", ami_id_or_parameter, ami_id)
            return ami_id

        except Exception as e:
            self._logger.warning(
                "Failed to resolve SSM parameter %s: %s", ami_id_or_parameter, str(e)
            )
            self._logger.debug("Exception details: %s: %s", type(e).__name__, str(e))

            # Mark as failed in cache
            if self._cache_enabled:
                self._cache.mark_failed(ami_id_or_parameter)

            # Handle fallback
            if self._ami_config.fallback_on_failure:
                self._logger.info(
                    "Fallback enabled, returning original parameter: %s", ami_id_or_parameter
                )
                return ami_id_or_parameter
            else:
                self._logger.error("Fallback disabled, raising error for %s", ami_id_or_parameter)
                raise InfrastructureError(
                    f"Failed to resolve AMI parameter {ami_id_or_parameter}: {str(e)}"
                )

    def _resolve_ssm_parameter(self, parameter_path: str) -> str:
        """
        Resolve SSM parameter to AMI ID.

        Args:
            parameter_path: SSM parameter path

        Returns:
            Resolved AMI ID

        Raises:
            Exception: If resolution fails
        """
        self._logger.debug("_resolve_ssm_parameter: Starting resolution for %s", parameter_path)
        try:
            # Use the AWS client's SSM client to get the parameter value
            self._logger.debug("Calling SSM get_parameter for: %s", parameter_path)
            response = self._aws_client.ssm_client.get_parameter(Name=parameter_path)
            self._logger.debug("SSM response received: %s", response)

            if "Parameter" not in response or "Value" not in response["Parameter"]:
                raise ValueError(f"Invalid SSM parameter response for {parameter_path}")

            ami_id = response["Parameter"]["Value"]
            self._logger.debug("Extracted AMI ID from response: %s", ami_id)

            # Validate that we got a valid AMI ID
            if not ami_id.startswith("ami-"):
                raise ValueError(
                    f"SSM parameter {parameter_path} resolved to invalid AMI ID: {ami_id}"
                )

            self._logger.debug("Successfully resolved %s to %s", parameter_path, ami_id)
            return ami_id

        except Exception as e:
            self._logger.debug(
                "Exception in _resolve_ssm_parameter: %s: %s", type(e).__name__, str(e)
            )
            raise
            # Re-raise with more context
            raise Exception(f"SSM parameter resolution failed: {str(e)}")

    def get_cache_stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        return self._cache.get_stats()

    def clear_cache(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._logger.info("AMI resolution cache cleared")

    # TemplateResolverPort interface methods

    def resolve_parameter(self, parameter: str) -> Optional[str]:
        """
        Resolve a template parameter.

        Args:
            parameter: Parameter to resolve

        Returns:
            Resolved value or None if resolution fails
        """
        try:
            resolved = self.resolve_with_fallback(parameter)
            # Return None if resolution failed (fallback returned original)
            return resolved if resolved != parameter else None
        except Exception:
            return None

    def is_resolvable(self, parameter: str) -> bool:
        """
        Check if a parameter can be resolved by this resolver.

        Args:
            parameter: Parameter to check

        Returns:
            True if parameter can be resolved
        """
        return (
            self._ami_config.enabled
            and parameter.startswith("/aws/service/")
            and not parameter.startswith("ami-")
        )

    def get_resolver_type(self) -> str:
        """
        Get the type of resolver.

        Returns:
            String identifying the resolver type
        """
        return "ami"
