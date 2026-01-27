"""HostFactory scheduler strategy for field mapping and response formatting."""

import os
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from domain.request.aggregate import Request

from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from domain.machine.aggregate import Machine
from domain.template.template_aggregate import Template
from infrastructure.scheduler.base.strategy import BaseSchedulerStrategy
from infrastructure.utilities.common.serialization import serialize_enum

from .field_mappings import HostFactoryFieldMappings
from .transformations import HostFactoryTransformations


class HostFactorySchedulerStrategy(BaseSchedulerStrategy):
    """HostFactory scheduler strategy for field mapping and response formatting."""

    def __init__(
        self,
        config_manager: ConfigurationPort,
        logger: LoggingPort,
        template_defaults_service=None,
    ) -> None:
        """Initialize the instance."""
        self.config_manager = config_manager
        self._logger = logger
        self.template_defaults_service = template_defaults_service

        # Initialize provider selection service for provider selection
        from application.services.provider_selection_service import (
            ProviderSelectionService,
        )
        from infrastructure.di.container import get_container

        container = get_container()
        self._provider_selection_service = container.get(ProviderSelectionService)

    def get_templates_file_path(self) -> str:
        """Get the templates file path for HostFactory."""
        try:
            # Use provider selection service for provider selection
            selection_result = self._provider_selection_service.select_active_provider()
            provider_type = selection_result.provider_type
            
            # Use ConfigurationManager's unified template discovery
            return self.config_manager.find_templates_file(provider_type)
            
        except Exception as e:
            self._logger.error("Failed to determine templates file path: %s", e)
            # Re-raise with context - let caller handle the error
            raise

    def get_template_paths(self) -> list[str]:
        """Get template file paths."""
        return [self.get_templates_file_path()]

    def load_templates_from_path(self, template_path: str) -> list[dict[str, Any]]:
        """Load and process templates from a file path with field mapping."""
        try:
            import json

            with open(template_path) as f:
                data = json.load(f)

            # Handle different template file formats
            if isinstance(data, dict) and "templates" in data:
                template_data = data["templates"]
            elif isinstance(data, list):
                template_data = data
            else:
                return []

            # Process each template with field mapping
            processed_templates = []
            for template in template_data:
                if template is None:
                    continue

                try:
                    processed_template = self._map_template_fields(template)
                    processed_templates.append(processed_template)
                except Exception as e:
                    # Skip invalid templates but log the issue
                    self._logger.warning(
                        "Skipping invalid template %s: %s",
                        template.get("id", "unknown"),
                        e,
                    )
                    continue

            return processed_templates

        except Exception:
            # Return empty list on error - let caller handle logging
            return []

    def _map_template_fields(self, template: dict[str, Any]) -> dict[str, Any]:
        """
        Map HostFactory standard fields to internal domain model fields.

        This method handles the conversion from HostFactory format to internal domain model
        using HostFactory-specific field mappings.
        """
        if template is None:
            raise ValueError("Template cannot be None in field mapping")

        if not isinstance(template, dict):
            raise ValueError(f"Template must be a dictionary, got {type(template)}")

        # Get active provider type for provider-aware mapping
        provider_type = self._get_active_provider_type()

        # Get HostFactory-specific field mappings
        field_mappings = HostFactoryFieldMappings.get_mappings(provider_type)

        mapped = {}

        # Apply HostFactory field mappings
        for hf_field, internal_field in field_mappings.items():
            if hf_field in template:
                mapped[internal_field] = template[hf_field]

        # Apply HostFactory transformations
        mapped = HostFactoryTransformations.apply_transformations(mapped)

        # === SPECIAL HANDLING FOR COMPLEX FIELDS ===

        # Handle vmTypes -> instance_types mapping
        if "vmTypes" in template and isinstance(template["vmTypes"], dict):
            mapped["instance_types"] = template["vmTypes"]
            # Ensure primary instance_type is set if not already present
            if "instance_type" not in mapped or not mapped["instance_type"]:
                mapped["instance_type"] = next(iter(template["vmTypes"].keys()))

        # === ATTRIBUTES (HostFactory standard) ===

        # Copy attributes as-is (required by HostFactory)
        if "attributes" in template:
            mapped["attributes"] = template["attributes"]

        # === METADATA AND DEFAULTS ===

        # Set provider API using defaults service if available, otherwise fallback
        if self.template_defaults_service:
            mapped["provider_api"] = self.template_defaults_service.resolve_provider_api_default(
                template
            )
        else:
            # Fallback to template value or default
            mapped["provider_api"] = template.get(
                "providerApi", template.get("provider_api", "EC2Fleet")
            )

        # Set name (use template_id if not provided)
        if "template_id" in mapped:
            mapped["name"] = template.get("name", mapped["template_id"])

        # Set defaults for required fields
        mapped.setdefault("max_instances", 1)
        mapped.setdefault("price_type", "ondemand")
        mapped.setdefault("allocation_strategy", "lowest_price")
        mapped.setdefault("subnet_ids", [])
        mapped.setdefault("security_group_ids", [])
        mapped.setdefault("tags", {})

        # Copy timestamps if present
        mapped["created_at"] = template.get("created_at")
        mapped["updated_at"] = template.get("updated_at")
        mapped["version"] = template.get("version")

        self._logger.debug(
            "Mapped template fields: %s HostFactory mappings applied for %s provider",
            len(field_mappings),
            provider_type,
        )

        return mapped

    def _get_active_provider_type(self) -> str:
        """Get the active provider type from configuration."""
        try:
            # Use provider selection service for provider selection
            selection_result = self._provider_selection_service.select_active_provider()
            provider_type = selection_result.provider_type
            self._logger.debug("Active provider type: %s", provider_type)
            return provider_type
        except Exception as e:
            self._logger.warning("Failed to get active provider type, defaulting to 'aws': %s", e)
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
        # Allow both dicts and Pydantic-style objects
        if hasattr(request_data, "model_dump"):
            request_dict = request_data.model_dump()
        elif hasattr(request_data, "to_dict"):
            request_dict = request_data.to_dict()
        elif isinstance(request_data, dict):
            request_dict = request_data
        else:
            # Fallback: try to coerce to dict
            try:
                request_dict = dict(request_data)
            except Exception:
                request_dict = {}

        if "requests" in request_dict:
            return {
                "requests": request_dict.get("requests", []),
                "status": request_dict.get("status", "complete"),
                "message": request_dict.get("message", "Status retrieved successfully"),
            }

        # Prefer snake_case in API responses
        request_id = request_dict.get("request_id", request_dict.get("requestId"))
        return {
            # HostFactory schema expects camelCase requestId
            "requestId": request_id,
            "message": request_dict.get("message", "Request VM success from AWS."),
        }

    def convert_domain_to_hostfactory_output(
        self, operation: str, data: dict[str, Any]
    ) -> dict[str, Any]:
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
            base_message = "Request VM success from AWS."
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

                machines = self._format_machines_for_hostfactory(machines_data)
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
                machines = self._format_machines_for_hostfactory(data.get("machines", []))
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

    def _convert_template_to_hostfactory(self, template: Template) -> dict[str, Any]:
        """Convert internal template to HostFactory format."""
        # Handle both domain Template objects and TemplateDTO objects
        if hasattr(template, "to_dict"):
            template_dict = template.to_dict()
        elif hasattr(template, "__dict__"):
            template_dict = template.__dict__
        else:
            template_dict = template

        # Convert to HostFactory format with HF attributes
        hf_template = {
            "templateId": template_dict.get("template_id", template_dict.get("templateId", "")),
            "maxNumber": template_dict.get("max_instances", template_dict.get("maxNumber", 1)),
            "attributes": self._create_hf_attributes_from_template(template_dict),
        }

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

    def _create_hf_attributes_from_template(self, template) -> dict[str, Any]:
        """Create HF-compatible attributes object from Template domain object or dict.

        This method handles the creation of HostFactory attributes with
        CPU and RAM specifications based on the template's instance type.

        Args:
            template: Template domain object or dict with template data
        """
        # Handle both Template objects and dictionaries
        if hasattr(template, "instance_type"):
            # Template domain object - direct property access
            instance_type = template.instance_type or "t2.micro"
        elif isinstance(template, dict):
            # Dictionary - handle both snake_case and camelCase field names
            instance_type = template.get("instance_type") or template.get(
                "instanceType", "t2.micro"
            )
        else:
            # Fallback for other types
            instance_type = "t2.micro"

        # Use centralized function to derive CPU/RAM from instance type
        from cli.field_mapping import derive_cpu_ram_from_instance_type

        ncpus, nram = derive_cpu_ram_from_instance_type(instance_type)

        # Return HF-compatible attributes format matching the expected output
        # Note: This method includes ncores for backward compatibility
        return {
            "nram": ["Numeric", nram],
            "ncpus": ["Numeric", ncpus],
            "ncores": ["Numeric", ncpus],  # Use same value as ncpus for cores
            "type": ["String", "X86_64"],
        }

    def get_config_file_path(self) -> str:
        """Get config file path using configuration."""
        # Get raw config and build path manually
        config = self.config_manager.get_app_config()

        # Get scheduler config root
        scheduler_config = config.get("scheduler", {})
        config_root = scheduler_config.get("config_root", "config")

        # Get provider type from active provider
        provider_config = config.get("provider", {})
        active_provider = provider_config.get("active_provider", "aws-default")
        provider_type = active_provider.split("-")[0]

        # Build config file path
        config_file = f"{provider_type}prov_config.json"
        return os.path.join(config_root, config_file)

    # KBG TODO: function is not used
    def parse_template_config(self, raw_data: dict[str, Any]) -> Template:
        """
        Parse HostFactory template to domain Template.

        This method handles the conversion from HostFactory template format to domain Template objects.
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
            "provider_api": raw_data.get("providerApi"),
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

        # Create domain Template object with validation
        return Template(**domain_data)

    def parse_request_data(
        self, raw_data: dict[str, Any]
    ) -> Union[dict[str, Any], list[dict[str, Any]]]:
        """
        Parse HostFactory request data to domain-compatible format.

        This method handles the conversion from HostFactory request format to domain-compatible data.
        For [request machines]: supports both nested format: {"template": {"templateId": ...}} and flat format: {"templateId": ...}
        For [requests status]: supports both list and a single request_id
        """

        # DEBUG: Log the raw input data
        self._logger.debug("parse_request_data input: %s", raw_data)

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
            self._logger.debug("parse_request_data output (requests): %s", result)
            return result

        # Request Machines
        # Handle nested HostFactory format: {"template": {"templateId": "...", "machineCount": ...}}
        if "template" in raw_data:
            template_data = raw_data["template"]
            self._logger.debug("Found template data: %s", template_data)
            result = {
                "template_id": template_data.get("templateId"),
                "requested_count": template_data.get("machineCount", 1),
                "request_type": template_data.get("requestType", "provision"),
                "metadata": raw_data.get("metadata", {}),
            }
            self._logger.debug("parse_request_data output (template): %s", result)
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
        self._logger.debug("parse_request_data output (flat): %s", result)
        return result

    def format_templates_response(self, templates: list[Template]) -> dict[str, Any]:
        """
        Format domain Templates to HostFactory response.

        This method handles the conversion from domain Template objects to HostFactory response format.
        The format matches the expected HostFactory getAvailableTemplates output with minimal fields.
        """
        return {
            "templates": [
                {
                    "templateId": template.template_id,
                    "maxNumber": template.max_instances,
                    "attributes": self._create_hf_attributes_from_template(template),
                    "pgrpName": None,
                    "onDemandCapacity": 0,
                }
                for template in templates
            ]
        }

    def format_request_status_response(self, requests: list["Request"]) -> dict[str, Any]:
        """
        Format domain Requests to HostFactory response format.
        Converts machine fields from snake_case to camelCase.
        """
        formatted_requests = []
        for request in requests:
            req_dict = request.to_dict()
            # Convert machines to camelCase
            if "machines" in req_dict:
                req_dict["machines"] = [
                    self._convert_machine_to_camel(m) for m in req_dict["machines"]
                ]
            formatted_requests.append(req_dict)

        return {
            "requests": formatted_requests,
            "message": "Request status retrieved successfully",
            "count": len(requests),
        }

    def _convert_machine_to_camel(self, machine: dict[str, Any]) -> dict[str, Any]:
        """Convert machine dict from snake_case to camelCase for HostFactory."""
        result = {
            "machineId": machine.get("machine_id"),
            "name": machine.get("name"),
            "result": machine.get("result"),
            "status": machine.get("status"),
            "privateIpAddress": machine.get("private_ip_address"),
            "message": machine.get("message", ""),
        }
        if machine.get("public_ip_address"):
            result["publicIpAddress"] = machine["public_ip_address"]
        if machine.get("instance_type"):
            result["instanceType"] = machine["instance_type"]
        if machine.get("price_type"):
            result["priceType"] = machine["price_type"]
        if machine.get("instance_tags"):
            result["instanceTags"] = machine["instance_tags"]
        if machine.get("cloud_host_id"):
            result["cloudHostId"] = machine["cloud_host_id"]
        if machine.get("launch_time"):
            result["launchtime"] = str(machine["launch_time"])
        return result

    def format_machine_status_response(self, machines: list[Machine]) -> dict[str, Any]:
        """
        Format domain Machines to HostFactory machine response.

        This method handles the conversion from domain Machine objects to HostFactory response format.
        """
        return {
            "machines": [
                {
                    # Domain -> HostFactory field mapping using consistent serialization
                    "instanceId": serialize_enum(machine.instance_id) or str(machine.instance_id),
                    "templateId": str(machine.template_id),
                    "requestId": str(machine.request_id),
                    "vmType": serialize_enum(machine.instance_type) or str(machine.instance_type),
                    "imageId": str(machine.image_id),
                    "privateIp": machine.private_ip,
                    "publicIp": machine.public_ip,
                    "subnetId": machine.subnet_id,
                    "securityGroupIds": machine.security_group_ids,
                    "status": serialize_enum(machine.status) or str(machine.status),
                    "statusReason": machine.status_reason,
                    "launchTime": machine.launch_time,
                    "terminationTime": machine.termination_time,
                    "tags": serialize_enum(machine.tags) or machine.tags,
                }
                for machine in machines
            ]
        }

    def get_working_directory(self) -> str:
        """Get working directory from HF_PROVIDER_WORKDIR."""
        return os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())

    def get_config_directory(self) -> str:
        """Get config directory from HF_PROVIDER_CONFDIR."""
        return os.environ.get(
            "HF_PROVIDER_CONFDIR", os.path.join(self.get_working_directory(), "config")
        )

    def get_logs_directory(self) -> str:
        """Get logs directory from HF_PROVIDER_LOGDIR."""
        return os.environ.get(
            "HF_PROVIDER_LOGDIR", os.path.join(self.get_working_directory(), "logs")
        )

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    @classmethod
    def get_templates_filename(cls, provider_name: str, provider_type: str, config: dict = None) -> str:
        """Get templates filename with config override support.
        
        Can be called as classmethod (before app init) or instance method.
        """
        # Check config override first
        if config:
            scheduler_config = config.get("scheduler", {})
            config_filename = scheduler_config.get("templates_filename")
            if config_filename:
                return config_filename
        
        # Use HostFactory default: provider_name + '_templates.json'
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
        
        response = {
            "success": False,
            "error": str(error),
            "error_type": type(error).__name__
        }
        
        if context.get("verbose"):
            response["traceback"] = traceback.format_exc()
        
        return response

    def format_health_response(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        """Format health check response for HostFactory."""
        passed = sum(1 for c in checks if c.get("status") == "pass")
        failed = len(checks) - passed
        
        return {
            "success": failed == 0,
            "checks": checks,
            "summary": {
                "total": len(checks),
                "passed": passed,
                "failed": failed
            }
        }

    def get_directory(self, file_type: str) -> str | None:
        self._logger.debug("[HF_STRATEGY] get_directory called with file_type=%s", file_type)

        if file_type in ["conf", "template", "legacy"]:
            confdir = os.environ.get("HF_PROVIDER_CONFDIR")
            workdir = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            result = confdir if confdir else os.path.join(workdir, "config")
            self._logger.debug(
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
            self._logger.info(
                "[HF_STRATEGY] file_type=log: HF_PROVIDER_LOGDIR=%s, HF_PROVIDER_WORKDIR=%s, result=%s",
                logdir,
                workdir,
                result,
            )
            return result
        elif file_type in ["work", "data"]:
            result = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            self._logger.debug(
                "[HF_STRATEGY] file_type=%s: HF_PROVIDER_WORKDIR=%s, result=%s",
                file_type,
                os.environ.get("HF_PROVIDER_WORKDIR"),
                result,
            )
            return result
        else:
            result = os.environ.get("HF_PROVIDER_WORKDIR", os.getcwd())
            self._logger.debug(
                "[HF_STRATEGY] file_type=%s (default): HF_PROVIDER_WORKDIR=%s, result=%s",
                file_type,
                os.environ.get("HF_PROVIDER_WORKDIR"),
                result,
            )
            return result

    def _format_machines_for_hostfactory(
        self, machines: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Format machine data to exact HostFactory format per hf_docs/input-output.md."""
        formatted_machines = []

        for machine in machines:
            # Follow exact HostFactory getRequestStatus output format
            formatted_machine = {
                "machineId": machine.get("instance_id"),
                "name": machine.get("private_ip", machine.get("instance_id", "")),
                "result": self._map_machine_status_to_result(machine.get("status")),
                "status": machine.get("status", "unknown"),
                "privateIpAddress": machine.get("private_ip"),
                "publicIpAddress": machine.get("public_ip"),
                "launchtime": int(machine.get("launch_time_timestamp", 0)),
                "message": "",
            }
            formatted_machines.append(formatted_machine)

        return formatted_machines

    def _map_machine_status_to_result(self, status: str) -> str:
        """Map machine status to HostFactory result field per hf_docs/input-output.md."""
        # Per docs: "Possible values: 'executing', 'fail', 'succeed'"
        if status == "running":
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
