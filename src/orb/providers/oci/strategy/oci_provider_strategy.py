from __future__ import annotations

"""OCI provider strategy - base implementation for registry integration."""

import json
import re
from importlib.resources import files
from typing import Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
from orb.providers.base.strategy import (
    ProviderCapabilities,
    ProviderHealthStatus,
    ProviderOperation,
    ProviderOperationType,
    ProviderResult,
    ProviderStrategy,
)
from orb.providers.oci.async_utils import run_blocking_operation
from orb.providers.oci.cli_client import OCICommandError, run_oci
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.handlers import OCIComputeHandler
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.services import OCIPricingService


class OCIProviderStrategy(ProviderStrategy):
    """OCI Compute strategy using provider-owned CLI authentication and templates."""

    def __init__(
        self,
        config: OCIProviderConfig,
        logger: LoggingPort,
        provider_name: Optional[str] = None,
        provider_instance_config: Optional[Any] = None,
    ) -> None:
        if not isinstance(config, OCIProviderConfig):
            raise ValueError("OCIProviderStrategy requires OCIProviderConfig")
        super().__init__(config)
        self._config = config
        self._logger = logger
        self._provider_name = provider_name
        self._provider_instance_config = provider_instance_config
        self._compute_handler = OCIComputeHandler(
            logger=logger,
            region=config.region,
            profile=config.profile,
            credential_source=config.credential_source,
            auth_config=config.model_dump(),
        )

    @property
    def provider_type(self) -> str:
        return "oci"

    @property
    def provider_name(self) -> Optional[str]:
        return self._provider_name

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
        is_valid, message, _, _ = self._config.validate_auth_configuration()
        if not is_valid:
            self._logger.error("OCI initialization failed: %s", message)
            return False
        if not is_dry_run_active() and not self._compute_handler._oci_cli_available:
            self._logger.error("OCI CLI is unavailable; install oci-cli and add oci to PATH")
            return False
        self._initialized = True
        return True

    async def execute_operation(self, operation: ProviderOperation) -> ProviderResult:
        if not self._initialized:
            return ProviderResult.error_result(
                "OCI provider strategy not initialized", "NOT_INITIALIZED"
            )

        if operation.operation_type == ProviderOperationType.HEALTH_CHECK:
            return await run_blocking_operation(self._health_result, operation)
        if operation.operation_type == ProviderOperationType.VALIDATE_TEMPLATE:
            params = operation.parameters or {}
            template_data = (
                params.get("template_config")
                or params.get("template_data")
                or params.get("template")
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
                    "pricing_estimate": OCIPricingService.estimate_hourly_cost(template_data),
                },
                {"provider": "oci", "operation": "validate_template"},
            )
        if operation.operation_type == ProviderOperationType.CREATE_INSTANCES:
            return await self._compute_handler.create_instances(operation)
        if operation.operation_type == ProviderOperationType.TERMINATE_INSTANCES:
            return await run_blocking_operation(
                self._compute_handler.terminate_instances, operation
            )
        if operation.operation_type == ProviderOperationType.GET_INSTANCE_STATUS:
            return await run_blocking_operation(
                self._compute_handler.get_instance_status, operation
            )
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

    def _health_result(self, operation: ProviderOperation) -> ProviderResult:
        health = self.check_health()
        return ProviderResult.success_result(
            health.model_dump(), {"provider": "oci", "operation": "health_check"}
        )

    def check_health(self) -> ProviderHealthStatus:
        result = self.test_credentials(**self._config.model_dump())
        if result["success"]:
            return ProviderHealthStatus.healthy("OCI CLI authenticated successfully")
        return ProviderHealthStatus.unhealthy(result["message"], result.get("metadata", {}))

    @classmethod
    def generate_provider_name(cls, config: dict[str, Any]) -> str:
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

    @classmethod
    def get_available_credential_sources(cls) -> list[dict]:
        return [
            {
                "name": "default",
                "description": "Default OCI CLI credentials",
                "config_delta": {"credential_source": "default"},
            },
            {
                "name": "profile",
                "description": "OCI DEFAULT config profile",
                "config_delta": {"credential_source": "profile", "profile": "DEFAULT"},
            },
            {
                "name": "security_token",
                "description": "OCI browser session (DEFAULT profile; choose your session profile)",
                "config_delta": {"credential_source": "security_token", "profile": "DEFAULT"},
            },
            {
                "name": "resource_principal",
                "description": "OCI resource principal",
                "config_delta": {"credential_source": "resource_principal"},
            },
            {
                "name": "instance_principal",
                "description": "OCI instance principal",
                "config_delta": {"credential_source": "instance_principal"},
            },
        ]

    @classmethod
    def test_credentials(cls, credential_source: str | None = None, **kwargs) -> dict:
        if credential_source in {"profile", "security_token"}:
            kwargs.setdefault("profile", "DEFAULT")
        config = OCIProviderConfig(credential_source=credential_source, **kwargs)
        is_valid, message, missing, resolved_source = config.validate_auth_configuration(
            credential_source=credential_source
        )
        result = {
            "success": is_valid,
            "source": resolved_source,
            "message": message,
            "missing_fields": missing,
            "metadata": {
                "region": config.region,
                "has_profile": bool(config.profile),
            },
        }
        if is_valid:
            try:
                response = run_oci(
                    ["os", "ns", "get"],
                    region=config.region,
                    profile=config.profile,
                    credential_source=resolved_source,
                    auth_config=config.model_dump(),
                )
                if not isinstance(response.get("data"), str) or not response["data"]:
                    raise OCICommandError(
                        "OCI authentication probe returned no namespace", reason="invalid_response"
                    )
                result["message"] = "OCI CLI authenticated successfully"
            except OCICommandError as exc:
                result.update(success=False, message=str(exc), error=str(exc))
        else:
            result["error"] = message
        return result

    @classmethod
    def get_credential_requirements(cls) -> dict:
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

    @classmethod
    def get_operational_requirements(cls) -> dict:
        return {
            "region": {"required": True, "description": "OCI region"},
            "compartment_id": {
                "required": False,
                "description": "Worker compartment OCID (optional)",
            },
            "subnet_id": {"required": False, "description": "Worker subnet OCID (optional)"},
            "image_id": {"required": False, "description": "Worker image OCID (optional)"},
        }

    @classmethod
    def get_available_regions(cls) -> list[tuple[str, str]]:
        return [
            ("us-phoenix-1", "US West (Phoenix)"),
            ("us-ashburn-1", "US East (Ashburn)"),
            ("eu-frankfurt-1", "Germany Central (Frankfurt)"),
            ("uk-london-1", "UK South (London)"),
        ]

    @classmethod
    def get_default_region(cls) -> str:
        return "us-phoenix-1"

    @classmethod
    def get_cli_provider_config(cls, args: Any) -> dict[str, Any]:
        from orb.providers.oci.cli.oci_cli_spec import OCICLISpec

        return OCICLISpec().extract_config(args)

    @classmethod
    def get_resource_id_pattern(cls) -> str:
        return r"^ocid1\.instance\.[a-z0-9.-]+$"

    @classmethod
    def get_operational_param_default(cls, param: str) -> str:
        return cls.get_default_region() if param == "region" else ""

    @classmethod
    def validate_operational_param(
        cls, param: str, value: str, selected_config: dict[str, Any]
    ) -> str | None:
        if not value:
            return "OCI region is required" if param == "region" else None
        if param == "region" and not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
            return "Enter an OCI region identifier"
        resource = {
            "compartment_id": "compartment",
            "subnet_id": "subnet",
            "image_id": "image",
        }.get(param)
        if resource and not value.startswith(f"ocid1.{resource}."):
            return f"Enter an OCI {resource} OCID"
        return None

    @classmethod
    def get_contextual_operational_param_choices(
        cls, param: str, selected_config: dict[str, Any]
    ) -> list[tuple[str, str]]:
        if param == "region":
            return cls.get_available_regions()
        compartment = selected_config.get("compartment_id")
        if param not in {"subnet_id", "image_id"} or not compartment:
            return []
        args = (
            ["network", "subnet", "list"] if param == "subnet_id" else ["compute", "image", "list"]
        )
        try:
            config = OCIProviderConfig(**selected_config)
            response = run_oci(
                [*args, "--compartment-id", compartment, "--all"],
                region=config.region,
                profile=config.profile,
                credential_source=config.credential_source,
                auth_config=config.model_dump(),
            )
            return [
                (item["id"], item.get("display-name") or item["id"])
                for item in response.get("data", [])
                if item.get("id")
            ]
        except OCICommandError:
            # Manual entry stays available; raw CLI stderr must never reach init output.
            return []

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Transfer values already collected by ORB's init hooks; never prompt here."""
        config = provider_config.get("config") or {}
        defaults = {key: config[key] for key in ("compartment_id", "image_id") if config.get(key)}
        if config.get("subnet_id"):
            defaults["subnet_ids"] = [config["subnet_id"]]
        return defaults

    def cleanup(self) -> None:
        self._initialized = False
