"""HostFactory scheduler strategy for field mapping and response formatting."""

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

# Normalize legacy/alias provider API names to canonical registry keys
PROVIDER_API_ALIASES: dict[str, str] = {
    "AutoScalingGroup": "ASG",
    "autoscalinggroup": "ASG",
    "asg": "ASG",
}

if TYPE_CHECKING:
    from domain.template.ports.template_defaults_port import TemplateDefaultsPort

from application.dto.responses import MachineDTO
from application.request.dto import RequestDTO
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from infrastructure.scheduler.hostfactory.field_mapper import HostFactoryFieldMapper
from infrastructure.scheduler.hostfactory.transformations import HostFactoryTransformations
from infrastructure.template.dtos import TemplateDTO
from infrastructure.utilities.common.string_utils import extract_provider_type


class HostFactorySchedulerStrategy(BaseSchedulerStrategy):
    """HostFactory scheduler strategy for field mapping and response formatting."""

    def __init__(
        self,
        template_defaults_service: "TemplateDefaultsPort | None" = None,
    ) -> None:
        """Initialize the instance."""
        self._config_manager = None
        self._logger = None
        self._template_defaults_service = template_defaults_service
        # Initialize field mapper lazily - will be created when first needed
        self._field_mapper = None

    @property
    def config_manager(self) -> Any:
        if self._config_manager is None:
            from infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                self._config_manager = get_container().get(ConfigurationPort)
        return self._config_manager

    @property
    def logger(self) -> Any:
        if self._logger is None:
            from infrastructure.di.container import get_container, is_container_ready

            if is_container_ready():
                self._logger = get_container().get(LoggingPort)
            else:
                from infrastructure.logging.logger import get_logger

                return get_logger(__name__)  # fallback, don't cache — pick up DI logger once ready
        return self._logger

    @property
    def field_mapper(self) -> HostFactoryFieldMapper:
        """Lazy initialization of field mapper to avoid circular dependencies."""
        if self._field_mapper is None:
            provider_type = self._get_active_provider_type()
            self._field_mapper = HostFactoryFieldMapper(provider_type)
        return self._field_mapper

    def load_templates_from_path(
        self, template_path: str, provider_override=None
    ) -> list[dict[str, Any]]:
        """Load templates from a specific path."""
        if not os.path.exists(template_path):
            self.logger.debug("Template file not found: %s", template_path)
            return []

        try:
            templates = self._load_single_file(template_path)
            self.logger.debug("Loaded %d templates from %s", len(templates), template_path)

            # Process each template with field mapping
            processed_templates = []
            for template in templates:
                if template is None:
                    continue

                try:
                    processed_template = self._map_template_fields(template, provider_override)
                    processed_templates.append(processed_template)
                except Exception as e:
                    self.logger.warning(
                        "Skipping invalid template %s: %s",
                        template.get("id", "unknown"),
                        e,
                    )
                    continue

            return processed_templates
        except Exception as e:
            self.logger.error("Error loading templates from %s: %s", template_path, e)
            return []

    def _load_single_file(self, template_path: str) -> list[dict[str, Any]]:
        """Load templates from a single file."""
        try:
            import json

            with open(template_path) as f:
                data = json.load(f)

            # Handle different template file formats
            if isinstance(data, dict) and "templates" in data:
                return data["templates"]
            elif isinstance(data, list):
                return data
            else:
                return []

        except Exception:
            return []

    def _map_template_fields(
        self, template: dict[str, Any], provider_override=None
    ) -> dict[str, Any]:
        """Map HostFactory fields to internal format with business logic."""
        if template is None:
            raise ValueError("Template cannot be None in field mapping")
        if not isinstance(template, dict):
            raise ValueError(f"Template must be a dictionary, got {type(template)}")

        # Field mapping (bidirectional)
        mapped = self.field_mapper.map_input_fields(template)

        # Apply HostFactory transformations
        mapped = HostFactoryTransformations.apply_transformations(mapped)

        # Transform machine types from HF format to internal format
        machine_types_data = self._transform_machine_types_input(template)
        mapped.update(machine_types_data)

        if "attributes" in template:
            mapped["attributes"] = template["attributes"]

        # Business logic - Apply template defaults
        target_provider = provider_override or self._get_provider_name()
        if self._template_defaults_service:
            mapped["provider_api"] = self._template_defaults_service.resolve_provider_api_default(
                template
            )
            mapped["provider_api"] = PROVIDER_API_ALIASES.get(
                mapped["provider_api"], mapped["provider_api"]
            )
        else:
            raw_api = template.get("providerApi", template.get("provider_api", "EC2Fleet"))
            mapped["provider_api"] = PROVIDER_API_ALIASES.get(raw_api, raw_api)
        mapped = self._apply_template_defaults(mapped, target_provider)

        if "template_id" in mapped:
            mapped["name"] = template.get("name", mapped["template_id"])

        mapped.setdefault("max_instances", 1)
        mapped.setdefault("price_type", "ondemand")
        mapped.setdefault("allocation_strategy", "lowest_price")
        mapped.setdefault("subnet_ids", [])
        mapped.setdefault("security_group_ids", [])
        mapped.setdefault("tags", {})

        mapped["created_at"] = template.get("created_at")
        mapped["updated_at"] = template.get("updated_at")
        mapped["version"] = template.get("version")

        return mapped

    def _get_provider_name(self) -> str:
        """Get the active provider instance name."""
        try:
            from application.services.provider_registry_service import ProviderRegistryService
            from infrastructure.di.container import get_container

            container = get_container()
            provider_service = container.get(ProviderRegistryService)
            selection_result = provider_service.select_active_provider()
            return selection_result.provider_name
        except Exception as e:
            self.logger.warning("Failed to get provider instance name: %s", e)
            return "default"

    def _get_active_provider_type(self) -> str:
        """Get the active provider type from configuration."""
        try:
            from application.services.provider_registry_service import ProviderRegistryService
            from infrastructure.di.container import get_container

            container = get_container()
            provider_service = container.get(ProviderRegistryService)
            selection_result = provider_service.select_active_provider()
            provider_type = selection_result.provider_type
            self.logger.debug("Active provider type: %s", provider_type)
            return provider_type
        except Exception as e:
            self.logger.warning("Failed to get active provider type, defaulting to 'aws': %s", e)
            return "aws"  # Default fallback

    def convert_cli_args_to_hostfactory_input(self, operation: str, args: Any) -> dict[str, Any]:
        """Convert CLI arguments to HostFactory JSON input format.

        This method handles the conversion from CLI arguments to the expected
        HostFactory API input format as documented in hf_docs/input-output.md.

        Args:
            operation: The HostFactory operation (requestMachines, getRequestStatus, etc.)
            args: CLI arguments namespace

        Returns:
            Dict in HostFactory JSON input format
        """
        if operation == "requestMachines":
            return {
                "template": {
                    "templateId": getattr(args, "template_id", ""),
                    "machineCount": getattr(args, "count", 1),
                }
            }
        elif operation == "getRequestStatus":
            return {"requests": [{"requestId": getattr(args, "request_id", "")}]}
        elif operation == "requestReturnMachines":
            machine_ids = getattr(args, "machine_ids", [])
            return {
                "machines": [
                    {"name": machine_id, "machineId": machine_id} for machine_id in machine_ids
                ]
            }
        else:
            raise ValueError(f"Unsupported HostFactory operation: {operation}")

    def format_request_response(self, request_data: Any) -> dict[str, Any]:
        """Format request creation response to HostFactory format."""
        request_dict = self._coerce_to_dict(request_data)

        if "requests" in request_dict:
            return {
                "requests": request_dict.get("requests", []),
                "status": request_dict.get("status", "complete"),
                "message": request_dict.get("message", "Status retrieved successfully"),
            }

        raw_id = request_dict.get("request_id", request_dict.get("requestId"))
        request_id = self._unwrap_request_id(raw_id)

        # Check request status to provide appropriate message
        status = request_dict.get("status", "pending")
        error_message = request_dict.get("status_message")  # Use domain field instead of metadata

        # Status-based message and response logic
        if status == "failed":
            return {
                "requestId": request_id,
                "message": f"Request failed: {error_message or 'Unknown error'}",
            }
        elif status == "cancelled":
            return {"requestId": request_id, "message": "Request cancelled"}
        elif status == "timeout":
            return {"requestId": request_id, "message": "Request timed out"}
        elif status == "partial":
            return {
                "requestId": request_id,
                "message": f"Request partially completed: {error_message or 'Some resources failed'}",
            }
        elif status == "complete":
            return {"requestId": request_id, "message": "Request completed successfully"}
        elif status == "in_progress":
            return {"requestId": request_id, "message": "Request in progress"}
        elif status == "pending":
            return {"requestId": request_id, "message": "Request submitted for processing"}
        else:
            return {
                "requestId": request_id,
                "message": request_dict.get("message", "Request status unknown"),
            }

    def convert_domain_to_hostfactory_output(self, operation: str, data: Any) -> dict[str, Any]:
        """Convert domain objects to HostFactory JSON output format.

        This method handles the conversion from internal domain objects to the expected
        HostFactory API output format as documented in hf_docs/input-output.md.

        Args:
            operation: The HostFactory operation
            data: Domain objects or DTOs to convert

        Returns:
            Dict in HostFactory JSON output format
        """
        if operation == "getAvailableTemplates":
            if isinstance(data, list):
                templates = []
                for template in data:
                    # Convert domain Template to HostFactory format
                    hf_template = self._convert_template_to_hostfactory(template)
                    templates.append(hf_template)

                return {
                    "templates": templates,
                    "message": "Get available templates success.",
                }
            else:
                return {"templates": [], "message": "No templates found."}

        elif operation == "requestMachines":
            # Handle both direct request_id and nested data structures
            if isinstance(data, str):
                request_id = data
                resource_ids = []
            elif isinstance(data, dict):
                request_id = str(data.get("request_id", data.get("requestId", "")))
                resource_ids = data.get("resource_ids", [])
            else:
                request_id = str(data) if data else ""
                resource_ids = []

            # Create message with resource ID information
            base_message = "Request VM succeeded."
            if resource_ids:
                # Include the first resource ID in the message for user visibility
                # Show first for brevity
                resource_id_info = f" Resource ID: {resource_ids[0]}"
                message = base_message + resource_id_info
            else:
                message = base_message

            # Return success - the command handler will have already handled any errors
            # If we reach here, the request was created successfully
            return {"requestId": request_id, "message": message}

        elif operation == "getRequestStatus":
            # Convert RequestDTO to HostFactory format
            if hasattr(data, "request_id"):
                # Handle RequestDTO object - use to_dict() to get machines data
                dto_dict = data.to_dict()
                machines_data = dto_dict.get("machines", [])

                machines = self._format_machines_for_hostfactory(
                    machines_data, request_type=dto_dict.get("request_type")
                )
                status = self._map_domain_status_to_hostfactory(data.status)
                message = self._generate_status_message(data.status, len(machines))

                return {
                    "requests": [
                        {
                            "requestId": data.request_id,
                            "status": status,
                            "message": message,
                            "machines": machines,
                        }
                    ]
                }
            elif isinstance(data, dict):
                # Handle dict format (fallback)
                machines = self._format_machines_for_hostfactory(
                    data.get("machines", []), request_type=data.get("request_type")
                )
                status = self._map_domain_status_to_hostfactory(data.get("status", "unknown"))
                message = self._generate_status_message(
                    data.get("status", "unknown"), len(machines)
                )

                return {
                    "requests": [
                        {
                            "requestId": data.get("request_id", data.get("requestId", "")),
                            "status": status,
                            "message": message,
                            "machines": machines,
                        }
                    ]
                }
            else:
                return {"requests": [], "message": "Request not found."}

        else:
            raise ValueError(f"Unsupported HostFactory operation: {operation}")

    def _convert_template_to_hostfactory(self, template: Any) -> dict[str, Any]:
        """Convert internal template to HostFactory format."""
        # Handle TemplateDTO objects
        template_dict = self.format_template_for_display(template)

        # Start with the formatted template
        hf_template = template_dict.copy()

        # Add optional HostFactory fields if present
        optional_fields = [
            "imageId",
            "subnetId",
            "vmType",
            "vmTypes",
            "keyName",
            "securityGroupIds",
            "priceType",
            "instanceTags",
            "instanceProfile",
            "userDataScript",
            "rootDeviceVolumeSize",
            "volumeType",
            "fleetRole",
            "maxSpotPrice",
            "allocationStrategy",
            "spotFleetRequestExpiry",
            "abisInstanceRequirements",
        ]

        for field in optional_fields:
            # Map from internal field names to HostFactory field names
            internal_field = self._map_hostfactory_to_internal_field(field)
            if internal_field in template_dict:
                hf_template[field] = template_dict[internal_field]
            elif field in template_dict:
                hf_template[field] = template_dict[field]

        return hf_template

    def _map_hostfactory_to_internal_field(self, hf_field: str) -> str:
        """Map HostFactory field names to internal field names."""
        mapping = {
            "templateId": "template_id",
            "maxNumber": "max_instances",
            "imageId": "image_id",
            "subnetId": "subnet_ids",  # Note: HF uses single, we use array
            "vmType": "instance_type",
            "keyName": "key_name",
            "securityGroupIds": "security_group_ids",
            "priceType": "price_type",
            "abisInstanceRequirements": "abis_instance_requirements",
        }
        return mapping.get(hf_field, hf_field)

    def get_config_file_path(self) -> str:
        """Get config file path using configuration."""
        # Get raw config and build path manually
        config = self.config_manager.app_config.model_dump()

        # Get scheduler config root
        scheduler_config = config.get("scheduler", {})
        config_root = scheduler_config.get("config_root", "config")

        # Get provider type from active provider
        provider_config = config.get("provider", {})
        active_provider = provider_config.get("active_provider")
        if not active_provider:
            # Get from provider registry instead of hardcoded default
            try:
                from application.services.provider_registry_service import ProviderRegistryService
                from infrastructure.di.container import get_container

                container = get_container()
                provider_service = container.get(ProviderRegistryService)
                selection_result = provider_service.select_active_provider()
                active_provider = selection_result.provider_name
            except Exception:
                active_provider = "aws_default"

        provider_type = extract_provider_type(active_provider)

        # Build config file path
        config_file = f"{provider_type}prov_config.json"
        return os.path.join(config_root, config_file)

    def parse_template_config(self, raw_data: dict[str, Any]) -> TemplateDTO:
        """
        Parse HostFactory template to TemplateDTO.

        This method handles the conversion from HostFactory template format to TemplateDTO objects.
        """
        # Map HostFactory field names to domain field names
        domain_data = {
            # Core template fields
            "template_id": raw_data.get("templateId"),
            "name": raw_data.get("name"),
            "description": raw_data.get("description"),
            # Instance configuration
            "instance_type": raw_data.get("vmType"),
            "image_id": raw_data.get("imageId"),
            "max_instances": raw_data.get("maxNumber", 1),
            # Network configuration
            "subnet_ids": raw_data.get("subnetIds", []),
            "security_group_ids": raw_data.get("securityGroupIds", []),
            # Pricing and allocation
            "price_type": raw_data.get("priceType", "ondemand"),
            "allocation_strategy": raw_data.get("allocationStrategy", "lowest_price"),
            "max_price": raw_data.get("maxPrice"),
            # Tags and metadata
            "tags": raw_data.get("tags", {}),
            "metadata": raw_data.get("metadata", {}),
            # Provider API
            "provider_api": PROVIDER_API_ALIASES.get(
                raw_data.get("providerApi", ""), raw_data.get("providerApi")
            ),
            # Timestamps
            "created_at": raw_data.get("createdAt"),
            "updated_at": raw_data.get("updatedAt"),
            "is_active": raw_data.get("isActive", True),
            # HostFactory-specific fields
            "vm_type": raw_data.get("vmType"),
            "vm_types": raw_data.get("vmTypes", {}),
            "key_name": raw_data.get("keyName"),
            "user_data": raw_data.get("userData"),
            # Native spec fields
            "launch_template_spec": raw_data.get("launch_template_spec"),
            "launch_template_spec_file": raw_data.get("launch_template_spec_file"),
            "provider_api_spec": raw_data.get("provider_api_spec"),
            "provider_api_spec_file": raw_data.get("provider_api_spec_file"),
        }

        # Create TemplateDTO object with validation
        return cast(TemplateDTO, TemplateDTO.from_dict(domain_data))

    def parse_request_data(self, raw_data: dict[str, Any]) -> dict[str, Any] | list[dict[str, Any]]:
        """
        Parse HostFactory request data to domain-compatible format.

        This method handles the conversion from HostFactory request format to domain-compatible data.
        For [request machines]: supports both nested format: {"template": {"templateId": ...}} and flat format: {"templateId": ...}
        For [requests status]: supports both list and a single request_id
        """

        # DEBUG: Log the raw input data
        self.logger.debug("parse_request_data input: %s", raw_data)

        # Request Status
        # Handles 2 formats of requests
        # 1. {"requests": [{"requestId": "req-ABC"}, {"requestId": "req-DEF"}]}
        # 2. {"requests": {"requestId": "XYZ"}}
        if "requests" in raw_data:
            requests = raw_data["requests"]
            requests_list = requests if isinstance(requests, list) else [requests]
            result = [
                {"request_id": req.get("requestId", req.get("request_id"))} for req in requests_list
            ]
            self.logger.debug("parse_request_data output (requests): %s", result)
            return result

        # Request Machines
        # Handle nested HostFactory format: {"template": {"templateId": "...", "machineCount": ...}}
        if "template" in raw_data:
            template_data = raw_data["template"]
            self.logger.debug("Found template data: %s", template_data)
            result = {
                "template_id": template_data.get("templateId"),
                "requested_count": template_data.get("machineCount", 1),
                "request_type": template_data.get("requestType", "provision"),
                "metadata": raw_data.get("metadata", {}),
            }
            self.logger.debug("parse_request_data output (template): %s", result)
            return result

        # Handle flat HostFactory format: {"templateId": ..., "maxNumber": ...}
        # Also handle request status format: {"requestId": ...}
        result = {
            "template_id": raw_data.get("templateId") or raw_data.get("template_id"),
            "requested_count": raw_data.get(
                "requested_count", raw_data.get("maxNumber", raw_data.get("machineCount", 1))
            ),
            "request_type": raw_data.get("requestType", "provision"),
            "request_id": raw_data.get("requestId", raw_data.get("request_id")),
            "metadata": raw_data.get("metadata", {}),
        }
        self.logger.debug("parse_request_data output (flat): %s", result)
        return result

    def format_templates_response(self, templates: list[TemplateDTO]) -> dict[str, Any]:
        """
        Format TemplateDTOs to HostFactory response.

        This method handles the conversion from TemplateDTO objects to HostFactory response format.
        The format matches the expected HostFactory getAvailableTemplates output with minimal fields.
        """
        formatted_templates = []
        for template in templates:
            # Use the new architecture-compliant method
            formatted_template = self.format_template_for_display(template)

            # Ensure required HF fields are present
            if "templateId" not in formatted_template and "template_id" in formatted_template:
                formatted_template["templateId"] = formatted_template["template_id"]
            if "maxNumber" not in formatted_template and "max_instances" in formatted_template:
                formatted_template["maxNumber"] = formatted_template["max_instances"]

            # Per HF schema, instanceTags must be a string not a dict
            if "instanceTags" in formatted_template:
                tags = formatted_template["instanceTags"]
                if isinstance(tags, dict):
                    formatted_template["instanceTags"] = json.dumps(tags)
                elif tags is None:
                    del formatted_template["instanceTags"]

            # Ensure attributes object is always present (required by IBM HF spec)
            if "attributes" not in formatted_template:
                instance_type = (
                    formatted_template.get("vmType")
                    or formatted_template.get("instance_type")
                    or "t2.micro"
                )
                formatted_template["attributes"] = self._build_hf_attributes(instance_type)

            formatted_templates.append(formatted_template)

        return {
            "templates": formatted_templates,
            "message": f"Retrieved {len(formatted_templates)} templates successfully",
            "success": True,
            "total_count": len(formatted_templates),
        }

    def _build_hf_attributes(self, instance_type: str) -> dict[str, list[str]]:
        """Build IBM HF attributes dict from an instance type string."""
        from cli.field_mapping import derive_cpu_ram_from_instance_type

        ncpus, nram = derive_cpu_ram_from_instance_type(instance_type)
        return {
            "type": ["String", "X86_64"],
            "ncpus": ["Numeric", str(ncpus)],
            "ncores": ["Numeric", str(ncpus)],
            "nram": ["Numeric", str(nram)],
        }

    def format_templates_for_generation(self, templates: list[dict]) -> list[dict]:
        """Convert internal templates to HostFactory format without applying defaults."""
        processed_templates = []

        for template in templates:
            # Convert to HostFactory format for file storage WITHOUT applying defaults
            hf_template = self.field_mapper.format_for_generation([template])[0]
            processed_templates.append(hf_template)

        return processed_templates

    def format_request_status_response(self, requests: list[RequestDTO]) -> dict[str, Any]:
        """
        Format RequestDTOs to HostFactory response format.
        Only includes fields specified in HostFactory documentation.
        """
        formatted_requests = []
        for request_dto in requests:
            # Use DTO's to_dict() method
            req_dict = request_dto.to_dict()

            # Rename machine_references to machines for HostFactory compatibility
            if "machine_references" in req_dict:
                req_dict["machines"] = req_dict.pop("machine_references")

            # Convert machines to camelCase using existing method
            machines = []
            if "machines" in req_dict:
                machines = self._format_machines_for_hostfactory(
                    req_dict["machines"], request_type=req_dict.get("request_type")
                )

            # Create HostFactory-compliant request object (only HF spec fields)
            hf_request = {
                "requestId": req_dict.get("request_id"),
                "status": self._map_domain_status_to_hostfactory(
                    req_dict.get("status") or "pending"
                ),
                "message": req_dict.get("message", ""),
                "machines": machines,
            }

            # Add provider information if present
            if req_dict.get("provider_name"):
                hf_request["providerName"] = req_dict["provider_name"]
            if req_dict.get("provider_type"):
                hf_request["providerType"] = req_dict["provider_type"]
            if req_dict.get("provider_api"):
                hf_request["providerApi"] = req_dict["provider_api"]

            formatted_requests.append(hf_request)

        return {"requests": formatted_requests}

    def format_machine_status_response(self, machines: list[MachineDTO]) -> dict[str, Any]:
        """
        Format MachineDTOs to HostFactory machine response.

        This method handles the conversion from MachineDTO objects to HostFactory response format.
        """
        return {
            "machines": [
                {
                    # Domain -> HostFactory field mapping using consistent serialization
                    "instanceId": str(machine.machine_id),
                    "templateId": str(machine.template_id),
                    "requestId": str(machine.request_id),
                    "vmType": str(machine.instance_type),
                    "imageId": str(machine.image_id),
                    "privateIp": machine.private_ip,
                    "publicIp": machine.public_ip,
                    "subnetId": machine.subnet_id,
                    "securityGroupIds": machine.security_group_ids,
                    "status": str(machine.status),
                    "statusReason": machine.status_reason,
                    "launchTime": machine.launch_time,
                    "terminationTime": machine.termination_time,
                    "tags": machine.tags,
                }
                for machine in machines
            ]
        }

    def format_machine_details_response(self, machine_data: dict) -> dict:
        """Format machine details with hostfactory-specific fields."""
        return {
            "id": machine_data.get("id"),
            "name": machine_data.get("name"),
            "status": machine_data.get("status"),
            "provider": "hostfactory",
            "instance_type": machine_data.get("instance_type"),
            "region": machine_data.get("region"),
            "instanceId": machine_data.get("id"),
            "vmType": machine_data.get("instance_type"),
            "imageId": machine_data.get("image_id"),
            "privateIp": machine_data.get("private_ip"),
            "publicIp": machine_data.get("public_ip"),
            "subnetId": machine_data.get("subnet_id"),
            "securityGroupIds": machine_data.get("security_group_ids"),
            "statusReason": machine_data.get("status_reason"),
            "launchTime": machine_data.get("launch_time"),
            "terminationTime": machine_data.get("termination_time"),
            "tags": machine_data.get("tags"),
        }

    def _get_scheduler_env_var(self, suffix: str) -> str | None:
        """HostFactory checks HF_PROVIDER_* and HF_* vars."""
        import os

        mapping = {
            "CONFIG_DIR": "HF_PROVIDER_CONFDIR",
            "WORK_DIR": "HF_PROVIDER_WORKDIR",
            "LOG_DIR": "HF_PROVIDER_LOGDIR",
            "LOG_LEVEL": "HF_LOGLEVEL",
        }
        if env_var := mapping.get(suffix):
            return os.environ.get(env_var)
        return None

    def get_scheduler_type(self) -> str:
        """Return the scheduler type identifier."""
        return "hostfactory"

    def get_scripts_directory(self) -> Path | None:
        """Return the path to the HostFactory scripts directory."""
        return Path("src/infrastructure/scheduler/hostfactory/scripts/")

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def _templates_filename_pattern_key(self) -> str:
        return "provider_specific"

    def _templates_filename_fallback(self, provider_name: str, provider_type: str) -> str:
        return f"{provider_name}_templates.json"

    def should_log_to_console(self) -> bool:
        """Check if logs should be written to console for HostFactory.

        HostFactory scripts log to file by default, console only if enabled.
        """
        import os

        return os.environ.get("HF_LOGGING_CONSOLE_ENABLED", "false").lower() == "true"

    def format_error_response(self, error: Exception, context: dict[str, Any]) -> dict[str, Any]:
        """Format error response for HostFactory (JSON only)."""
        import traceback

        response = {"success": False, "error": str(error), "error_type": type(error).__name__}

        if context.get("verbose"):
            response["traceback"] = traceback.format_exc()

        return response

    def get_exit_code_for_status(self, status: str) -> int:
        """HostFactory exit codes: 1 for failures, 0 for accepted requests."""
        failure_statuses = ["failed", "cancelled", "timeout", "partial"]
        return 1 if status in failure_statuses else 0

    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check response for HostFactory."""
        passed = sum(1 for c in checks if c.get("status") == "pass")
        failed = len(checks) - passed

        return {
            "success": failed == 0,
            "checks": checks,
            "summary": {"total": len(checks), "passed": passed, "failed": failed},
        }

    def get_directory(self, file_type: str) -> str | None:
        self.logger.debug("[HF_STRATEGY] get_directory called with file_type=%s", file_type)

        if file_type in ["conf", "template", "legacy"]:
            confdir = os.environ.get("HF_PROVIDER_CONFDIR")
            workdir = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            result = confdir if confdir else os.path.join(workdir, "config")
            self.logger.debug(
                "[HF_STRATEGY] file_type=%s: HF_PROVIDER_CONFDIR=%s, HF_PROVIDER_WORKDIR=%s, result=%s",
                file_type,
                confdir,
                workdir,
                result,
            )
            return result
        elif file_type == "log":
            logdir = os.environ.get("HF_PROVIDER_LOGDIR")
            workdir = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            result = logdir if logdir else os.path.join(workdir, "logs")
            self.logger.info(
                "[HF_STRATEGY] file_type=log: HF_PROVIDER_LOGDIR=%s, HF_PROVIDER_WORKDIR=%s, result=%s",
                logdir,
                workdir,
                result,
            )
            return result
        elif file_type in ["work", "data"]:
            result = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            self.logger.debug(
                "[HF_STRATEGY] file_type=%s: HF_PROVIDER_WORKDIR=%s, result=%s",
                file_type,
                os.environ.get("HF_PROVIDER_WORKDIR"),
                result,
            )
            return result
        else:
            result = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            self.logger.debug(
                "[HF_STRATEGY] file_type=%s (default): HF_PROVIDER_WORKDIR=%s, result=%s",
                file_type,
                os.environ.get("HF_PROVIDER_WORKDIR"),
                result,
            )
            return result

    def _format_machines_for_hostfactory(
        self, machines: list[dict[str, Any]], request_type: str | None = None
    ) -> list[dict[str, Any]]:
        """Format machine data to exact HostFactory format per hf_docs/input-output.md."""
        formatted_machines = []

        for machine in machines:
            result = self._map_machine_status_to_result(
                machine.get("status"), request_type=request_type
            )

            # Per IBM HF spec, message is mandatory when result=="fail"
            if result == "fail":
                message = (
                    machine.get("status_reason")
                    or machine.get("error")
                    or machine.get("message")
                    or ""
                )
            else:
                message = machine.get("message", "")

            # Per IBM HF spec, launchtime is mandatory - default to 0 if not available
            launchtime = int(machine.get("launch_time_timestamp", 0))

            # Per IBM HF spec, privateIpAddress must be a valid IP or null (not empty string)
            raw_ip = machine.get("private_ip_address", machine.get("private_ip"))
            private_ip = raw_ip if raw_ip else None

            formatted_machine = {
                "machineId": machine.get("machine_id", machine.get("instance_id")),
                "name": machine.get(
                    "name", machine.get("instance_id", machine.get("private_ip", ""))
                ),
                "result": result,
                "status": machine.get("status", "unknown"),
                "privateIpAddress": private_ip,
                "launchtime": launchtime,
                "message": message,
                # Per IBM HF spec, cloudHostId must always be present, defaulting to null
                "cloudHostId": machine.get("cloud_host_id") or None,
            }

            formatted_machine["publicIpAddress"] = (
                machine.get("public_ip_address") or machine.get("public_ip") or None
            )
            if machine.get("instance_type"):
                formatted_machine["instanceType"] = machine["instance_type"]
            if machine.get("price_type"):
                formatted_machine["priceType"] = machine["price_type"]
            if machine.get("instance_tags"):
                tags = machine["instance_tags"]
                formatted_machine["instanceTags"] = (
                    json.dumps(tags) if isinstance(tags, dict) else str(tags)
                )

            formatted_machines.append(formatted_machine)

        return formatted_machines

    def _map_machine_status_to_result(
        self, status: str | None, request_type: str | None = None
    ) -> str:
        """Map machine status to HostFactory result field per hf_docs/input-output.md."""
        # Per docs: "Possible values: 'executing', 'fail', 'succeed'"
        if request_type == "return":
            # For return requests: terminated/stopped = success, in-flight = executing
            if status in ["terminated", "stopped"]:
                return "succeed"
            elif status in ["shutting-down", "stopping", "pending", "terminating"]:
                return "executing"
            else:
                return "fail"
        # For acquire requests, running is success
        elif status == "running":
            return "succeed"
        elif status in ["pending", "launching"]:
            return "executing"
        elif status in ["terminated", "failed", "error"]:
            return "fail"
        else:
            return "executing"  # Default for unknown states

    def _map_domain_status_to_hostfactory(self, domain_status: str) -> str:
        """Map domain status to HostFactory status per hf_docs/input-output.md."""
        # Per docs: "Possible values: 'running', 'complete', 'complete_with_error'"
        status_mapping = {
            "pending": "running",
            "in_progress": "running",
            "provisioning": "running",
            "complete": "complete",
            "completed": "complete",
            "partial": "complete_with_error",
            "failed": "complete_with_error",
            "cancelled": "complete_with_error",
        }

        return status_mapping.get(domain_status.lower(), "running")

    def _generate_status_message(self, status: str, machine_count: int) -> str:
        """Generate appropriate status message."""
        if status == "completed":
            return ""  # HostFactory examples show empty message for success
        elif status == "partial":
            return f"Partially fulfilled: {machine_count} instances created"
        elif status == "failed":
            return "Failed to create instances"
        elif status in ["pending", "in_progress", "provisioning"]:
            return ""  # HostFactory examples show empty message for running
        else:
            return ""

    def format_template_for_display(self, template: TemplateDTO) -> dict[str, Any]:
        """Format TemplateDTO for display using HostFactory field mapper."""
        internal_dict = template.to_dict()
        return self.field_mapper.map_output_fields(internal_dict, copy_unmapped=False)

    def format_template_for_provider(self, template: TemplateDTO) -> dict[str, Any]:
        """Format template for provider operations using internal format (no field mapping)."""
        return template.to_dict()

    def format_machine_for_display(self, machine_dict: dict[str, Any]) -> dict[str, Any]:
        """Format machine dict for display using HostFactory field mapper."""
        return self.field_mapper.map_output_fields(machine_dict, copy_unmapped=False)

    def format_request_for_display(self, request: RequestDTO) -> dict[str, Any]:
        """Format RequestDTO for display using HostFactory field mapper."""
        return self.field_mapper.map_output_fields(request.to_dict(), copy_unmapped=False)

    def _transform_machine_types_input(self, hf_data: dict) -> dict:
        """Transform HF vmType/vmTypes to internal machine_types."""
        if "vmType" in hf_data:
            return {"machine_types": {hf_data["vmType"]: 1}}
        elif "vmTypes" in hf_data:
            return {"machine_types": hf_data["vmTypes"]}
        return {}

    def _transform_machine_types_output(self, internal_data: dict) -> dict:
        """Transform internal machine_types to HF vmType/vmTypes."""
        machine_types = internal_data.get("machine_types", {})
        if not machine_types:
            return {}

        if len(machine_types) == 1 and next(iter(machine_types.values())) == 1:
            return {"vmType": next(iter(machine_types.keys()))}
        else:
            return {"vmTypes": machine_types}
