"""Azure credential construction owned by Azure infrastructure."""

from __future__ import annotations

from typing import Any, Optional, Protocol

from orb.domain.base.ports import LoggingPort


class AzureCredentialProtocol(Protocol):
    """Credential surface required by Azure provider code."""

    def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        """Request an access token for the given scopes."""
        ...

    def close(self) -> None:
        """Release any resources held by this credential."""
        ...


class AsyncAzureCredentialProtocol(Protocol):
    """Async credential surface required by native async Azure SDK flows."""

    async def get_token(self, *scopes: str, **kwargs: Any) -> Any:
        """Asynchronously request an access token for the given scopes."""
        ...

    async def close(self) -> None:
        """Release any resources held by this async credential."""
        ...


class AzureAccessTokenProviderProtocol(Protocol):
    """Short-lived Azure token provider used by auth flows."""

    def get_access_token(self, scope: str) -> str:
        """Return a raw access-token string for the given scope."""
        ...

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return exception types that signal authentication failures."""
        ...


class AsyncAzureAccessTokenProviderProtocol(Protocol):
    """Async short-lived Azure token provider used by native async auth flows."""

    async def get_access_token(self, scope: str) -> str:
        """Return a raw access-token string for the given scope."""
        ...

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return exception types that signal authentication failures."""
        ...


class AzureCredentialAccessTokenProvider(AzureAccessTokenProviderProtocol):
    """Adapt an existing Azure credential to the token-provider protocol."""

    def __init__(self, credential: AzureCredentialProtocol) -> None:
        self._credential = credential

    def get_access_token(self, scope: str) -> str:
        """Delegate token retrieval to the wrapped credential."""
        token = self._credential.get_token(scope)
        return token.token

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return Azure SDK credential error types."""
        return get_default_azure_credential_error_types()


class AsyncAzureCredentialAccessTokenProvider(AsyncAzureAccessTokenProviderProtocol):
    """Adapt an async Azure credential to the async token-provider protocol."""

    def __init__(self, credential: AsyncAzureCredentialProtocol) -> None:
        self._credential = credential

    async def get_access_token(self, scope: str) -> str:
        """Delegate token retrieval to the wrapped async credential."""
        token = await self._credential.get_token(scope)
        return token.token

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return Azure SDK credential error types."""
        return get_default_azure_credential_error_types()


def format_import_error(package_name: str, exc: ImportError) -> str:
    """Return a precise dependency error message without masking nested imports."""
    message = str(exc).strip()
    if not message:
        return f"{package_name} package is not installed"
    return f"{package_name} dependency error: {message}"


def get_default_azure_credential_error_types() -> tuple[type[Exception], ...]:
    """Return the expected Azure SDK exception types for credential operations."""
    try:
        from azure.core.exceptions import ClientAuthenticationError
        from azure.identity import CredentialUnavailableError
    except ImportError:
        return ()

    return CredentialUnavailableError, ClientAuthenticationError


def _default_credential_kwargs(*, client_id: Optional[str]) -> dict[str, Any]:
    """Build the shared DefaultAzureCredential kwargs for sync and async factories."""
    credential_kwargs: dict[str, Any] = {}
    if client_id:
        credential_kwargs["managed_identity_client_id"] = client_id
    return credential_kwargs


def create_default_azure_credential(
    *,
    client_id: Optional[str],
    logger: LoggingPort,
) -> AzureCredentialProtocol:
    """Create the canonical Azure DefaultAzureCredential for ORB Azure flows."""
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        logger.error(format_import_error("azure-identity", exc))
        raise

    credential_kwargs = _default_credential_kwargs(client_id=client_id)

    try:
        credential = DefaultAzureCredential(**credential_kwargs)
    except ImportError as exc:
        logger.error(format_import_error("azure-identity", exc))
        raise
    logger.info("Azure DefaultAzureCredential initialised")
    return credential


def create_default_azure_credential_async(
    *,
    client_id: Optional[str],
    logger: LoggingPort,
) -> AsyncAzureCredentialProtocol:
    """Create the canonical async Azure DefaultAzureCredential for ORB Azure flows."""
    try:
        from azure.identity.aio import DefaultAzureCredential
    except ImportError as exc:
        logger.error(format_import_error("azure-identity", exc))
        raise

    credential_kwargs = _default_credential_kwargs(client_id=client_id)

    try:
        credential = DefaultAzureCredential(**credential_kwargs)
    except ImportError as exc:
        logger.error(format_import_error("azure-identity", exc))
        raise
    logger.info("Async Azure DefaultAzureCredential initialised")
    return credential


class DefaultAzureAccessTokenProvider(AzureAccessTokenProviderProtocol):
    """Resolve Azure access tokens with short-lived credentials."""

    def __init__(
        self,
        *,
        client_id: Optional[str],
        logger: LoggingPort,
    ) -> None:
        self._client_id = client_id
        self._logger = logger

    def get_access_token(self, scope: str) -> str:
        """Create a short-lived credential, fetch a token, then close it."""
        credential = create_default_azure_credential(
            client_id=self._client_id,
            logger=self._logger,
        )
        try:
            token = credential.get_token(scope)
            return token.token
        finally:
            credential.close()

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return Azure SDK and ImportError types for credential failures."""
        return ImportError, *get_default_azure_credential_error_types()


class AsyncDefaultAzureAccessTokenProvider(AsyncAzureAccessTokenProviderProtocol):
    """Resolve Azure access tokens with short-lived async credentials."""

    def __init__(
        self,
        *,
        client_id: Optional[str],
        logger: LoggingPort,
    ) -> None:
        self._client_id = client_id
        self._logger = logger

    async def get_access_token(self, scope: str) -> str:
        """Create a short-lived async credential, fetch a token, then close it."""
        credential = create_default_azure_credential_async(
            client_id=self._client_id,
            logger=self._logger,
        )
        try:
            token = await credential.get_token(scope)
            return token.token
        finally:
            await credential.close()

    def get_auth_error_types(self) -> tuple[type[Exception], ...]:
        """Return Azure SDK and ImportError types for credential failures."""
        return ImportError, *get_default_azure_credential_error_types()
