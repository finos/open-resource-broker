"""Template Configuration Manager

Orchestrates template services while delegating to scheduler strategies.
Provides focused orchestration logic for template operations.

Architecture Principles:
- Delegates file operations to scheduler strategies
- Uses dedicated services for caching and storage
- Maintains clean separation of concerns
- Preserves existing public interface
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from config.managers.configuration_manager import ConfigurationManager
from domain.base.dependency_injection import injectable
from domain.base.exceptions import DomainException, EntityNotFoundError, ValidationError
from domain.base.ports.event_publisher_port import EventPublisherPort
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort

from .dtos import TemplateDTO
from .services.template_storage_service import TemplateStorageService
from .template_cache_service import TemplateCacheService, create_template_cache_service

if TYPE_CHECKING:
    from application.services.provider_registry_service import ProviderRegistryService
    from application.services.template_defaults_service import TemplateDefaultsService
    from domain.template.factory import TemplateFactoryPort


class TemplateConfigurationError(DomainException):
    """Base exception for template configuration errors."""


class TemplateNotFoundError(EntityNotFoundError):
    """Exception raised when a template is not found."""


class TemplateValidationError(ValidationError):
    """Exception raised when template validation fails."""


@dataclass
class TemplateFileMetadata:
    """Metadata for template files."""

    path: Path
    provider: str
    file_type: str
    priority: int
    last_modified: datetime


@injectable
class TemplateConfigurationManager:
    """
    Template Configuration Manager.

    This class orchestrates template operations by delegating to:
    - Scheduler strategies for file operations and field mapping
    - Cache service for performance optimization
    - Persistence service for CRUD operations
    - Template defaults service for hierarchical configuration

    Responsibilities:
    - Orchestrate template loading via scheduler strategy
    - Coordinate caching and storage services
    - Provide integrated template access interface
    - Handle template validation and events
    """

    def __init__(
        self,
        config_manager: ConfigurationManager,
        scheduler_strategy: SchedulerPort,
        logger: LoggingPort,
        cache_service: Optional[TemplateCacheService] = None,
        storage_service: Optional[TemplateStorageService] = None,
        event_publisher: Optional[EventPublisherPort] = None,
        template_defaults_service: Optional["TemplateDefaultsService"] = None,
        provider_registry_service: Optional["ProviderRegistryService"] = None,
        template_factory: Optional["TemplateFactoryPort"] = None,
    ) -> None:
        """
        Initialize the template configuration manager.

        Args:
            config_manager: Configuration manager for paths and settings
            scheduler_strategy: Strategy for file operations and field mapping
            logger: Logger for operations and debugging
            cache_service: Optional cache service (creates default if None)
            storage_service: Optional storage service (creates default if None)
            event_publisher: Optional event publisher for domain events
            template_defaults_service: Optional service for template defaults
            provider_registry_service: Optional provider registry service for provider operations
            template_factory: Optional factory for creating provider-specific templates
        """
        self.config_manager = config_manager
        self.scheduler_strategy = scheduler_strategy
        self.logger = logger
        self.event_publisher = event_publisher
        self.template_defaults_service = template_defaults_service
        self.provider_registry_service = provider_registry_service
        if template_factory is None:
            from domain.template.factory import TemplateFactory

            template_factory = TemplateFactory()
        self.template_factory = template_factory

        # Initialize services
        self.cache_service = cache_service or create_template_cache_service("ttl", logger)
        self.storage_service = storage_service or TemplateStorageService(
            scheduler_strategy, logger, event_publisher
        )

        # Provider selection cache for batch operations
        self._provider_selection_cache = {}

        self.logger.info("Template configuration manager initialized")

    async def load_templates(
        self, force_refresh: bool = False, provider_override: Optional[str] = None
    ) -> list[TemplateDTO]:
        """
        Load all templates using cache service and scheduler strategy.

        Args:
            force_refresh: Force reload even if cached

        Returns:
            List of TemplateDTO objects
        """
        if force_refresh:
            self.cache_service.invalidate()

        return await self.cache_service.get_or_load(
            lambda: self._load_templates_from_scheduler(provider_override)  # type: ignore[return-value]
        )

    async def _load_templates_from_scheduler(
        self, provider_override: Optional[str] = None
    ) -> list[TemplateDTO]:
        """Load templates using scheduler strategy with batch AMI resolution."""
        try:
            # Get template file paths from scheduler strategy
            template_paths = self.scheduler_strategy.get_template_paths()  # type: ignore[attr-defined]
            if not template_paths:
                self.logger.warning("No template paths available from scheduler strategy")
                return []

            all_template_dicts = []

            # Load templates from each path
            for template_path in template_paths:
                try:
                    # Use scheduler strategy to load and parse templates
                    template_dicts = self.scheduler_strategy.load_templates_from_path(  # type: ignore[attr-defined]
                        template_path, provider_override
                    )
                    all_template_dicts.extend(template_dicts)

                except Exception as e:
                    self.logger.error("Failed to load templates from %s: %s", template_path, e)
                    continue

            # Apply batch image resolution before converting to DTOs
            resolved_template_dicts = await self._batch_resolve_images(all_template_dicts)

            # Apply deduplication to remove duplicate templates
            resolved_template_dicts = self._deduplicate_template_dicts(resolved_template_dicts)

            # Convert to DTOs with defaults applied
            all_templates = []
            for template_dict in resolved_template_dicts:
                try:
                    template_dto = self._convert_dict_to_template_dto(template_dict)
                    all_templates.append(template_dto)
                except Exception as e:
                    self.logger.warning("Failed to convert template dict to DTO: %s", e)
                    continue

            self.logger.debug("Loaded %s templates from scheduler strategy", len(all_templates))
            return all_templates

        except Exception as e:
            self.logger.error("Failed to load templates from scheduler: %s", e)
            return []

    def _convert_dict_to_template_dto(
        self, template_dict: dict[str, Any], file_metadata: Optional[TemplateFileMetadata] = None
    ) -> TemplateDTO:
        """Convert template dictionary to TemplateDTO with defaults applied."""
        # Extract template ID (scheduler strategy should have normalized this)
        template_id = template_dict.get("template_id", template_dict.get("templateId", ""))

        if not template_id:
            raise ValueError("Template missing required template_id field")

        # AMI resolution is already done in _batch_resolve_images, no need to do it again
        # Template defaults are applied by the scheduler strategy before this point.

        # Create domain Template object from dict with defaults
        # Use factory so provider-specific subclasses (e.g. AWSTemplate) are constructed,
        # preserving fields that base Template silently drops (extra="ignore").
        template_domain = self.template_factory.create_template(template_dict)

        # Convert domain → DTO using existing method
        return TemplateDTO.from_domain(template_domain)

    def _determine_provider_instance(self, template_dict: dict[str, Any]) -> Optional[str]:
        """Determine which provider instance this template belongs to."""
        # 1. Check if template specifies provider instance
        if "provider_name" in template_dict:
            return template_dict["provider_name"]

        # 2. Check cache for templates with same constraints
        cache_key = (template_dict.get("provider_type"), template_dict.get("provider_api"))

        if cache_key in self._provider_selection_cache:
            return self._provider_selection_cache[cache_key]

        # 3. Use active provider from configuration (expensive operation)
        try:
            from providers.registry import get_provider_registry

            get_provider_registry()
            from application.services.provider_registry_service import ProviderRegistryService
            from infrastructure.di.container import get_container

            container = get_container()
            provider_service = container.get(ProviderRegistryService)
            selection_result = provider_service.select_active_provider()
            result = selection_result.provider_instance

            # Cache the result
            self._provider_selection_cache[cache_key] = result
            return result
        except Exception as e:
            self.logger.debug("Could not determine provider instance via registry: %s", e)

            # Fallback: try direct provider config access
            try:
                provider_config = self.config_manager.get_provider_config()
                if provider_config:
                    active_providers = provider_config.get_active_providers()
                    if active_providers:
                        result = active_providers[0].name
                        # Cache the fallback result
                        self._provider_selection_cache[cache_key] = result
                        return result
            except Exception as e2:
                self.logger.debug("Could not determine provider instance via direct access: %s", e2)

        # 4. Fallback to default
        result = "aws"
        self._provider_selection_cache[cache_key] = result
        return result

    def _clear_provider_selection_cache(self):
        """Clear provider selection cache between batches."""
        self._provider_selection_cache.clear()

    async def _batch_resolve_images(
        self, template_dicts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Batch resolve image IDs from specifications using provider registry."""
        try:
            # Clear cache at start of each batch
            self._clear_provider_selection_cache()

            if not self._is_image_resolution_enabled():
                return template_dicts

            # Group templates by provider instance
            templates_by_provider = self._group_templates_by_provider(template_dicts)

            resolved_templates = []
            for provider_instance, templates in templates_by_provider.items():
                # Collect image specifications for this provider
                image_specifications = self._extract_image_specifications(templates)

                if image_specifications:
                    # Use provider registry to execute image resolution
                    resolved_images = await self._resolve_images_via_provider(
                        provider_instance, image_specifications
                    )
                    # Apply resolved images to templates
                    templates = self._apply_resolved_images(templates, resolved_images)

                resolved_templates.extend(templates)

            return resolved_templates

        except Exception as e:
            self.logger.error("Batch image resolution failed: %s", e)
            return template_dicts
        finally:
            # Clear cache at end of batch (cleanup)
            self._clear_provider_selection_cache()

    def _is_image_resolution_enabled(self) -> bool:
        """Check if image resolution is enabled."""
        try:
            provider_config = self.config_manager.get_provider_config()
            if provider_config is not None and (
                hasattr(provider_config, "provider_defaults")
                and "aws" in provider_config.provider_defaults
            ):
                aws_defaults = provider_config.provider_defaults["aws"]
                if hasattr(aws_defaults, "extensions"):
                    return getattr(aws_defaults.extensions, "ami_resolution_enabled", True)
            return True
        except Exception:
            return True

    def _group_templates_by_provider(
        self, template_dicts: list[dict[str, Any]]
    ) -> dict[str, list[dict[str, Any]]]:
        """Group templates by provider instance."""
        groups = {}
        for template_dict in template_dicts:
            provider_instance = self._determine_provider_instance(template_dict)
            if provider_instance not in groups:
                groups[provider_instance] = []
            groups[provider_instance].append(template_dict)
        return groups

    def _extract_image_specifications(self, templates: list[dict[str, Any]]) -> list[str]:
        """Extract unique image specifications from templates."""
        specifications = set()
        for template_dict in templates:
            image_id = template_dict.get("image_id") or template_dict.get("imageId")
            if image_id and image_id.startswith("/aws/service/"):
                specifications.add(image_id)
        return list(specifications)

    async def _resolve_images_via_provider(
        self, provider_instance: str, image_specifications: list[str]
    ) -> dict[str, str]:
        """Resolve image specifications via provider registry service."""
        if not self.provider_registry_service:
            self.logger.debug("Provider registry service not available, skipping image resolution")
            return {}

        try:
            from providers.base.strategy import ProviderOperation, ProviderOperationType

            operation = ProviderOperation(
                operation_type=ProviderOperationType.RESOLVE_IMAGE,
                parameters={"image_specifications": image_specifications},
            )

            result = await self.provider_registry_service.execute_operation(
                provider_instance, operation
            )

            if result.success and result.data:
                return result.data.get("resolved_images", {})
            else:
                self.logger.warning(
                    "Image resolution failed for provider %s: %s",
                    provider_instance,
                    result.error_message,
                )
                return {}

        except Exception as e:
            self.logger.warning("Image resolution failed for provider %s: %s", provider_instance, e)
            return {}

    def _apply_resolved_images(
        self, templates: list[dict[str, Any]], resolved_images: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Apply resolved images to templates."""
        resolved_templates = []
        for template_dict in templates:
            resolved_template = template_dict.copy()

            image_id = resolved_template.get("image_id") or resolved_template.get("imageId")
            if image_id and image_id in resolved_images:
                resolved_ami = resolved_images[image_id]
                resolved_template["image_id"] = resolved_ami
                if "imageId" in resolved_template:
                    resolved_template["imageId"] = resolved_ami

            resolved_templates.append(resolved_template)
        return resolved_templates

    async def get_template_by_id(self, template_id: str) -> Optional[TemplateDTO]:
        """
        Get a specific template by ID.

        Args:
            template_id: Template identifier

        Returns:
            TemplateDTO if found, None otherwise

        Raises:
            TemplateConfigurationError: If template loading fails
            ValidationError: If template_id is invalid
        """
        try:
            # Validate input
            if not template_id or not isinstance(template_id, str):
                raise TemplateValidationError("Template ID must be a non-empty string")

            # Load templates (uses cache)
            templates = await self.load_templates()

            # Find template by ID
            for template in templates:
                if template.template_id == template_id:
                    self.logger.debug("Retrieved template %s", template_id)
                    return template

            self.logger.debug("Template %s not found", template_id)
            return None

        except TemplateValidationError:
            raise
        except Exception as e:
            self.logger.error("Failed to get template %s: %s", template_id, e)
            raise TemplateConfigurationError(f"Failed to retrieve template {template_id}: {e!s}")

    async def get_templates_by_provider(self, provider_api: str) -> list[TemplateDTO]:
        """
        Get templates filtered by provider API.

        Args:
            provider_api: Provider API identifier

        Returns:
            List of templates for the specified provider
        """
        templates = await self.load_templates()
        filtered_templates = [
            t for t in templates if getattr(t, "provider_api", None) == provider_api
        ]

        self.logger.debug(
            "Found %s templates for provider %s", len(filtered_templates), provider_api
        )
        return filtered_templates

    async def get_all_templates(self) -> list[TemplateDTO]:
        """Get all templates (alias for load_templates for compatibility)."""
        return await self.load_templates()

    def get_all_templates_sync(self) -> list[TemplateDTO]:
        """Get all templates synchronously for adapter compatibility."""
        try:
            # Try to get existing event loop
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, we can't use run_until_complete
                # Fall back to direct template loading via scheduler strategy
                return self._load_templates_sync()
            else:
                return loop.run_until_complete(self.get_all_templates())
        except RuntimeError:
            # No event loop, create new one
            return asyncio.run(self.get_all_templates())

    def _load_templates_sync(self) -> list[TemplateDTO]:
        """Load templates synchronously as fallback when event loop is running."""
        try:
            template_paths = self.scheduler_strategy.get_template_paths()  # type: ignore[attr-defined]
            if not template_paths:
                return []
            all_template_dicts: list[dict[str, Any]] = []
            for template_path in template_paths:
                try:
                    template_dicts = self.scheduler_strategy.load_templates_from_path(  # type: ignore[attr-defined]
                        template_path, None
                    )
                    all_template_dicts.extend(template_dicts)
                except Exception as e:
                    self.logger.error("Failed to load templates from %s: %s", template_path, e)
            all_templates = []
            for template_dict in all_template_dicts:
                try:
                    template_dto = self._convert_dict_to_template_dto(template_dict)
                    all_templates.append(template_dto)
                except Exception as e:
                    self.logger.warning("Failed to convert template dict to DTO: %s", e)
            return all_templates
        except Exception as e:
            self.logger.error("Failed to load templates synchronously: %s", e)
            return []

    async def save_template(self, template: TemplateDTO) -> None:
        """
        Save template using storage service.

        Args:
            template: Template to save
        """
        try:
            await self.storage_service.save_template(template)

            # Invalidate cache to ensure fresh data on next load
            self.cache_service.invalidate()

            self.logger.info("Saved template %s", template.template_id)

        except Exception as e:
            self.logger.error("Failed to save template %s: %s", template.template_id, e)
            raise

    async def delete_template(self, template_id: str) -> None:
        """
        Delete template using storage service.

        Args:
            template_id: Template identifier to delete
        """
        try:
            await self.storage_service.delete_template(template_id)

            # Invalidate cache to ensure fresh data on next load
            self.cache_service.invalidate()

            self.logger.info("Deleted template %s", template_id)

        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", template_id, e)
            raise

    def get_template(self, template_id: str) -> Optional[TemplateDTO]:
        """Get template by ID synchronously for compatibility."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Loop already running — use sync fallback to avoid nested asyncio.run()
                templates = self._load_templates_sync()
                return next((t for t in templates if t.template_id == template_id), None)
            else:
                return loop.run_until_complete(self.get_template_by_id(template_id))
        except RuntimeError:
            return asyncio.run(self.get_template_by_id(template_id))

    async def validate_template(
        self, template: TemplateDTO, provider_instance: Optional[str] = None
    ) -> dict[str, Any]:
        """
        Validate template configuration.

        Args:
            template: Template to validate
            provider_instance: Optional provider instance for capability validation

        Returns:
            Dictionary with validation results
        """
        validation_result = {
            "template_id": template.template_id,
            "is_valid": True,
            "errors": [],
            "warnings": [],
            "supported_features": [],
            "validation_time": datetime.now(),
        }

        try:
            # Basic validation
            self._validate_basic_template_structure(template, validation_result)

            # Provider capability validation (if available via registry)
            if provider_instance:
                await self._validate_with_provider_registry(
                    template, provider_instance, validation_result
                )

            self.logger.info(
                "Template validation completed for %s: %s",
                template.template_id,
                "valid" if validation_result["is_valid"] else "invalid",
            )

            return validation_result

        except Exception as e:
            self.logger.error("Template validation failed for %s: %s", template.template_id, e)
            validation_result["is_valid"] = False
            validation_result["errors"].append(f"Validation error: {e!s}")
            return validation_result

    def _validate_basic_template_structure(
        self, template: TemplateDTO, result: dict[str, Any]
    ) -> None:
        """Validate basic template structure and required fields."""
        # Check required fields
        if not template.template_id:
            result["is_valid"] = False
            result["errors"].append("Template ID is required")

        if not template.provider_api:
            result["is_valid"] = False
            result["errors"].append("Provider API is required")

        # Validate essential configuration fields directly from DTO
        if not template.image_id:
            result["errors"].append("Image ID is required in configuration")
            result["is_valid"] = False

        if template.max_instances <= 0:
            result["warnings"].append("Max instances should be greater than 0")
        elif template.max_instances > 1000:
            result["warnings"].append(
                "Max instances is very high (>1000), consider if this is intentional"
            )

        self.logger.debug("Basic validation completed for template %s", template.template_id)

    async def _validate_with_provider_registry(
        self, template: TemplateDTO, provider_instance: str, result: dict[str, Any]
    ) -> None:
        """Validate template against provider capabilities via registry."""
        try:
            # Convert TemplateDTO to Template domain object for validation
            from domain.template.template_aggregate import Template

            # Create minimal Template object for validation
            domain_template = Template(
                template_id=template.template_id,
                name=template.name,
                provider_api=template.provider_api,
            )

            # Use provider registry for validation
            from providers.registry import get_provider_registry

            registry = get_provider_registry()
            if not hasattr(registry, "validate_template_requirements"):
                result["warnings"].append("Provider registry does not support template validation")
                return
            capability_result = registry.validate_template_requirements(  # type: ignore[attr-defined]
                domain_template, provider_instance, "strict"
            )

            # Merge capability validation results
            if not capability_result.is_valid:
                result["is_valid"] = False
                result["errors"].extend(capability_result.errors)

            result["warnings"].extend(capability_result.warnings)
            result["supported_features"].extend(capability_result.supported_features)

            self.logger.debug(
                "Provider capability validation completed for template %s",
                template.template_id,
            )

        except Exception as e:
            self.logger.warning(
                "Provider capability validation failed for template %s: %s",
                template.template_id,
                e,
            )
            result["warnings"].append(f"Could not validate provider capabilities: {e!s}")

    def clear_cache(self) -> None:
        """Clear template cache."""
        self.cache_service.invalidate()
        self.logger.info("Cleared template cache")

    def _deduplicate_template_dicts(
        self, template_dicts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Deduplicate template dictionaries by template_id."""
        if not template_dicts:
            return []

        seen_ids = set()
        unique_templates = []

        for template_dict in template_dicts:
            template_id = template_dict.get("template_id", template_dict.get("templateId", ""))
            if template_id and template_id not in seen_ids:
                seen_ids.add(template_id)
                unique_templates.append(template_dict)

        self.logger.debug(
            "Deduplicated %d templates to %d unique", len(template_dicts), len(unique_templates)
        )
        return unique_templates


# Factory function for dependency injection
def create_template_configuration_manager(
    config_manager: ConfigurationManager,
    scheduler_strategy: SchedulerPort,
    logger: LoggingPort,
    template_factory: Optional["TemplateFactoryPort"] = None,
) -> TemplateConfigurationManager:
    """
    Create TemplateConfigurationManager.

    This function provides a clean way to create the manager with
    dependency injection.
    """
    return TemplateConfigurationManager(
        config_manager=config_manager,
        scheduler_strategy=scheduler_strategy,
        logger=logger,
        template_factory=template_factory,
    )
