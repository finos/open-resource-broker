"""JSON template repository and strategy implementation."""

import json
import os
from typing import Any, Dict, List, Optional

from config.managers.configuration_manager import ConfigurationManager
from domain.base.exceptions import ConfigurationError
from domain.template.aggregate import Template
from domain.template.repository import TemplateRepository
from infrastructure.logging.logger import get_logger
from infrastructure.patterns.singleton_registry import SingletonRegistry
from infrastructure.persistence.base import StrategyBasedRepository
from infrastructure.persistence.json.provider_template_strategy import (
    ProviderTemplateStrategy,
)
from infrastructure.persistence.json.strategy import JSONStorageStrategy


class TemplateJSONStorageStrategy(JSONStorageStrategy):
    """JSON storage strategy for templates with legacy format support."""

    def __init__(
        self,
        file_path: str,
        legacy_file_path: Optional[str] = None,
        create_dirs: bool = True,
    ):
        """
        Initialize with both main and legacy file paths.

        Args:
            file_path: Path to the main templates.json file
            legacy_file_path: Optional path to the legacy templates file
            create_dirs: Whether to create directories
        """
        super().__init__(file_path, create_dirs)
        self.legacy_file_path = legacy_file_path
        self.logger = get_logger(__name__)

        # Log which files we found
        if (
            os.path.exists(self.file_path)
            and self.legacy_file_path
            and os.path.exists(self.legacy_file_path)
        ):
            self.logger.info("Found both template files, will merge contents")
            self.logger.debug("Templates file: %s", self.file_path)
            self.logger.debug("Legacy templates file: %s", self.legacy_file_path)
        elif self.legacy_file_path and os.path.exists(self.legacy_file_path):
            self.logger.info("Found only legacy templates file: %s", self.legacy_file_path)
        elif os.path.exists(self.file_path):
            self.logger.info("Found only templates.json: %s", self.file_path)
        else:
            self.logger.warning(
                "No template files found at %s or %s",  self.file_path,  self.legacy_file_path
            )

    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Find entity by ID, checking both main and legacy files.

        Args:
            entity_id: Entity ID

        Returns:
            Entity data if found, None otherwise
        """
        # First check main file
        result = super().find_by_id(entity_id)
        if result:
            return result

        # Then check legacy file if it exists
        if self.legacy_file_path and os.path.exists(self.legacy_file_path):
            try:
                with open(self.legacy_file_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)

                if isinstance(legacy_data, list):
                    for item in legacy_data:
                        if isinstance(item, dict) and item.get("template_id") == entity_id:
                            return item
                elif isinstance(legacy_data, dict) and entity_id in legacy_data:
                    return legacy_data[entity_id]

            except Exception as e:
                self.logger.error(
                    "Error reading legacy templates file %s: %s", self.legacy_file_path, e
                )

        return None

    def find_all(self) -> List[Dict[str, Any]]:
        """
        Find all entities from both main and legacy files.

        Returns:
            List of all entity data
        """
        all_entities = {}

        # Load from legacy file first (lower priority)
        if self.legacy_file_path and os.path.exists(self.legacy_file_path):
            try:
                with open(self.legacy_file_path, "r", encoding="utf-8") as f:
                    legacy_data = json.load(f)

                if isinstance(legacy_data, list):
                    for item in legacy_data:
                        if isinstance(item, dict) and "template_id" in item:
                            all_entities[item["template_id"]] = item
                elif isinstance(legacy_data, dict):
                    for key, value in legacy_data.items():
                        if isinstance(value, dict):
                            value["template_id"] = key
                            all_entities[key] = value

            except Exception as e:
                self.logger.error(
                    "Error reading legacy templates file %s: %s", self.legacy_file_path, e
                )

        # Load from main file (higher priority, will override legacy)
        main_entities = super().find_all()
        for entity in main_entities:
            if isinstance(entity, dict) and "template_id" in entity:
                all_entities[entity["template_id"]] = entity

        return list(all_entities.values())


class TemplateJSONRepository(StrategyBasedRepository, TemplateRepository):
    """JSON-based template repository with provider-specific file support."""

    def __init__(self, config_manager: ConfigurationManager, use_provider_strategy: bool = True):
        """
        Initialize template repository.

        Args:
            config_manager: Configuration manager
            use_provider_strategy: Whether to use provider-specific template loading
        """
        self.config_manager = config_manager
        self.logger = get_logger(__name__)

        # Get template file paths from configuration
        app_config = config_manager.get_app_config()
        templates_file_path = app_config.templates_file_path
        legacy_templates_file_path = getattr(app_config, "legacy_templates_file_path", None)

        # Choose strategy based on configuration
        if use_provider_strategy:
            strategy = ProviderTemplateStrategy(
                base_file_path=templates_file_path,
                config_manager=config_manager,
                create_dirs=True,
            )
            self.logger.info("Using provider-specific template loading strategy")
        else:
            strategy = TemplateJSONStorageStrategy(
                file_path=templates_file_path,
                legacy_file_path=legacy_templates_file_path,
                create_dirs=True,
            )
            self.logger.info("Using legacy template loading strategy")

        super().__init__(strategy)

    def find_by_id(self, template_id: str) -> Optional[Template]:
        """
        Find template by ID.

        Args:
            template_id: Template ID

        Returns:
            Template aggregate if found, None otherwise
        """
        template_data = self.strategy.find_by_id(template_id)
        if template_data:
            try:
                return self._data_to_aggregate(template_data)
            except Exception as e:
                self.logger.error(
                    "Error converting template data to aggregate for '%s': %s", template_id, e
                )
                return None
        return None

    def find_all(self) -> List[Template]:
        """
        Find all templates.

        Returns:
            List of all template aggregates
        """
        templates = []
        template_data_list = self.strategy.find_all()

        for template_data in template_data_list:
            try:
                template = self._data_to_aggregate(template_data)
                templates.append(template)
            except Exception as e:
                template_id = template_data.get("template_id", "unknown")
                self.logger.error(
                    "Error converting template data to aggregate for '%s': %s", template_id, e
                )
                continue

        return templates

    def save(self, template: Template) -> None:
        """
        Save template.

        Args:
            template: Template aggregate to save
        """
        template_data = self._aggregate_to_data(template)
        self.strategy.save(template_data)

    def delete(self, template_id: str) -> bool:
        """
        Delete template by ID.

        Args:
            template_id: Template ID to delete

        Returns:
            True if template was deleted, False if not found
        """
        return self.strategy.delete(template_id)

    def find_by_provider_type(self, provider_type: str) -> List[Template]:
        """
        Find templates by provider type.

        Args:
            provider_type: Provider type to filter by

        Returns:
            List of templates for the specified provider type
        """
        all_templates = self.find_all()
        return [template for template in all_templates if template.provider_type == provider_type]

    def find_by_provider_name(self, provider_name: str) -> List[Template]:
        """
        Find templates by provider name/instance.

        Args:
            provider_name: Provider name/instance to filter by

        Returns:
            List of templates for the specified provider name
        """
        all_templates = self.find_all()
        return [template for template in all_templates if template.provider_name == provider_name]

    def get_template_source_info(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about which file a template was loaded from.

        Args:
            template_id: Template ID

        Returns:
            Dictionary with source information or None if not found
        """
        if hasattr(self.strategy, "get_template_source_info"):
            return self.strategy.get_template_source_info(template_id)
        return None

    def refresh_templates(self) -> None:
        """Refresh template cache and file discovery."""
        if hasattr(self.strategy, "refresh_cache"):
            self.strategy.refresh_cache()
            self.logger.info("Refreshed template cache")

    def _data_to_aggregate(self, data: Dict[str, Any]) -> Template:
        """
        Convert template data to Template aggregate.

        Args:
            data: Template data dictionary

        Returns:
            Template aggregate
        """
        # Extract required fields
        template_id = data.get("template_id")
        if not template_id:
            raise ValueError("Template data must include 'template_id'")

        image_id = data.get("image_id")
        if not image_id:
            raise ValueError(f"Template '{template_id}' must include 'image_id'")

        subnet_ids = data.get("subnet_ids", [])
        if not subnet_ids:
            raise ValueError(f"Template '{template_id}' must include 'subnet_ids'")

        max_instances = data.get("max_instances", 1)
        if max_instances <= 0:
            raise ValueError(f"Template '{template_id}' max_instances must be greater than 0")

        # Create Template aggregate with all fields
        return Template(
            template_id=template_id,
            provider_type=data.get("provider_type"),
            provider_name=data.get("provider_name"),
            provider_api=data.get("provider_api"),
            image_id=image_id,
            subnet_ids=subnet_ids,
            max_instances=max_instances,
            instance_type=data.get("instance_type"),
            key_name=data.get("key_name"),
            security_group_ids=data.get("security_group_ids", []),
            user_data=data.get("user_data"),
            price_type=data.get("price_type", "ondemand"),
            metadata=data.get("metadata", {}),
            is_active=data.get("is_active", True),
        )

    def _aggregate_to_data(self, template: Template) -> Dict[str, Any]:
        """
        Convert Template aggregate to data dictionary.

        Args:
            template: Template aggregate

        Returns:
            Template data dictionary
        """
        return {
            "template_id": template.template_id,
            "provider_type": template.provider_type,
            "provider_name": template.provider_name,
            "provider_api": template.provider_api,
            "image_id": template.image_id,
            "subnet_ids": template.subnet_ids,
            "max_instances": template.max_instances,
            "instance_type": template.instance_type,
            "key_name": template.key_name,
            "security_group_ids": template.security_group_ids,
            "user_data": template.user_data,
            "price_type": template.price_type,
            "metadata": template.metadata,
            "is_active": template.is_active,
        }


# Register the repository in the singleton registry
def get_template_repository(
    config_manager: ConfigurationManager = None,
) -> TemplateJSONRepository:
    """
    Get template repository instance.

    Args:
        config_manager: Configuration manager (required for first call)

    Returns:
        Template repository instance
    """
    registry = SingletonRegistry()

    if not registry.has("template_repository"):
        if not config_manager:
            raise ConfigurationError(
                "ConfigurationManager required for first template repository creation"
            )

        repository = TemplateJSONRepository(config_manager)
        registry.register("template_repository", repository)
        return repository

    return registry.get("template_repository")


# Legacy compatibility - keep the old class name
JSONTemplateRepositoryImpl = TemplateJSONRepository
