"""Azure DefaultAzureCredential authentication strategy.

Uses the shared Azure infrastructure credential factory so auth and
provider-runtime flows construct the same credential shape.
"""

from typing import Optional

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.providers.azure.infrastructure.credential_factory import (
    AzureAccessTokenProviderProtocol,
    DefaultAzureAccessTokenProvider,
)


@injectable
class AzureAuthStrategy(AuthPort):
    """Authentication strategy using Azure DefaultAzureCredential."""

    def __init__(
        self,
        logger: LoggingPort,
        client_id: Optional[str] = None,
        enabled: bool = True,
        token_provider: Optional[AzureAccessTokenProviderProtocol] = None,
    ) -> None:
        self._logger = logger
        self.client_id = client_id
        self.enabled = enabled
        self._token_provider = token_provider or DefaultAzureAccessTokenProvider(
            client_id=client_id,
            logger=logger,
        )

    async def authenticate(self, context: AuthContext) -> AuthResult:
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Azure auth strategy disabled",
            )
        try:
            token = self._token_provider.get_access_token(
                "https://management.azure.com/.default"
            )

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=self.client_id or "azure-identity",
                token=token,
                user_roles=["provider"],
                metadata={
                    "strategy": "azure_default_credential",
                },
            )
        except self._token_provider.get_auth_error_types() as exc:
            self._logger.error("Azure authentication failed: %s", exc)
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message=f"Azure authentication failed: {exc}",
            )

    async def validate_token(self, token: str) -> AuthResult:
        """Token validation is handled by Azure SDK internally."""
        return AuthResult(
            status=AuthStatus.SUCCESS,
            token=token,
            metadata={"strategy": "azure_default_credential"},
        )

    async def refresh_token(self, refresh_token: str) -> AuthResult:
        """DefaultAzureCredential handles token refresh automatically."""
        try:
            token = self._token_provider.get_access_token(
                "https://management.azure.com/.default"
            )
            return AuthResult(
                status=AuthStatus.SUCCESS,
                token=token,
                metadata={"strategy": "azure_default_credential", "refreshed": True},
            )
        except self._token_provider.get_auth_error_types() as exc:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message=f"Token refresh failed: {exc}",
            )

    async def revoke_token(self, token: str) -> bool:
        """Azure ARM tokens cannot be revoked directly."""
        self._logger.debug("Token revocation not supported for Azure ARM tokens")
        return False

    def get_strategy_name(self) -> str:
        return "azure_default_credential"

    def is_enabled(self) -> bool:
        return self.enabled
