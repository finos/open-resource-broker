from __future__ import annotations

"""OCI provider strategy - base implementation for registry integration."""

import json
import os
import re
import shutil
import subprocess
from configparser import ConfigParser
from importlib.resources import files
from typing import Any, Optional

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
from orb.providers.oci.oci_cli_auth import build_oci_cli_extra_args
from orb.providers.oci.services import OCIPricingService


@injectable
class OCIProviderStrategy(ProviderStrategy):
    """Minimal OCI ProviderStrategy implementation used for base integration."""

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
                    "pricing_estimate": OCIPricingService.estimate_hourly_cost(template_data),
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

    def discover_infrastructure_interactive(
        self, provider_config: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover OCI infrastructure defaults interactively via OCI CLI."""
        console = self._get_console()
        if shutil.which("oci") is None:
            self._console_info(console, "OCI CLI not found; skipping infrastructure discovery")
            return {}

        config = provider_config.get("config", {})
        if not isinstance(config, dict):
            config = {}

        region = config.get("region") or self._config.region
        profile = config.get("profile") or self._config.profile
        credential_source = config.get("credential_source") or self._config.credential_source
        tenancy_ocid = (
            config.get("tenancy_ocid")
            or self._config.tenancy_ocid
            or self._read_oci_cli_tenancy(profile, credential_source)
        )

        self._console_info(console, "Discovering OCI infrastructure...")
        if not tenancy_ocid:
            tenancy_ocid = input(
                "  Tenancy/root compartment OCID (press Enter to skip discovery): "
            ).strip()
        if not tenancy_ocid:
            self._console_info(console, "No tenancy OCID available; skipping discovery")
            return {}

        discovered: dict[str, Any] = {
            "provider_api": "OCICompute",
            "provider_type": "oci",
            "tags": {"managed_by": "orb"},
        }

        compartments = [
            {
                "id": tenancy_ocid,
                "name": "Root tenancy",
                "description": "Root compartment",
            }
        ]
        compartments.extend(
            self._oci_list(
                ["iam", "compartment", "list", "--compartment-id", tenancy_ocid],
                region=region,
                profile=profile,
                credential_source=credential_source,
            )
        )
        compartment = self._pick_item(
            console,
            title="Found compartments:",
            items=compartments,
            default_index=0,
            label_fields=("name", "id"),
        )
        if not compartment:
            self._console_info(console, "No compartment selected")
            return discovered

        compartment_id = self._item_id(compartment)
        if compartment_id:
            discovered["compartment_id"] = compartment_id

        subnets = self._oci_list(
            ["network", "subnet", "list", "--compartment-id", compartment_id],
            region=region,
            profile=profile,
            credential_source=credential_source,
        )
        subnet = self._pick_item(
            console,
            title="Found subnets:",
            items=subnets,
            default_index=0,
            label_fields=("display-name", "cidr-block", "id"),
            allow_skip=True,
        )
        if subnet:
            subnet_id = self._item_id(subnet)
            if subnet_id:
                discovered["subnet_ids"] = [subnet_id]

        nsgs = self._oci_list(
            ["network", "nsg", "list", "--compartment-id", compartment_id],
            region=region,
            profile=profile,
            credential_source=credential_source,
        )
        selected_nsgs = self._pick_items(
            console,
            title="Found network security groups:",
            items=nsgs,
            default_selection="1" if nsgs else "",
            label_fields=("display-name", "id"),
        )
        if selected_nsgs:
            discovered["security_group_ids"] = [
                item_id for item in selected_nsgs if (item_id := self._item_id(item))
            ]

        images = self._oci_list(
            ["compute", "image", "list", "--compartment-id", compartment_id],
            region=region,
            profile=profile,
            credential_source=credential_source,
        )
        image_id = self._pick_image_id(console, images)
        if image_id:
            discovered["image_id"] = image_id

        ssh_key = self._collect_ssh_public_key(console)
        if ssh_key:
            discovered["ssh_authorized_keys"] = ssh_key

        if len(discovered) > 3:
            self._console_success(console, "OCI infrastructure discovered and configured!")
        else:
            self._console_info(console, "No OCI infrastructure defaults selected")
        return discovered

    def _run_oci_discovery_command(
        self,
        args: list[str],
        *,
        region: str,
        profile: str | None,
        credential_source: str | None,
    ) -> dict[str, Any]:
        cmd = ["oci", *args, "--region", region, "--all"]
        cmd.extend(
            build_oci_cli_extra_args(profile=profile, credential_source=credential_source)
        )
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        raw = completed.stdout.strip()
        return json.loads(raw) if raw else {}

    def _oci_list(
        self,
        args: list[str],
        *,
        region: str,
        profile: str | None,
        credential_source: str | None,
    ) -> list[dict[str, Any]]:
        try:
            response = self._run_oci_discovery_command(
                args,
                region=region,
                profile=profile,
                credential_source=credential_source,
            )
        except Exception as exc:
            self._logger.warning("OCI discovery command failed for %s: %s", args, exc)
            return []
        data = response.get("data", [])
        return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []

    def _read_oci_cli_tenancy(
        self,
        profile: str | None,
        credential_source: str | None,
    ) -> str | None:
        if credential_source in {"instance_principal", "resource_principal"}:
            return None
        profile_name = profile or "DEFAULT"
        config_path = os.environ.get("OCI_CLI_CONFIG_FILE") or os.path.expanduser("~/.oci/config")
        parser = ConfigParser()
        if not parser.read(config_path):
            return None
        if profile_name.upper() == "DEFAULT":
            return parser.defaults().get("tenancy")
        if not parser.has_section(profile_name):
            return None
        return parser.get(profile_name, "tenancy", fallback=None)

    def _pick_item(
        self,
        console: Any,
        *,
        title: str,
        items: list[dict[str, Any]],
        default_index: int,
        label_fields: tuple[str, ...],
        allow_skip: bool = False,
        page_size: int | None = None,
    ) -> dict[str, Any] | None:
        if not items:
            self._console_info(console, f"{title} none")
            return None
        if page_size and len(items) > page_size:
            return self._pick_item_paged(
                console,
                title=title,
                items=items,
                default_index=default_index,
                label_fields=label_fields,
                allow_skip=allow_skip,
                page_size=page_size,
            )
        self._console_info(console, "")
        self._console_info(console, title)
        for index, item in enumerate(items, 1):
            self._console_info(console, f"  ({index}) {self._item_label(item, label_fields)}")
        if allow_skip:
            self._console_info(console, "  (s) Skip")
        default_choice = str(default_index + 1)
        choice = input(f"  Select ({default_choice}): ").strip() or default_choice
        if allow_skip and choice.lower() == "s":
            return None
        try:
            return items[int(choice) - 1]
        except (ValueError, IndexError):
            self._console_error(console, "Invalid selection; skipping")
            return None

    def _pick_item_paged(
        self,
        console: Any,
        *,
        title: str,
        items: list[dict[str, Any]],
        default_index: int,
        label_fields: tuple[str, ...],
        allow_skip: bool,
        page_size: int,
    ) -> dict[str, Any] | None:
        page_start = max(default_index, 0) // page_size * page_size
        while True:
            page_end = min(page_start + page_size, len(items))
            self._console_info(console, "")
            self._console_info(
                console,
                f"{title} showing {page_start + 1}-{page_end} of {len(items)}",
            )
            for index, item in enumerate(items[page_start:page_end], page_start + 1):
                self._console_info(console, f"  ({index}) {self._item_label(item, label_fields)}")
            if page_end < len(items):
                self._console_info(console, "  (n) Next page")
            if page_start > 0:
                self._console_info(console, "  (p) Previous page")
            if allow_skip:
                self._console_info(console, "  (s) Skip")

            default_choice = str(page_start + 1)
            choice = input(f"  Select ({default_choice}): ").strip().lower() or default_choice
            if choice == "n":
                if page_end >= len(items):
                    self._console_error(console, "Already on the last page")
                else:
                    page_start = page_end
                continue
            if choice == "p":
                if page_start == 0:
                    self._console_error(console, "Already on the first page")
                else:
                    page_start = max(0, page_start - page_size)
                continue
            if allow_skip and choice == "s":
                return None
            try:
                selected_index = int(choice) - 1
            except ValueError:
                self._console_error(console, "Invalid selection")
                continue
            if 0 <= selected_index < len(items):
                return items[selected_index]
            self._console_error(console, "Invalid selection")

    def _pick_image_id(self, console: Any, images: list[dict[str, Any]]) -> str | None:
        self._console_info(console, "")
        self._console_info(console, "Image source:")
        self._console_info(console, "  (1) Oracle Linux")
        self._console_info(console, "  (2) Ubuntu")
        self._console_info(console, "  (3) Windows")
        self._console_info(console, "  (4) Custom image OCID")
        self._console_info(console, "  (s) Skip")
        choice = input("  Select image source (1): ").strip().lower() or "1"
        if choice == "s":
            return None
        if choice == "4":
            image_id = input("  Image OCID (press Enter to skip): ").strip()
            return image_id or None

        family_by_choice = {
            "1": ("oracle_linux", "Oracle Linux"),
            "2": ("ubuntu", "Ubuntu"),
            "3": ("windows", "Windows"),
        }
        family = family_by_choice.get(choice, family_by_choice["1"])
        filtered_images = self._filter_images_by_family(images, family[0])
        if not filtered_images:
            self._console_info(console, f"No {family[1]} images found")
            image_id = input("  Image OCID (press Enter to skip): ").strip()
            return image_id or None

        image = self._pick_item(
            console,
            title=f"Found {family[1]} images:",
            items=self._sort_images_for_selection(filtered_images),
            default_index=0,
            label_fields=("display-name", "operating-system", "operating-system-version", "id"),
            allow_skip=True,
            page_size=10,
        )
        return self._item_id(image) if image else None

    def _filter_images_by_family(
        self, images: list[dict[str, Any]], family: str
    ) -> list[dict[str, Any]]:
        aliases = {
            "oracle_linux": ("oracle linux", "oracle-linux", "oracle_linux"),
            "ubuntu": ("ubuntu", "canonical ubuntu"),
            "windows": ("windows", "windows server"),
        }.get(family, ())
        filtered = []
        for image in images:
            haystack = " ".join(
                str(image.get(field, ""))
                for field in ("display-name", "operating-system", "operating-system-version")
            ).lower()
            if any(alias in haystack for alias in aliases):
                filtered.append(image)
        return filtered

    def _sort_images_for_selection(self, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            images,
            key=lambda image: (
                str(image.get("time-created") or ""),
                str(image.get("display-name") or ""),
            ),
            reverse=True,
        )

    def _collect_ssh_public_key(self, console: Any) -> str | None:
        value = input("  SSH public key or path to .pub file (press Enter to skip): ").strip()
        if not value:
            return None

        if self._looks_like_ssh_public_key(value):
            return value

        path_text = value.strip("\"'")
        public_key_path = os.path.expandvars(os.path.expanduser(path_text))
        if os.path.isfile(public_key_path):
            try:
                with open(public_key_path, encoding="utf-8") as f:
                    for line in f:
                        candidate = line.strip()
                        if candidate and self._looks_like_ssh_public_key(candidate):
                            return candidate
            except OSError as exc:
                self._console_error(console, f"Could not read SSH public key file: {exc}")
                return None
            self._console_error(console, "SSH public key file did not contain a public key")
            return None

        self._console_error(console, "SSH public key path not found; skipping SSH key default")
        return None

    def _looks_like_ssh_public_key(self, value: str) -> bool:
        prefixes = (
            "ssh-rsa ",
            "ssh-ed25519 ",
            "ecdsa-sha2-",
            "sk-ssh-ed25519@openssh.com ",
            "sk-ecdsa-sha2-nistp256@openssh.com ",
        )
        return value.startswith(prefixes)

    def _pick_items(
        self,
        console: Any,
        *,
        title: str,
        items: list[dict[str, Any]],
        default_selection: str,
        label_fields: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not items:
            self._console_info(console, f"{title} none")
            return []
        self._console_info(console, "")
        self._console_info(console, title)
        for index, item in enumerate(items, 1):
            self._console_info(console, f"  ({index}) {self._item_label(item, label_fields)}")
        self._console_info(console, "  (s) Skip")
        choice = input(f"  Select comma-separated ({default_selection or 's'}): ").strip()
        choice = choice or default_selection
        if not choice or choice.lower() == "s":
            return []
        try:
            indices = [int(part.strip()) - 1 for part in choice.split(",")]
        except ValueError:
            self._console_error(console, "Invalid selection; skipping")
            return []
        return [items[index] for index in indices if 0 <= index < len(items)]

    def _item_label(self, item: dict[str, Any], fields: tuple[str, ...]) -> str:
        values = [str(item[field]) for field in fields if item.get(field)]
        return " | ".join(values) if values else str(item)

    def _item_id(self, item: dict[str, Any]) -> str | None:
        value = item.get("id")
        return str(value) if value else None

    def _get_console(self) -> Any:
        try:
            from orb.domain.base.ports.console_port import ConsolePort
            from orb.infrastructure.di.container import get_container

            return get_container().get(ConsolePort)
        except Exception:
            return None

    def _console_info(self, console: Any, message: str) -> None:
        if console is not None and hasattr(console, "info"):
            console.info(message)
        else:
            print(message)

    def _console_success(self, console: Any, message: str) -> None:
        if console is not None and hasattr(console, "success"):
            console.success(message)
        else:
            print(message)

    def _console_error(self, console: Any, message: str) -> None:
        if console is not None and hasattr(console, "error"):
            console.error(message)
        else:
            print(message)

    def cleanup(self) -> None:
        self._initialized = False
