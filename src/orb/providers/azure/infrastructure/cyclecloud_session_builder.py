"""CycleCloud session settings resolution for Azure infrastructure."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from orb.providers.azure.configuration.config import AzureProviderConfig
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.azure.exceptions.azure_exceptions import CycleCloudConnectionError
from orb.providers.azure.infrastructure.credential_factory import (
    AzureAccessTokenProviderProtocol,
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
        token_provider: Optional[AzureAccessTokenProviderProtocol] = None,
    ):
        self._cc_url = cc_url
        self._verify_ssl = verify_ssl
        self._template = template
        self._request_context = request_context or CycleCloudRequestContext()
        self._provider_cfg = provider_cfg
        self._token_provider = token_provider

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

    def _get_azure_bearer_token(self, scopes: list[str]) -> Optional[str]:
        if self._token_provider is None:
            return None
        for scope in scopes:
            if not scope:
                continue
            try:
                token = self._token_provider.get_access_token(scope)
                if token:
                    return token
            except self._token_provider.get_auth_error_types():
                continue
        return None

    def _resolve_credential_path(self) -> Optional[str]:
        credential_path = None
        if self._template is not None:
            credential_path = self._template.cyclecloud_credential_path
        if credential_path in (None, ""):
            credential_path = self._request_context.cyclecloud_credential_path
        if credential_path in (None, ""):
            provider_cyclecloud = self._provider_cyclecloud()
            credential_path = (
                None if provider_cyclecloud is None else provider_cyclecloud.credential_path
            )
        if credential_path in (None, ""):
            return None
        return str(credential_path)

    def _resolve_transport_settings(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> tuple[str, bool]:
        resolved_url = self._cc_url
        if resolved_url in (None, "") and self._template is not None:
            resolved_url = self._template.cyclecloud_url
        if resolved_url in (None, ""):
            resolved_url = self._request_context.cyclecloud_url
        if resolved_url in (None, ""):
            provider_cyclecloud = self._provider_cyclecloud()
            resolved_url = None if provider_cyclecloud is None else provider_cyclecloud.url
        resolved_url = resolved_url or credential_data.url

        verify_resolved = self._verify_ssl
        if verify_resolved is None:
            if self._template is not None:
                verify_resolved = self._template.cyclecloud_verify_ssl
            if verify_resolved in (None, ""):
                verify_resolved = self._request_context.cyclecloud_verify_ssl
            if verify_resolved in (None, ""):
                provider_cyclecloud = self._provider_cyclecloud()
                verify_resolved = (
                    None if provider_cyclecloud is None else provider_cyclecloud.verify_ssl
                )
        if verify_resolved in (None, ""):
            verify_resolved = credential_data.verify_ssl
        if verify_resolved in (None, ""):
            verify_resolved = True

        if not resolved_url:
            raise CycleCloudConnectionError(
                "cyclecloud_url is required in the template, request context, or provider configuration.",
                url=None,
            )

        return str(resolved_url).rstrip("/"), verify_resolved

    def _resolve_auth_mode(
        self,
        credential_data: CycleCloudCredentialData,
    ) -> Optional[str]:
        auth_mode = None
        if self._template is not None:
            auth_mode = self._template.cyclecloud_auth_mode
        if auth_mode in (None, ""):
            auth_mode = self._request_context.cyclecloud_auth_mode
        if auth_mode in (None, ""):
            provider_cyclecloud = self._provider_cyclecloud()
            auth_mode = None if provider_cyclecloud is None else provider_cyclecloud.auth_mode
        auth_mode = auth_mode or credential_data.auth_mode
        return str(auth_mode).strip().lower() if auth_mode else None

    def _resolve_bearer_token(
        self,
        *,
        base_url: str,
        credential_data: CycleCloudCredentialData,
    ) -> Optional[str]:
        if credential_data.bearer_token:
            return str(credential_data.bearer_token)

        aad_scope = None
        if self._template is not None:
            aad_scope = self._template.cyclecloud_aad_scope
        if aad_scope in (None, ""):
            aad_scope = self._request_context.cyclecloud_aad_scope
        if aad_scope in (None, ""):
            provider_cyclecloud = self._provider_cyclecloud()
            aad_scope = None if provider_cyclecloud is None else provider_cyclecloud.aad_scope
        aad_scope = aad_scope or credential_data.aad_scope

        parsed = urlparse(base_url)
        host_scope = (
            f"{parsed.scheme}://{parsed.netloc}/.default"
            if parsed.scheme and parsed.netloc
            else ""
        )
        scopes = [str(aad_scope)] if aad_scope else []
        scopes.extend([host_scope, "https://management.azure.com/.default"])
        return self._get_azure_bearer_token(scopes)

    def build_settings(self) -> CycleCloudSessionSettings:
        credential_path = self._resolve_credential_path()
        credential_data = (
            self._load_credential_file(credential_path)
            if credential_path
            else CycleCloudCredentialData()
        )
        base_url, verify_ssl = self._resolve_transport_settings(credential_data)
        auth_mode = self._resolve_auth_mode(credential_data)
        bearer_token = self._resolve_bearer_token(
            base_url=base_url,
            credential_data=credential_data,
        )
        return CycleCloudSessionSettings(
            base_url=base_url,
            verify_ssl=verify_ssl,
            auth_mode=auth_mode,
            credential_path=credential_path,
            username=credential_data.username,
            password=credential_data.password,
            bearer_token=bearer_token,
        )
