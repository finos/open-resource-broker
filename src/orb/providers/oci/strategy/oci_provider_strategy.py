from __future__ import annotations

"""OCI provider strategy - base implementation for registry integration."""

import json
import re
from importlib.resources import files
from typing import Any

from orb.domain.base.dependency_injection import injectable
from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.handlers import OCIComputeHandler
from orb.providers.oci.mapping import OCITemplateMapper


@injectable
class OCIProviderStrategy(ProviderStrategy):
    """Minimal OCI ProviderStrategy implementation used for base integration."""

    def __init__(self, config: OCIProviderConfig, logger: LoggingPort) -> None:
        if not isinstance(config, OCIProviderConfig):
            raise ValueError("OCIProviderStrategy requires OCIProviderConfig")
        super().__init__(config)
        self._config = config
        self._logger = logger
        self._compute_handler = OCIComputeHandler(
            logger=logger,
            region=config.region,
            profile=config.profile,
        )

    @property
    def provider_type(self) -> str:
        return "oci"

    @classmethod
    def get_defaults_config(cls) -> dict:
        text = (
            files("orb.providers.oci.config")
            .joinpath("oci_defaults.json")
            .read_text(encoding="utf-8")
        )
        raw = json.loads(text)
        provider_config = raw["provider"]["providers"][0]["config"]
        OCIProviderConfig(**provider_config)  # raises ValidationError if invalid
        return raw

    def initialize(self) -> bool:
        try:
            self._logger.info("OCI provider strategy ready for region: %s", self._config.region)
            self._initialized = True
            return True
        except Exception as exc:
            self._logger.error("Failed to initialize OCI provider strategy: %s", exc, exc_info=True)
            return False

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        if not self._initialized:
            return ProviderResult.error_result(
                "OCI provider strategy not initialized", "NOT_INITIALIZED"
            )

        # Base milestone: register OCI strategy + APIs without OCI SDK operations yet.
        if operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return ProviderResult.success_result(
                {"is_healthy": True, "status_message": "OCI provider strategy is ready"},
                {"provider": "oci", "operation": "health_check"},
            )
        if operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
            params = operation.parameters or {}
            template_data = (
                params.get("template")
                or params.get("template_data")
                or params.get("template_config")
                or params.get("configuration")
                or params
            )
            if not isinstance(template_data, dict):
                template_data = {}
            missing = OCITemplateMapper.validate_required_fields(template_data)
            return ProviderResult.success_result(
                {
                    "valid": len(missing) == 0,
                    "message": "OCI template validation complete",
                    "errors": missing,
                    "pricing_estimate": OCITemplateMapper.estimate_hourly_cost(template_data),
                },
                {"provider": "oci", "operation": "validate_template"},
            )
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return await self._compute_handler.create_instances(operation)
        if operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return self._compute_handler.terminate_instances(operation)
        if operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            return self._compute_handler.get_instance_status(operation)
        if operation.operation_type == ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES:
            return await self._compute_handler.describe_resource_instances(operation)

        return ProviderResult.error_result(
            f"OCI operation not implemented yet: {operation.operation_type}",
            "NOT_IMPLEMENTED",
            {"provider": "oci"},
        )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="oci",
            supported_operations=[
                ProviderOperationType.CREATE_INSTANCES,
                ProviderOperationType.TERMINATE_INSTANCES,
                ProviderOperationType.GET_INSTANCE_STATUS,
                ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
                ProviderOperationType.VALIDATE_TEMPLATE,
                ProviderOperationType.HEALTH_CHECK,
            ],
            supported_apis=["OCICompute"],
            features={
                "instance_management": True,
                "regions": [self._config.region],
            },
        )

    def check_health(self) -> ProviderHealthStatus:
        is_valid, message, missing, resolved_source = self._config.validate_auth_configuration()
        if is_valid:
            return ProviderHealthStatus.healthy(
                f"OCI provider strategy initialized (auth_source={resolved_source})"
            )
        return ProviderHealthStatus.unhealthy(
            f"OCI auth configuration invalid: {message}",
            {"missing_fields": missing, "auth_source": resolved_source},
        )

    def generate_provider_name(self, config: dict[str, Any]) -> str:
        profile = config.get("profile") or "instance-profile"
        region = config.get("region", "us-phoenix-1")
        sanitized_profile = re.sub(r"[^a-zA-Z0-9\\-_]", "-", profile)
        return f"oci_{sanitized_profile}_{region}"

    def parse_provider_name(self, provider_name: str) -> dict[str, str]:
        parts = provider_name.split("_")
        if len(parts) >= 3 and parts[0] == "oci":
            return {
                "type": "oci",
                "profile": parts[1],
                "region": "_".join(parts[2:]),
            }
        return {"type": "oci", "profile": "instance-profile", "region": "us-phoenix-1"}

    def get_provider_name_pattern(self) -> str:
        return "oci_{profile}_{region}"

    def get_available_credential_sources(self) -> list[dict]:
        return [
            {"name": "default", "description": "Default OCI credentials"},
            {"name": "profile", "description": "OCI config profile"},
            {"name": "api_key", "description": "Explicit OCI API key credentials"},
            {"name": "instance_principal", "description": "OCI instance principal"},
        ]

    def test_credentials(self, credential_source: str | None = None, **kwargs) -> dict:
        is_valid, message, missing, resolved_source = self._config.validate_auth_configuration(
            credential_source=credential_source, **kwargs
        )
        result = {
            "success": is_valid,
            "source": resolved_source,
            "message": message,
            "missing_fields": missing,
            "metadata": {
                "region": kwargs.get("region", self._config.region),
                "has_profile": bool(kwargs.get("profile", self._config.profile)),
            },
        }
        if not is_valid:
            result["error"] = message
        return result

    def get_credential_requirements(self) -> dict:
        return {
            "profile": {
                "required": False,
                "description": "OCI config profile (preferred for local development)",
            },
            "tenancy_ocid": {
                "required": False,
                "required_if_source": "api_key",
                "description": "OCI tenancy OCID",
            },
            "user_ocid": {
                "required": False,
                "required_if_source": "api_key",
                "description": "OCI user OCID",
            },
            "fingerprint": {
                "required": False,
                "required_if_source": "api_key",
                "description": "OCI API key fingerprint",
            },
            "private_key_path": {
                "required": False,
                "required_if_source": "api_key",
                "description": "Path to OCI API private key",
            },
        }

    def get_operational_requirements(self) -> dict:
        return {"region": {"required": True, "description": "OCI region"}}

    def get_available_regions(self) -> list[tuple[str, str]]:
        return [
            ("us-phoenix-1", "US West (Phoenix)"),
            ("us-ashburn-1", "US East (Ashburn)"),
            ("eu-frankfurt-1", "Germany Central (Frankfurt)"),
            ("uk-london-1", "UK South (London)"),
        ]

    def get_default_region(self) -> str:
        return "us-phoenix-1"

    def cleanup(self) -> None:
        self._initialized = False
