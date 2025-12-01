#!/usr/bin/env python3
"""Template processor for onaws tests - generates populated templates from base templates and config."""

import json
import logging
from pathlib import Path
from typing import Any, Dict

log = logging.getLogger(__name__)


class TemplateProcessor:
    """Processes base templates and generates populated templates for tests."""

    def __init__(self, base_dir: str = None):
        """Initialize the template processor.

        Args:
            base_dir: Base directory for the onaws tests (defaults to current file's directory)
        """
        if base_dir is None:
            base_dir = Path(__file__).parent
        else:
            base_dir = Path(base_dir)

        self.base_dir = base_dir
        self.config_templates_dir = base_dir / "config_templates"
        # Use the main config folder as source
        self.config_source_dir = base_dir.parent.parent / "config"
        self.run_templates_dir = base_dir / "run_templates"

    def detect_scheduler_type(self, overrides: dict) -> str:
        """
        Detect scheduler type from overrides.

        Args:
            overrides: Dictionary containing test overrides

        Returns:
            Scheduler type: "default" or "hostfactory"
            Defaults to "hostfactory" for backward compatibility
        """
        scheduler_type = overrides.get("scheduler", "hostfactory") if overrides else "hostfactory"
        log.info(f"Detected scheduler type: {scheduler_type}")
        return scheduler_type

    def select_base_template_for_scheduler(self, base_name: str, scheduler_type: str) -> str:
        """
        Select appropriate base template based on scheduler type.

        Args:
            base_name: Base template name (e.g., "awsprov_templates")
            scheduler_type: "default" or "hostfactory"

        Returns:
            Template filename to use

        Mapping:
            - templates + default → templates.base.json (snake_case fields)
            - awsprov_templates + hostfactory → awsprov_templates.base.json (camelCase fields)
            - default_config/config → corresponding *.base.json files
        """
        if base_name == "awsprov_templates" and scheduler_type == "default":
            return "templates"
        return base_name

    def load_config_source(self) -> Dict[str, Any]:
        """Load configuration values from the main config directory."""
        # Load from config.json
        config_file = self.config_source_dir / "config.json"
        awsprov_templates_file = self.config_source_dir / "awsprov_templates.json"

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration source not found: {config_file}")

        if not awsprov_templates_file.exists():
            raise FileNotFoundError(f"AWS provider templates not found: {awsprov_templates_file}")

        # Load config.json
        with open(config_file) as f:
            config_data = json.load(f)

        # Load awsprov_templates.json
        with open(awsprov_templates_file) as f:
            templates_data = json.load(f)

        # Extract configuration values from the loaded files
        extracted_config = {}

        # Extract from config.json
        if "provider" in config_data:
            # Extract active_provider
            extracted_config["active_provider"] = config_data["provider"].get(
                "active_provider", "aws-default"
            )

            if "providers" in config_data["provider"]:
                provider = config_data["provider"]["providers"][0]
                if "config" in provider:
                    extracted_config["region"] = provider["config"].get("region", "us-east-1")
                    extracted_config["profile"] = provider["config"].get("profile", "default")

            # Extract template defaults if available
            if "provider_defaults" in config_data["provider"]:
                aws_defaults = config_data["provider"]["provider_defaults"].get("aws", {})
                template_defaults = aws_defaults.get("template_defaults", {})

                extracted_config["image_id"] = template_defaults.get("image_id", "ami-12345678")
                extracted_config["security_group_ids"] = template_defaults.get(
                    "security_group_ids", ["sg-12345678"]
                )
                extracted_config["subnet_ids"] = template_defaults.get(
                    "subnet_ids", ["subnet-12345678"]
                )

                # Keep legacy keys for backward compatibility
                extracted_config["imageId"] = extracted_config["image_id"]
                if extracted_config["subnet_ids"]:
                    extracted_config["subnetId"] = extracted_config["subnet_ids"][0]
                else:
                    extracted_config["subnetId"] = "subnet-12345678"
                extracted_config["securityGroupIds"] = extracted_config["security_group_ids"]

        # Extract from awsprov_templates.json (use first template as reference)
        # Only use these values if they're not already set from config.json
        if templates_data.get("templates"):
            first_template = templates_data["templates"][0]

            # Override with actual values from templates if available and not already set
            if "imageId" in first_template and "imageId" not in extracted_config:
                extracted_config["imageId"] = first_template["imageId"]
            if "subnetId" in first_template and "subnetId" not in extracted_config:
                extracted_config["subnetId"] = first_template["subnetId"]
            if "securityGroupIds" in first_template and "securityGroupIds" not in extracted_config:
                extracted_config["securityGroupIds"] = first_template["securityGroupIds"]
            if "instanceProfile" in first_template:
                extracted_config["instanceProfile"] = first_template["instanceProfile"]
            if "fleetRole" in first_template:
                extracted_config["fleetRole"] = first_template["fleetRole"]
            if "userDataScript" in first_template:
                extracted_config["userDataScript"] = first_template["userDataScript"]

        # Set default values for fields that might be overridden
        extracted_config.setdefault("fleetType", "request")  # Default fleet type
        extracted_config.setdefault("providerApi", "EC2Fleet")
        extracted_config.setdefault("priceType", "ondemand")  # Default price type
        # Default to 100% on-demand to avoid unintentionally requesting spot capacity
        extracted_config.setdefault("percentOnDemand", 100)

        return extracted_config

    def load_base_template(self, template_name: str) -> Dict[str, Any]:
        """Load a base template file.

        Args:
            template_name: Name of the template file (e.g., 'awsprov_templates.base.json', 'config.base.json')
        """
        # If template_name doesn't have extension, add .base.json for backward compatibility
        if not template_name.endswith(".base.json"):
            template_name = f"{template_name}.base.json"

        template_file = self.config_templates_dir / template_name
        if not template_file.exists():
            raise FileNotFoundError(f"Base template not found: {template_file}")

        with open(template_file) as f:
            return json.load(f)

    def replace_placeholders(
        self, template: Dict[str, Any], config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Replace placeholders in template with actual config values.

        Args:
            template: Template dictionary with {{placeholder}} values
            config: Configuration dictionary with actual values

        Returns:
            Template with placeholders replaced
        """
        # Convert template to JSON string for easy replacement
        template_str = json.dumps(template, indent=2)

        # Replace each placeholder
        for key, value in config.items():
            placeholder = f"{{{{{key}}}}}"
            if isinstance(value, list):
                # Handle arrays (like securityGroupIds)
                value_str = json.dumps(value)
            elif isinstance(value, str):
                value_str = f'"{value}"'
            else:
                value_str = json.dumps(value)

            template_str = template_str.replace(f'"{placeholder}"', value_str)

        # Parse back to dictionary
        return json.loads(template_str)

    def generate_test_templates(
        self,
        test_name: str,
        base_template: str = None,
        awsprov_base_template: str = None,
        overrides: dict = None,
    ) -> None:
        """Generate populated templates for a specific test.

        Args:
            test_name: Name of the test (used as directory name)
            base_template: Optional base template name to use for config files (defaults to standard templates)
            awsprov_base_template: Optional base template name for awsprov_templates (e.g., "awsprov_templates1", "awsprov_templates2")
            overrides: Optional dictionary of configuration overrides
        """
        # Detect scheduler type from overrides
        scheduler_type = self.detect_scheduler_type(overrides)

        # Create test directory
        test_dir = self.run_templates_dir / test_name
        test_dir.mkdir(parents=True, exist_ok=True)

        # Load configuration
        config = self.load_config_source()

        # Apply overrides if provided
        if overrides:
            config.update(overrides)
            # Handle legacy field mappings for overrides
            if "imageId" in overrides:
                config["image_id"] = overrides["imageId"]
            if "subnetId" in overrides:
                config["subnet_ids"] = [overrides["subnetId"]]
            if "securityGroupIds" in overrides:
                config["security_group_ids"] = overrides["securityGroupIds"]
            # Normalize vmTypes to snake_case for default scheduler configs
            if "vmTypes" in overrides and "vm_types" not in overrides:
                config["vm_types"] = overrides["vmTypes"]
                overrides["vm_types"] = overrides["vmTypes"]
            # Also map to instance_types for default scheduler templates that expect this key
            if "vm_types" in overrides and "instance_types" not in overrides:
                config["instance_types"] = overrides["vm_types"]
                overrides["instance_types"] = overrides["vm_types"]

            # Set percentOnDemand based on priceType (only for provider APIs that support it)
            if "priceType" in overrides:
                provider_api = overrides.get("providerApi", config.get("providerApi", "EC2Fleet"))

                # If percentOnDemand is explicitly provided in overrides, use it
                if "percentOnDemand" in overrides:
                    config["percentOnDemand"] = overrides["percentOnDemand"]
                elif provider_api in ["EC2Fleet", "SpotFleet"]:
                    # EC2Fleet and SpotFleet support percentOnDemand
                    if overrides["priceType"] == "spot":
                        config["percentOnDemand"] = 0  # 100% spot instances
                    elif overrides["priceType"] == "ondemand":
                        config["percentOnDemand"] = 100  # 100% on-demand instances
                elif provider_api in ["RunInstances", "ASG"]:
                    # RunInstances and ASG don't use percentOnDemand the same way as fleets.
                    # Ensure on-demand requests stay on-demand; spot explicitly sets 0.
                    if overrides["priceType"] == "spot":
                        config["percentOnDemand"] = 0
                    else:
                        config["percentOnDemand"] = 100

        # Set scheduler type in config for template replacement (after overrides)
        config["scheduler"] = scheduler_type
        log.info(f"Config scheduler value for template replacement: {config.get('scheduler')}")

        # Generate each required file
        # Note: awsprov_templates.json is only for hostfactory scheduler
        template_files = [
            ("config", "config.json"),
        ]

        if scheduler_type == "hostfactory":
            template_files.insert(0, ("awsprov_templates", "awsprov_templates.json"))
        elif scheduler_type == "default":
            template_files.insert(0, ("templates", "templates.json"))

        for base_name, output_name in template_files:
            try:
                # Determine which base template to use
                if base_name == "awsprov_templates" and awsprov_base_template:
                    # Use specified awsprov base template
                    template_name = awsprov_base_template
                elif base_name == "config" and base_template:
                    # Use specified config base template
                    template_name = base_template
                else:
                    # Use scheduler-aware template selection
                    template_name = self.select_base_template_for_scheduler(
                        base_name, scheduler_type
                    )

                # Load base template
                base_template_data = self.load_base_template(template_name)

                # Replace placeholders
                populated_template = self.replace_placeholders(base_template_data, config)

                # Apply explicit overrides for fields not covered by placeholders
                if overrides and isinstance(populated_template, dict):
                    templates_list = populated_template.get("templates", [])
                    for tmpl in templates_list:
                        # Apply overrides with scheduler-aware key mapping
                        scheduler_is_default = config.get("scheduler") == "default"

                        # Handle VM types separately to avoid duplicate fields
                        if any(k in overrides for k in ("vm_types", "vmTypes", "instance_types")):
                            vm_override = (
                                overrides.get("instance_types")
                                or overrides.get("vm_types")
                                or overrides.get("vmTypes")
                            )
                            if scheduler_is_default:
                                tmpl["instance_types"] = vm_override
                                tmpl.pop("vm_type", None)
                                tmpl.pop("vmTypes", None)
                                tmpl.pop("vm_types", None)
                            else:
                                tmpl["vmTypes"] = vm_override
                                tmpl.pop("vm_type", None)
                                tmpl.pop("vm_types", None)
                                tmpl.pop("instance_types", None)

                        # Apply other known override keys directly
                        for key in [
                            "vmType",
                            "abisInstanceRequirements",
                            "abis_instance_requirements",
                            "instance_types",
                            "instanceTypes",
                        ]:
                            if key in overrides:
                                tmpl[key] = overrides[key]

                # Write populated template (always use standard output name)
                output_file = test_dir / output_name
                with open(output_file, "w") as f:
                    json.dump(populated_template, f, indent=2)

                # Show the actual template file used
                actual_template_name = (
                    template_name
                    if template_name.endswith(".base.json")
                    else f"{template_name}.base.json"
                )
                log.info(
                    f"Selected base template: {actual_template_name} for scheduler: {scheduler_type}"
                )
                print(f"Generated {output_file} from {actual_template_name}")

            except Exception as e:
                print(f"Error generating {base_name}: {e}")
                raise

    def cleanup_test_templates(self, test_name: str) -> None:
        """Clean up generated templates for a test.

        Args:
            test_name: Name of the test
        """
        test_dir = self.run_templates_dir / test_name
        if test_dir.exists():
            import shutil

            shutil.rmtree(test_dir)
            print(f"Cleaned up {test_dir}")


def main():
    """Main function for testing the template processor."""
    processor = TemplateProcessor()

    # Generate templates for a test
    test_name = "test_get_available_templates"
    print(f"Generating templates for {test_name}...")

    try:
        processor.generate_test_templates(test_name)
        print(f"Successfully generated templates for {test_name}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
