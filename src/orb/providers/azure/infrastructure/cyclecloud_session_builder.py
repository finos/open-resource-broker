"""CycleCloud session settings resolution for Azure infrastructure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureAccessTokenProviderProtocol,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudSessionSettings,
)

_CYCLECLOUD_CREDENTIAL_FIELDS = frozenset(
    {
        "url",
        "verify_ssl",
        "auth_mode",
        "username",
        "password",
        "bearer_token",
        "aad_scope",
    }
)


class CycleCloudSessionBuilder:
    """Resolve CycleCloud credential and transport settings before session creation."""

    def __init__(
        self,
        *,
        provider_cfg: Optional[AzureProviderConfig],
        async_token_provider: Optional[AsyncAzureAccessTokenProviderProtocol] = None,
    ):
        self._provider_cfg = provider_cfg
        self._async_token_provider = async_token_provider

    @classmethod
    def _load_credential_file(cls, credential_path: str) -> CycleCloudCredentialData:
        path = Path(credential_path).expanduser()
        try:
            with path.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise CycleCloudConnectionError(
                "Configured CycleCloud credential file was not found.",
                url=None,
            ) from exc
        except json.JSONDecodeError as exc:
            raise CycleCloudConnectionError(
                "Configured CycleCloud credential file is not valid JSON.",
                url=None,
            ) from exc
        except OSError as exc:
            raise CycleCloudConnectionError(
                "Failed to read the configured CycleCloud credential file.",
                url=None,
            ) from exc

        if not isinstance(data, dict):
            raise CycleCloudConnectionError(
                "Configured CycleCloud credential file must contain a JSON object.",
                url=None,
            )

        unsupported_fields = set(data).difference(_CYCLECLOUD_CREDENTIAL_FIELDS)
        if unsupported_fields:
            field_list = ", ".join(sorted(unsupported_fields))
            raise CycleCloudConnectionError(
                f"Configured CycleCloud credential file contains unsupported fields: {field_list}.",
                url=None,
            )

        return CycleCloudCredentialData.from_mapping(data)

    def _provider_cyclecloud(self):
        if self._provider_cfg is None:
            return None
        return self._provider_cfg.cyclecloud

    @staticmethod
    def _resolve_cascaded_value(
        *sources: object,
        default: object = None,
    ) -> object:
        """Return the first configured value from the resolution cascade."""
        for value in sources:
            if value not in (None, ""):
                return value
        return default

    async def _get_azure_bearer_token_async(self, scopes: list[str]) -> Optional[str]:
        if self._async_token_provider is None:
            return None
        for scope in scopes:
            if not scope:
                continue
            try:
                token = await self._async_token_provider.get_access_token(scope)
                if token:
                    return token
            except self._async_token_provider.get_auth_error_types():
                continue
        return None

    def _load_provider_credential_data(self) -> CycleCloudCredentialData:
        provider_cyclecloud = self._provider_cyclecloud()
        credential_path = (
            None if provider_cyclecloud is None else provider_cyclecloud.credential_path
        )
        if credential_path in (None, ""):
            return CycleCloudCredentialData()
        return self._load_credential_file(str(credential_path))

    def _resolve_transport_settings(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> tuple[str, bool]:
        provider_cyclecloud = self._provider_cyclecloud()
        resolved_url = self._resolve_cascaded_value(
            None if provider_cyclecloud is None else provider_cyclecloud.url,
            credential_data.url,
        )

        verify_resolved = self._resolve_cascaded_value(
            None if provider_cyclecloud is None else provider_cyclecloud.verify_ssl,
            credential_data.verify_ssl,
            default=True,
        )

        if not resolved_url:
            raise CycleCloudConnectionError(
                "cyclecloud.url is required in provider configuration or its credential file.",
                url=None,
            )

        return str(resolved_url).rstrip("/"), bool(verify_resolved)

    def _resolve_auth_mode(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> Optional[str]:
        provider_cyclecloud = self._provider_cyclecloud()
        auth_mode = self._resolve_cascaded_value(
            None if provider_cyclecloud is None else provider_cyclecloud.auth_mode,
            credential_data.auth_mode,
        )
        return str(auth_mode).strip().lower() if auth_mode else None

    async def _resolve_bearer_token_async(
        self,
        *,
        base_url: str,
        credential_data: CycleCloudCredentialData,
    ) -> Optional[str]:
        if credential_data.bearer_token:
            return str(credential_data.bearer_token)

        provider_cyclecloud = self._provider_cyclecloud()
        aad_scope = self._resolve_cascaded_value(
            None if provider_cyclecloud is None else provider_cyclecloud.aad_scope,
            credential_data.aad_scope,
        )

        parsed = urlparse(base_url)
        host_scope = (
            f"{parsed.scheme}://{parsed.netloc}/.default" if parsed.scheme and parsed.netloc else ""
        )
        scopes = [str(aad_scope)] if aad_scope else []
        scopes.extend([host_scope, "https://management.azure.com/.default"])
        return await self._get_azure_bearer_token_async(scopes)

    def build_settings(self) -> CycleCloudSessionSettings:
        """Resolve credential, transport, and auth settings into a session config."""
        credential_data = self._load_provider_credential_data()
        base_url, verify_ssl = self._resolve_transport_settings(credential_data)
        auth_mode = self._resolve_auth_mode(credential_data)
        return CycleCloudSessionSettings(
            base_url=base_url,
            verify_ssl=verify_ssl,
            auth_mode=auth_mode,
            credential_data=credential_data,
        )

    async def resolve_async_auth(
        self,
        *,
        settings: CycleCloudSessionSettings,
    ) -> tuple[dict[str, str], httpx.BasicAuth | None, str]:
        """Resolve auth settings for an ``httpx.AsyncClient`` transport."""
        if settings.auth_mode == "ssh":
            raise CycleCloudConnectionError(
                "cyclecloud.auth_mode=ssh is not supported. "
                "Configure CycleCloud API credentials instead.",
                url=settings.base_url,
            )

        credential_data = settings.credential_data
        if credential_data.username and credential_data.password and settings.auth_mode != "bearer":
            return {}, httpx.BasicAuth(credential_data.username, credential_data.password), "basic"

        bearer_token = await self._resolve_bearer_token_async(
            base_url=settings.base_url,
            credential_data=credential_data,
        )
        if bearer_token:
            return {"Authorization": f"Bearer {bearer_token}"}, None, "bearer"
        if settings.auth_mode == "bearer":
            raise CycleCloudConnectionError(
                "cyclecloud.auth_mode=bearer requested but no bearer token could be resolved.",
                url=settings.base_url,
            )
        raise CycleCloudConnectionError(
            "No CycleCloud auth method resolved. Provide username/password or a bearer token/Azure credential.",
            url=settings.base_url,
        )
