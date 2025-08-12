"""HostFactory scheduler strategy for field mapping and response formatting."""

import os
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    pass

from src.config.manager import ConfigurationManager
from src.domain.base.ports.logging_port import LoggingPort
from src.domain.exceptions import ConfigurationError
from src.domain.base.ports.scheduler_port import SchedulerPort
from src.domain.machine.aggregate import Machine
from src.domain.request.aggregate import Request
from src.domain.template.aggregate import Template
from src.infrastructure.utilities.common.serialization import serialize_enum

from .field_mappings import HostFactoryFieldMappings
from .transformations import HostFactoryTransformations


class HostFactorySchedulerStrategy(SchedulerPort):
    """HostFactory scheduler strategy for field mapping and response formatting."""

    def __init__(
        self,
        config_manager: ConfigurationManager,
        logger: LoggingPort,
        template_defaults_service=None,
    ):
        """Initialize the instance."""
        self.config_manager = config_manager
        self._logger = logger
        self.template_defaults_service = template_defaults_service

    def get_templates_file_path(self) -> str:
        """Get the templates file path for HostFactory."""
        provider_config = self.config_manager.get_provider_config()
        if not provider_config or not hasattr(provider_config, 'active_provider'):
            raise ConfigurationError("Provider configuration or active_provider not found")
        
        active_provider = provider_config.active_provider
        provider_type = active_provider.split("-")[0]
        templates_file = f"{provider_type}prov_templates.json"

        return self.config_manager.resolve_file("template", templates_file)

    def get_template_paths(self) -> List[str]:
        """Get template file paths."""
        return [self.get_templates_file_path()]

    def load_templates_from_path(self, template_path: str) -> List[Dict[str, Any]]:
        """Load and process templates from a file path with field mapping."""
        try:
            import json

            with open(template_path, "r") as f:
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
                        f"Skipping invalid template { template.get( 'id', 'unknown')}: {e}"
                    )
                    continue

            return processed_templates

        except Exception:
            # Return empty list on error - let caller handle logging
            return []

    def _map_template_fields(self, template: Dict[str, Any]) -> Dict[str, Any]:
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

        # Handle vmTypes -> instance_types mapping (missing from old implementation)
        if "vmTypes" in template and isinstance(template["vmTypes"], dict):
            mapped["instance_types"] = template["vmTypes"]
            # Ensure primary instance_type is set if not already present
            if "instance_type" not in mapped or not mapped["instance_type"]:
                mapped["instance_type"] = list(template["vmTypes"].keys())[0]

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
            f"Mapped template fields: { len(field_mappings)} HostFactory mappings applied for {provider_type} provider"
        )

        return mapped

    def _get_active_provider_type(self) -> str:
        """Get the active provider type from configuration."""
        try:
            provider_config = self.config_manager.get_provider_config()
            if not provider_config or not hasattr(provider_config, 'active_provider'):
                raise ConfigurationError("Provider configuration or active_provider not found")
            
            active_provider = provider_config.active_provider  # e.g., 'aws-default'
            provider_type = active_provider.split("-")[0]  # Extract 'aws' from 'aws-default'
            self._logger.debug(f"Active provider type: {provider_type}")
            return provider_type
        except Exception as e:
            self._logger.warning(f"Failed to get active provider type, defaulting to 'aws': {e}")
            return "aws"  # Default fallback

    def convert_cli_args_to_hostfactory_input(self, operation: str, args: Any) -> Dict[str, Any]:
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

    def convert_domain_to_hostfactory_output(
        self, operation: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
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

    def _convert_template_to_hostfactory(self, template: Template) -> Dict[str, Any]:
        """Convert internal template to HostFactory format."""
        # Handle both domain Template objects and TemplateDTO objects
        if hasattr(template, "to_dict"):
            template_dict = template.to_dict()
        elif hasattr(template, "__dict__"):
            template_dict = template.__dict__
        else:
            template_dict = template

        # Convert to HostFactory format with proper HF attributes
        hf_template = {
            "templateId": template_dict.get("template_id", template_dict.get("templateId", "")),
            "maxNumber": template_dict.get("max_instances", template_dict.get("maxNumber", 1)),
            "attributes": self._create_hf_attributes(template_dict),
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
        }
        return mapping.get(hf_field, hf_field)

    def _create_hf_attributes(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create HF-compatible attributes object with CPU/RAM specs.

        This method handles the creation of HostFactory attributes with proper
        CPU and RAM specifications based on instance type.
        """
        # Handle both snake_case and camelCase field names
        instance_type = template_data.get("instance_type") or template_data.get(
            "instanceType", "t2.micro"
        )

        # CPU/RAM mapping for common instance types
        cpu_ram_mapping = {
            "t2.micro": {"ncpus": "1", "nram": "1024"},
            "t2.small": {"ncpus": "1", "nram": "2048"},
            "t2.medium": {"ncpus": "2", "nram": "4096"},
            "t3.micro": {"ncpus": "2", "nram": "1024"},
            "t3.small": {"ncpus": "2", "nram": "2048"},
            "t3.medium": {"ncpus": "2", "nram": "4096"},
            "m5.large": {"ncpus": "2", "nram": "8192"},
            "m5.xlarge": {"ncpus": "4", "nram": "16384"},
            "c5.large": {"ncpus": "2", "nram": "4096"},
            "c5.xlarge": {"ncpus": "4", "nram": "8192"},
            "r5.large": {"ncpus": "2", "nram": "16384"},
            "r5.xlarge": {"ncpus": "4", "nram": "32768"},
        }

        # Get specs for instance type, default to t2.micro specs
        specs = cpu_ram_mapping.get(instance_type, {"ncpus": "1", "nram": "1024"})

        # Return HF-compatible attributes format
        return {
            "type": ["String", "X86_64"],
            "ncpus": ["Numeric", specs["ncpus"]],
            "nram": ["Numeric", specs["nram"]],
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

    def parse_template_config(self, raw_data: Dict[str, Any]) -> Template:
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
        }

        # Create domain Template object with validation
        return Template(**domain_data)

    def parse_request_data(self, raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse HostFactory request data to domain-compatible format.

        This method handles the conversion from HostFactory request format to domain-compatible data.
        """
        return {
            "template_id": raw_data.get("templateId"),
            "requested_count": raw_data.get("maxNumber", 1),
            "request_type": raw_data.get("requestType", "provision"),
            "metadata": raw_data.get("metadata", {}),
        }

    def format_templates_response(self, templates: List[Template]) -> Dict[str, Any]:
        """
        Format domain Templates to HostFactory response.

        This method handles the conversion from domain Template objects to HostFactory response format.
        """
        return {
            "templates": [
                {
                    # Core template fields - Domain -> HostFactory
                    "templateId": template.template_id,
                    "name": template.name,
                    "description": template.description,
                    # Instance configuration - Domain -> HostFactory
                    "vmType": template.instance_type,
                    "imageId": template.image_id,
                    "maxNumber": template.max_instances,
                    # Network configuration - Domain -> HostFactory
                    "subnetIds": template.subnet_ids,
                    "securityGroupIds": template.security_group_ids,
                    # Pricing and allocation - Domain -> HostFactory
                    "priceType": template.price_type,
                    "allocationStrategy": template.allocation_strategy,
                    "maxPrice": template.max_price,
                    # Tags and metadata - Domain -> HostFactory
                    "tags": template.tags,
                    "metadata": template.metadata,
                    # Provider API - Domain -> HostFactory
                    "providerApi": template.provider_api,
                    # Timestamps - Domain -> HostFactory
                    "createdAt": template.created_at,
                    "updatedAt": template.updated_at,
                    "isActive": template.is_active,
                    # HostFactory-specific fields - Domain -> HostFactory
                    "keyName": template.key_name,
                    "userData": template.user_data,
                    "vmTypes": template.vm_types,
                }
                for template in templates
            ]
        }

    def format_request_status_response(self, requests: List[Request]) -> Dict[str, Any]:
        """
        Format domain Requests to HostFactory status response.

        This method handles the conversion from domain Request objects to HostFactory response format.
        """
        return {
            "requests": [
                {
                    # Domain -> HostFactory field mapping using consistent serialization
                    "requestId": serialize_enum(request.request_id) or str(request.request_id),
                    "requestType": serialize_enum(request.request_type)
                    or str(request.request_type),
                    "templateId": str(request.template_id),
                    "maxNumber": request.requested_count,
                    "numAllocated": request.successful_count,
                    "status": serialize_enum(request.status) or str(request.status),
                    "statusMessage": request.status_message,
                    "instanceIds": [
                        serialize_enum(inst_id) or str(inst_id) for inst_id in request.instance_ids
                    ],
                    "createdAt": request.created_at,
                    "startedAt": request.started_at,
                    "completedAt": request.completed_at,
                    "errorDetails": request.error_details,
                }
                for request in requests
            ]
        }

    def format_machine_status_response(self, machines: List[Machine]) -> Dict[str, Any]:
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

    def get_storage_base_path(self) -> str:
        """Get storage base path within working directory."""
        workdir = self.get_working_directory()
        return os.path.join(workdir, "data")

    def _format_machines_for_hostfactory(
        self, machines: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
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
