"""Template Defaults Service - Hierarchical template default resolution with domain extensions."""

from typing import Any, Dict, Optional

from domain.base.dependency_injection import injectable
from domain.base.ports.configuration_port import ConfigurationPort
from domain.base.ports.logging_port import LoggingPort
from domain.template.aggregate import Template
from domain.template.extensions import TemplateExtensionRegistry
from domain.template.factory import TemplateFactoryPort
from domain.template.ports.template_defaults_port import TemplateDefaultsPort


@injectable
class TemplateDefaultsService(TemplateDefaultsPort):
    """
    Service for resolving hierarchical template defaults.

    Implements the following precedence hierarchy:
    1. Template file values (highest priority)
    2. Provider instance defaults
    3. Provider type defaults
    4. Global template defaults (lowest priority)

    This service ensures that templates get appropriate defaults applied
    while respecting the configuration hierarchy.
    """

    def __init__(
        self,
        config_manager: ConfigurationPort,
        logger: LoggingPort,
        template_factory: Optional[TemplateFactoryPort] = None,
        extension_registry: Optional[TemplateExtensionRegistry] = None,
    ):
        """
        Initialize the template defaults service.

        Args:
            config_manager: Configuration port for accessing defaults
            logger: Logger for debugging and monitoring
            template_factory: Factory for creating domain templates
            extension_registry: Registry for provider extensions
        """
        self.config_manager = config_manager
        self.logger = logger
        self.template_factory = template_factory
        self.extension_registry = extension_registry or TemplateExtensionRegistry

    def resolve_template_defaults(
        self,
        template_dict: Dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Apply hierarchical defaults to a template dictionary.

        Args:
            template_dict: Raw template data from file
            provider_instance_name: Name of provider instance for context

        Returns:
            Template dictionary with defaults applied
        """
        self.logger.debug(
            f"Resolving defaults for template { template_dict.get( 'template_id', 'unknown')}"
        )

        # Start with empty defaults
        resolved_defaults = {}

        # 1. Apply global template defaults (lowest priority)
        global_defaults = self._get_global_template_defaults()
        resolved_defaults.update(global_defaults)
        self.logger.debug(f"Applied {len(global_defaults)} global defaults")

        # 2. Apply provider type defaults
        if provider_instance_name:
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                provider_type_defaults = self._get_provider_type_defaults(provider_type)
                resolved_defaults.update(provider_type_defaults)
                self.logger.debug(
                    f"Applied { len(provider_type_defaults)} provider type defaults for {provider_type}"
                )

                # 3. Apply provider instance defaults
                provider_instance_defaults = self._get_provider_instance_defaults(
                    provider_instance_name
                )
                resolved_defaults.update(provider_instance_defaults)
                self.logger.debug(
                    f"Applied { len(provider_instance_defaults)} provider instance defaults for {provider_instance_name}"
                )

        # 4. Apply template values (highest priority - only for missing fields)
        result = {**resolved_defaults}
        for key, value in template_dict.items():
            if value is not None:  # Don't override with None values
                result[key] = value

        self.logger.debug(f"Final template has {len(result)} fields after default resolution")
        return result

    def resolve_provider_api_default(
        self,
        template_dict: Dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> str:
        """
        Resolve provider_api default using hierarchical configuration.

        This method specifically handles the provider_api field which was
        previously hardcoded to 'aws' in the scheduler strategy.

        Args:
            template_dict: Template data
            provider_instance_name: Provider instance name for context

        Returns:
            Resolved provider_api value
        """
        # 1. Check template file first (highest priority)
        provider_api = template_dict.get("providerApi") or template_dict.get("provider_api")
        if provider_api:
            self.logger.debug(f"Using provider_api from template: {provider_api}")
            return provider_api

        # 2. Check provider instance defaults
        if provider_instance_name:
            instance_defaults = self._get_provider_instance_defaults(provider_instance_name)
            if instance_defaults.get("provider_api"):
                provider_api = instance_defaults["provider_api"]
                self.logger.debug(f"Using provider_api from instance defaults: {provider_api}")
                return provider_api

            # 3. Check provider type defaults
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                type_defaults = self._get_provider_type_defaults(provider_type)
                if type_defaults.get("provider_api"):
                    provider_api = type_defaults["provider_api"]
                    self.logger.debug(f"Using provider_api from type defaults: {provider_api}")
                    return provider_api

        # 4. Check global template defaults
        global_defaults = self._get_global_template_defaults()
        if global_defaults.get("provider_api"):
            provider_api = global_defaults["provider_api"]
            self.logger.debug(f"Using provider_api from global defaults: {provider_api}")
            return provider_api

        # 5. Final fallback (should be configured, not hardcoded)
        self.logger.warning("No provider_api configured anywhere, using fallback 'EC2Fleet'")
        return "EC2Fleet"

    def get_effective_template_defaults(
        self, provider_instance_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get the effective template defaults for a provider instance.

        This method returns the merged defaults without applying them to a specific template.
        Useful for validation and debugging.

        Args:
            provider_instance_name: Provider instance name

        Returns:
            Merged template defaults
        """
        defaults = {}

        # Global defaults
        defaults.update(self._get_global_template_defaults())

        # Provider type defaults
        if provider_instance_name:
            provider_type = self._get_provider_type(provider_instance_name)
            if provider_type:
                defaults.update(self._get_provider_type_defaults(provider_type))

                # Provider instance defaults
                defaults.update(self._get_provider_instance_defaults(provider_instance_name))

        return defaults

    def _get_global_template_defaults(self) -> Dict[str, Any]:
        """Get global template defaults from configuration."""
        try:
            template_config = self.config_manager.get_template_config()
            if hasattr(template_config, "model_dump"):
                config_dict = template_config.model_dump()
            else:
                config_dict = template_config

            # Extract only default-like fields from cleaned schema
            global_defaults = {}
            default_fields = [
                "max_number",
                "default_price_type",
                "default_provider_api",
            ]

            for field in default_fields:
                if field in config_dict and config_dict[field] is not None:
                    # Remove 'default_' prefix for clean field names
                    clean_field = (
                        field.replace("default_", "") if field.startswith("default_") else field
                    )
                    global_defaults[clean_field] = config_dict[field]

            return global_defaults

        except Exception as e:
            self.logger.warning(f"Could not get global template defaults: {e}")
            return {}

    def _get_provider_type_defaults(self, provider_type: str) -> Dict[str, Any]:
        """Get template defaults for a provider type."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "provider_defaults"):
                provider_defaults = provider_config.provider_defaults.get(provider_type)
                if provider_defaults and hasattr(provider_defaults, "template_defaults"):
                    return provider_defaults.template_defaults or {}

            return {}

        except Exception as e:
            self.logger.warning(f"Could not get provider type defaults for {provider_type}: {e}")
            return {}

    def _get_provider_instance_defaults(self, provider_instance_name: str) -> Dict[str, Any]:
        """Get template defaults for a specific provider instance."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:
                    if provider.name == provider_instance_name:
                        return provider.template_defaults or {}

            return {}

        except Exception as e:
            self.logger.warning(
                f"Could not get provider instance defaults for {provider_instance_name}: {e}"
            )
            return {}

    def _get_provider_type(self, provider_instance_name: str) -> Optional[str]:
        """Get provider type from provider instance name."""
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:
                    if provider.name == provider_instance_name:
                        return provider.type

            # Fallback: extract from name (e.g., 'aws-primary' -> 'aws')
            if "-" in provider_instance_name:
                return provider_instance_name.split("-")[0]

            return provider_instance_name

        except Exception as e:
            self.logger.warning(
                f"Could not determine provider type for {provider_instance_name}: {e}"
            )
            return None

    def validate_template_defaults(
        self, provider_instance_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Validate template defaults configuration.

        Args:
            provider_instance_name: Provider instance to validate

        Returns:
            Validation results with any issues found
        """
        validation_result = {
            "is_valid": True,
            "warnings": [],
            "errors": [],
            "provider_instance": provider_instance_name,
        }

        try:
            # Check if defaults are correctly configured
            effective_defaults = self.get_effective_template_defaults(provider_instance_name)

            # Validate essential fields have defaults
            essential_fields = ["provider_api", "price_type", "max_number"]
            for field in essential_fields:
                if field not in effective_defaults:
                    validation_result["warnings"].append(
                        f"No default configured for essential field: {field}"
                    )

            # Check for AWS-specific defaults in global config
            global_defaults = self._get_global_template_defaults()
            aws_specific_patterns = ["ami-", "sg-", "subnet-", "vpc-"]
            for key, value in global_defaults.items():
                if isinstance(value, str):
                    for pattern in aws_specific_patterns:
                        if pattern in value:
                            validation_result["warnings"].append(
                                f"AWS-specific default '{key}: {value}' found in global config. "
                                f"Consider moving to provider-specific defaults."
                            )

            self.logger.info(
                f"Template defaults validation completed for { provider_instance_name or 'global'}"
            )

        except Exception as e:
            validation_result["is_valid"] = False
            validation_result["errors"].append(f"Validation failed: {str(e)}")
            self.logger.error(f"Template defaults validation failed: {e}")

        return validation_result

    def resolve_template_with_extensions(
        self,
        template_dict: Dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> Template:
        """
        Resolve template with provider extensions using domain factory.

        This method integrates hierarchical defaults
        with domain extensions and creates appropriate domain template objects.

        Args:
            template_dict: Raw template data from file
            provider_instance_name: Provider instance name for context

        Returns:
            Domain template object with extensions applied
        """
        self.logger.debug(
            f"Resolving template with extensions: { template_dict.get( 'template_id', 'unknown')}"
        )

        # 1. Apply hierarchical defaults (existing logic)
        resolved_dict = self.resolve_template_defaults(template_dict, provider_instance_name)

        # 2. Determine provider type
        provider_type = (
            self._get_provider_type(provider_instance_name) if provider_instance_name else None
        )

        # 3. Apply provider extension defaults
        if provider_type:
            extension_defaults = self._get_extension_defaults(provider_type, provider_instance_name)
            # Extension defaults have lower priority than hierarchical defaults
            resolved_dict = {**extension_defaults, **resolved_dict}
            self.logger.debug(
                f"Applied { len(extension_defaults)} extension defaults for {provider_type}"
            )

        # 4. Create appropriate template type via factory
        if self.template_factory:
            try:
                template = self.template_factory.create_template(resolved_dict, provider_type)
                self.logger.debug(f"Created {type(template).__name__} via factory")
                return template
            except Exception as e:
                self.logger.error(f"Failed to create template via factory: {e}")
                # Fall back to core template

        # Fallback: create core template directly
        try:
            template = Template(**resolved_dict)
            self.logger.debug("Created core Template as fallback")
            return template
        except Exception as e:
            self.logger.error(f"Failed to create core template: {e}")
            raise

    def _get_extension_defaults(
        self, provider_type: str, provider_instance_name: Optional[str]
    ) -> Dict[str, Any]:
        """
        Get provider extension defaults with hierarchy.

        Args:
            provider_type: Provider type (e.g., 'aws', 'provider1')
            provider_instance_name: Provider instance name for overrides

        Returns:
            Dictionary of extension defaults
        """
        extension_defaults = {}

        try:
            # 1. Get provider type extension defaults
            if self.extension_registry.has_extension(provider_type):
                type_extension_defaults = self.extension_registry.get_extension_defaults(
                    provider_type
                )
                extension_defaults.update(type_extension_defaults)
                self.logger.debug(f"Applied {len(type_extension_defaults)} type extension defaults")

            # 2. Get provider instance extension overrides
            if provider_instance_name:
                instance_extension_defaults = self._get_provider_instance_extension_defaults(
                    provider_instance_name, provider_type
                )
                extension_defaults.update(instance_extension_defaults)
                self.logger.debug(
                    f"Applied { len(instance_extension_defaults)} instance extension defaults"
                )

        except Exception as e:
            self.logger.warning(f"Could not load extension defaults for {provider_type}: {e}")

        return extension_defaults

    def _get_provider_instance_extension_defaults(
        self, provider_instance_name: str, provider_type: str
    ) -> Dict[str, Any]:
        """
        Get extension defaults for a specific provider instance.

        Args:
            provider_instance_name: Provider instance name
            provider_type: Provider type for extension lookup

        Returns:
            Dictionary of instance-specific extension defaults
        """
        try:
            provider_config = self.config_manager.get_provider_config()

            if hasattr(provider_config, "providers"):
                for provider in provider_config.providers:
                    if (
                        provider.name == provider_instance_name
                        and hasattr(provider, "extensions")
                        and provider.extensions
                    ):
                        # Use extension registry to process the extensions
                        if self.extension_registry.has_extension(provider_type):
                            return self.extension_registry.get_extension_defaults(
                                provider_type, provider.extensions
                            )
                        else:
                            # Return raw extensions if no registry entry
                            return provider.extensions

            return {}

        except Exception as e:
            self.logger.warning(
                f"Could not get instance extension defaults for {provider_instance_name}: {e}"
            )
            return {}

    def get_effective_template_with_extensions(
        self,
        template_dict: Dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get effective template configuration with all defaults and extensions applied.

        This method is useful for debugging and validation - it shows the final
        resolved configuration without creating a domain object.

        Args:
            template_dict: Raw template data
            provider_instance_name: Provider instance name

        Returns:
            Dictionary with all defaults and extensions applied
        """
        # Apply hierarchical defaults
        resolved_dict = self.resolve_template_defaults(template_dict, provider_instance_name)

        # Apply extension defaults
        provider_type = (
            self._get_provider_type(provider_instance_name) if provider_instance_name else None
        )
        if provider_type:
            extension_defaults = self._get_extension_defaults(provider_type, provider_instance_name)
            resolved_dict = {**extension_defaults, **resolved_dict}

        return resolved_dict

    def validate_template_with_extensions(
        self,
        template_dict: Dict[str, Any],
        provider_instance_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate template configuration with extensions.

        Args:
            template_dict: Template data to validate
            provider_instance_name: Provider instance name

        Returns:
            Validation results including extension validation
        """
        validation_result = self.validate_template_defaults(provider_instance_name)

        try:
            # Try to create template with extensions to validate
            template = self.resolve_template_with_extensions(template_dict, provider_instance_name)

            # Additional validation for domain template
            if hasattr(template, "validate"):
                try:
                    template.validate()
                    validation_result["domain_validation"] = "passed"
                except Exception as e:
                    validation_result["warnings"].append(f"Domain validation failed: {e}")
                    validation_result["domain_validation"] = "failed"

            # Check for provider-specific validation
            provider_type = (
                self._get_provider_type(provider_instance_name) if provider_instance_name else None
            )
            if provider_type and hasattr(template, f"validate_{provider_type}"):
                try:
                    getattr(template, f"validate_{provider_type}")()
                    validation_result[f"{provider_type}_validation"] = "passed"
                except Exception as e:
                    validation_result["warnings"].append(f"{provider_type} validation failed: {e}")
                    validation_result[f"{provider_type}_validation"] = "failed"

        except Exception as e:
            validation_result["errors"].append(f"Template creation with extensions failed: {e}")
            validation_result["is_valid"] = False

        return validation_result
