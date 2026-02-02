"""AWS Health Check Service - Handles provider health monitoring and credentials."""

import time
from typing import TYPE_CHECKING, Any, Optional

from domain.base.ports import LoggingPort
from providers.base.strategy import ProviderHealthStatus

if TYPE_CHECKING:
    from providers.aws.infrastructure.aws_client import AWSClient
    from providers.aws.configuration.config import AWSProviderConfig


class AWSHealthCheckService:
    """Service for AWS provider health monitoring and credential management."""

    def __init__(self, aws_client: "AWSClient", config: "AWSProviderConfig", logger: LoggingPort):
        self._aws_client = aws_client
        self._config = config
        self._logger = logger

    def check_health(self) -> ProviderHealthStatus:
        """Check AWS provider health status."""
        start_time = time.time()

        try:
            if not self._aws_client:
                return ProviderHealthStatus.unhealthy(
                    "AWS client initialization failed", {"error": "client_initialization_failed"}
                )

            # Check dry-run mode
            from infrastructure.mocking.dry_run_context import is_dry_run_active

            if is_dry_run_active():
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.healthy(
                    f"AWS provider healthy (DRY-RUN) - Region: {self._config.region}",
                    response_time_ms,
                )

            # Perform AWS connectivity check
            try:
                from providers.aws.infrastructure.dry_run_adapter import aws_dry_run_context

                with aws_dry_run_context():
                    response = self._aws_client.sts_client.get_caller_identity()
                    account_id = response.get("Account", "unknown")

                    response_time_ms = (time.time() - start_time) * 1000

                    return ProviderHealthStatus.healthy(
                        f"AWS provider healthy - Account: {account_id}, Region: {self._config.region}",
                        response_time_ms,
                    )

            except Exception as e:
                response_time_ms = (time.time() - start_time) * 1000
                return ProviderHealthStatus.unhealthy(
                    f"AWS connectivity check failed: {e}",
                    {
                        "error": str(e),
                        "region": self._config.region,
                        "response_time_ms": response_time_ms,
                    },
                )

        except Exception as e:
            response_time_ms = (time.time() - start_time) * 1000
            return ProviderHealthStatus.unhealthy(
                f"Health check error: {e}",
                {"error": str(e), "response_time_ms": response_time_ms},
            )

    def get_available_credential_sources(self) -> list[dict]:
        """Get available AWS credential sources."""
        from providers.aws.profile_discovery import get_available_profiles
        return get_available_profiles()

    def test_credentials(self, credential_source: Optional[str] = None, **kwargs) -> dict:
        """Test AWS credentials."""
        from providers.aws.session_factory import AWSSessionFactory
        region = kwargs.get("region")
        return AWSSessionFactory.discover_credentials(credential_source, region)

    def get_credential_requirements(self) -> dict:
        """AWS requires region."""
        return {"region": {"required": True, "description": "AWS region"}}