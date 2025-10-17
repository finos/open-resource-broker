#!/usr/bin/env python3
"""Template processor for onaws tests - generates populated templates from base templates and config."""

import json
from pathlib import Path
from typing import Any, Dict


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

        # Generate each required file
        template_files = [
            ("awsprov_templates", "awsprov_templates.json"),
            ("config", "config.json"),
            ("default_config", "default_config.json"),
        ]

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
                    # Use default base template
                    template_name = base_name

                # Load base template
                base_template_data = self.load_base_template(template_name)

                # Replace placeholders
                populated_template = self.replace_placeholders(base_template_data, config)

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
