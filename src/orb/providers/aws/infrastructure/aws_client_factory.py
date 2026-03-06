"""AWS Client Factory - Provider-specific AWS client creation."""

from typing import Dict

from orb.domain.base.ports import LoggingPort
from orb.providers.aws.infrastructure.aws_client import AWSClient


class AWSClientFactory:
    """Factory for creating provider-specific AWS clients."""

    def __init__(self, logger: LoggingPort):
        self._logger = logger
        self._clients: Dict[str, AWSClient] = {}

    def get_client(self, provider_name: str, aws_config, config_port) -> AWSClient:
        """Get AWS client for specific provider instance."""
        if provider_name not in self._clients:
            self._logger.debug("Creating AWS client for provider: %s", provider_name)
            self._clients[provider_name] = AWSClient(
                config=config_port, logger=self._logger, provider_name=provider_name
            )
        return self._clients[provider_name]

    def cleanup(self) -> None:
        """Clean up all cached clients."""
        for client in self._clients.values():
            try:
                client.cleanup()  # type: ignore[attr-defined]
            except Exception as e:
                self._logger.warning("Failed to cleanup client: %s", e)
        self._clients.clear()
