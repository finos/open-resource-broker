"""CycleCloud session settings resolution for Azure infrastructure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from orb.providers.azure.infrastructure.credential_factory import (
    AsyncAzureAccessTokenProviderProtocol,
)
from orb.providers.azure.infrastructure.cyclecloud_session import (
    CycleCloudCredentialData,
    CycleCloudRequestContext,
    CycleCloudSessionSettings,
)


class CycleCloudSessionBuilder:
    """Resolve CycleCloud credential and transport settings before session creation."""

    def __init__(
        self,
        *,
        cc_url: Optional[str],
        verify_ssl: Optional[bool],
        template: Optional[AzureTemplate],
        request_context: Optional[CycleCloudRequestContext],
        provider_cfg: Optional[AzureProviderConfig],
        async_token_provider: Optional[AsyncAzureAccessTokenProviderProtocol] = None,
    ):
        self._cc_url = cc_url
        self._verify_ssl = verify_ssl
        self._template = template
        self._request_context = request_context or CycleCloudRequestContext()
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
                f"CycleCloud credential file not found: {path}",
                url=None,
            ) from exc
        except json.JSONDecodeError as exc:
            raise CycleCloudConnectionError(
                f"CycleCloud credential file is not valid JSON: {path}",
                url=None,
            ) from exc
        except OSError as exc:
            raise CycleCloudConnectionError(
                f"Failed to read CycleCloud credential file {path}: {exc}",
                url=None,
            ) from exc

        if not isinstance(data, dict):
            raise CycleCloudConnectionError(
                f"CycleCloud credential file must contain a JSON object: {path}",
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

    def _resolve_credential_path(self) -> Optional[str]:
        provider_cyclecloud = self._provider_cyclecloud()
        credential_path = self._resolve_cascaded_value(
            None if self._template is None else self._template.cyclecloud_credential_path,
            self._request_context.cyclecloud_credential_path,
            None if provider_cyclecloud is None else provider_cyclecloud.credential_path,
        )
        if credential_path in (None, ""):
            return None
        return str(credential_path)

    def _resolve_transport_settings(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> tuple[str, bool]:
        provider_cyclecloud = self._provider_cyclecloud()
        resolved_url = self._resolve_cascaded_value(
            self._cc_url,
            None if self._template is None else self._template.cyclecloud_url,
            self._request_context.cyclecloud_url,
            None if provider_cyclecloud is None else provider_cyclecloud.url,
            credential_data.url,
        )

        verify_resolved = self._resolve_cascaded_value(
            self._verify_ssl,
            None if self._template is None else self._template.cyclecloud_verify_ssl,
            self._request_context.cyclecloud_verify_ssl,
            None if provider_cyclecloud is None else provider_cyclecloud.verify_ssl,
            credential_data.verify_ssl,
            default=True,
        )

        if not resolved_url:
            raise CycleCloudConnectionError(
                "cyclecloud_url is required in the template, request context, or provider configuration.",
                url=None,
            )

        return str(resolved_url).rstrip("/"), bool(verify_resolved)

    def _resolve_auth_mode(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> Optional[str]:
        provider_cyclecloud = self._provider_cyclecloud()
        auth_mode = self._resolve_cascaded_value(
            None if self._template is None else self._template.cyclecloud_auth_mode,
            self._request_context.cyclecloud_auth_mode,
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
            None if self._template is None else self._template.cyclecloud_aad_scope,
            self._request_context.cyclecloud_aad_scope,
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
        credential_path = self._resolve_credential_path()
        credential_data = (
            self._load_credential_file(credential_path)
            if credential_path
            else CycleCloudCredentialData()
        )
        base_url, verify_ssl = self._resolve_transport_settings(credential_data)
        auth_mode = self._resolve_auth_mode(credential_data)
        return CycleCloudSessionSettings(
            base_url=base_url,
            verify_ssl=verify_ssl,
            auth_mode=auth_mode,
            credential_path=credential_path,
        )

    async def resolve_async_auth(
        self,
        *,
        settings: CycleCloudSessionSettings,
    ) -> tuple[dict[str, str], httpx.BasicAuth | None, str]:
        """Resolve auth settings for an ``httpx.AsyncClient`` transport."""
        if settings.auth_mode == "ssh":
            raise CycleCloudConnectionError(
                "cyclecloud_auth_mode=ssh is not supported. Configure CycleCloud API credentials instead.",
                url=settings.base_url,
            )

        credential_data = (
            self._load_credential_file(settings.credential_path)
            if settings.credential_path
            else CycleCloudCredentialData()
        )
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
                "cyclecloud_auth_mode=bearer requested but no bearer token could be resolved.",
                url=settings.base_url,
            )
        raise CycleCloudConnectionError(
            "No CycleCloud auth method resolved. Provide username/password or a bearer token/Azure credential.",
            url=settings.base_url,
        )
