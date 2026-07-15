"""Azure DefaultAzureCredential authentication strategy.

Uses the shared Azure infrastructure credential factory so auth and
provider-runtime flows construct the same credential shape.
"""

import asyncio
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.adapters.ports.auth import (
    AuthContext,
    AuthPort,
    AuthResult,
    AuthStatus,
)
from orb.infrastructure.di.injectable import injectable
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureAccessTokenProviderProtocol,
    AsyncDefaultAzureAccessTokenProvider,
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
        async_token_provider: Optional[AsyncAzureAccessTokenProviderProtocol] = None,
    ) -> None:
        """Initialise Azure auth with async-first token acquisition."""
        self._logger = logger
        self.client_id = client_id
        self.enabled = enabled
        self._token_provider = token_provider
        self._async_token_provider = async_token_provider
        if self._token_provider is None and self._async_token_provider is None:
            self._async_token_provider = AsyncDefaultAzureAccessTokenProvider(
                client_id=client_id,
                logger=logger,
            )

    @classmethod
    def from_auth_config(cls, auth_config: Any) -> "AzureAuthStrategy":
        """Build an Azure auth strategy from the shared AuthConfig object."""
        from orb.infrastructure.adapters.logging_adapter import LoggingAdapter

        provider_auth = getattr(auth_config, "provider_auth", None)
        azure_cfg = getattr(provider_auth, "azure", None) if provider_auth is not None else None
        client_id = getattr(azure_cfg, "client_id", None) if azure_cfg is not None else None
        enabled = bool(getattr(auth_config, "enabled", True))
        return cls(
            logger=LoggingAdapter(),
            client_id=client_id,
            enabled=enabled,
        )

    def _auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return the active provider's declared auth failure types."""
        if self._async_token_provider is not None:
            return self._async_token_provider.get_auth_error_types()
        if self._token_provider is None:
            return DefaultAzureAccessTokenProvider(
                client_id=self.client_id,
                logger=self._logger,
            ).get_auth_error_types()
        return self._token_provider.get_auth_error_types()

    async def _get_access_token(self, scope: str) -> str:
        """Resolve an ARM token without blocking the event loop."""
        if self._async_token_provider is not None:
            return await self._async_token_provider.get_access_token(scope)
        if self._token_provider is None:
            raise RuntimeError("Azure auth strategy has no token provider configured")
        return await asyncio.to_thread(self._token_provider.get_access_token, scope)

    async def authenticate(self, context: AuthContext) -> AuthResult:
        if not self.enabled:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message="Azure auth strategy disabled",
            )
        try:
            token = await self._get_access_token("https://management.azure.com/.default")

            return AuthResult(
                status=AuthStatus.SUCCESS,
                user_id=self.client_id or "azure-identity",
                token=token,
                user_roles=["provider"],
                metadata={
                    "strategy": "azure_default_credential",
                },
            )
        except self._auth_error_types() as exc:
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
            token = await self._get_access_token("https://management.azure.com/.default")
            return AuthResult(
                status=AuthStatus.SUCCESS,
                token=token,
                metadata={"strategy": "azure_default_credential", "refreshed": True},
            )
        except self._auth_error_types() as exc:
            return AuthResult(
                status=AuthStatus.FAILED,
                error_message=f"Token refresh failed: {exc}",
            )

    async def revoke_token(self, token: str) -> bool:
        """Azure ARM tokens cannot be revoked directly."""
        self._logger.debug("Token revocation not supported for Azure ARM tokens")
        return False

    def get_strategy_name(self) -> str:
        """Return the identifier for the Azure default-credential strategy."""
        return "azure_default_credential"

    def is_enabled(self) -> bool:
        """Return whether this auth strategy is currently enabled."""
        return self.enabled
