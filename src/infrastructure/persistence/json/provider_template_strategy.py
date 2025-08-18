"""Provider-specific template loading strategy."""

import json
import os
from typing import Any, Dict, List, Optional

from config.managers.configuration_manager import ConfigurationManager
from infrastructure.logging.logger import get_logger
from infrastructure.persistence.json.strategy import JSONStorageStrategy


class ProviderTemplateStrategy(JSONStorageStrategy):
    """
    Template loading strategy that supports provider-specific template files.

    This strategy loads templates from multiple sources in priority order:
    1. Provider instance-specific files (e.g., aws-us-east-1_templates.json)
    2. Provider type-specific files (e.g., awsprov_templates.json)
    3. Main templates file (templates.json)
    4. Legacy templates file (awsprov_templates.json)

    Templates from higher priority sources override those from lower priority sources
    when they have the same template_id.
    """

    def __init__(
        self,
        base_file_path: str,
        config_manager: ConfigurationManager,
        create_dirs: bool = True,
    ):
        """
        Initialize provider template strategy.

        Args:
            base_file_path: Base path for template files (e.g., config/templates.json)
            config_manager: Configuration manager for provider information
            create_dirs: Whether to create directories
        """
        super().__init__(base_file_path, create_dirs)
        self.config_manager = config_manager
        self.logger = get_logger(__name__)

        # Cache for loaded templates to avoid repeated file reads
        self._template_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._cache_timestamp: Optional[float] = None

        # Discover all template files
        self._template_files = self._discover_template_files()

        self.logger.info(
            "Initialized provider template strategy with %s template files", len(self._template_files)
        )
        for file_path in self._template_files:
            self.logger.debug("Template file: %s", file_path)

    def _discover_template_files(self) -> List[str]:
        """
        Discover all template files in priority order.

        Returns:
            List of template file paths in priority order (highest to lowest)
        """
        template_files = []
        base_dir = os.path.dirname(str(self.file_manager.file_path))

        try:
            # Get provider configuration
            provider_config = self.config_manager.get_provider_config()

            # 1. Provider instance-specific files (highest priority)
            for provider_instance in provider_config.providers:
                if provider_instance.enabled:
                    instance_file = os.path.join(
                        base_dir, f"{provider_instance.name}_templates.json"
                    )
                    if os.path.exists(instance_file):
                        template_files.append(instance_file)
                        self.logger.debug("Found provider instance template file: %s", instance_file)

            # 2. Provider type-specific files
            provider_types = set()
            for provider_instance in provider_config.providers:
                if provider_instance.enabled:
                    provider_types.add(provider_instance.type)

            for provider_type in provider_types:
                type_file = os.path.join(base_dir, f"{provider_type}prov_templates.json")
                if os.path.exists(type_file):
                    template_files.append(type_file)
                    self.logger.debug("Found provider type template file: %s", type_file)

            # 3. Main templates file
            if os.path.exists(self.file_manager.file_path):
                template_files.append(str(self.file_manager.file_path))
                self.logger.debug("Found main template file: %s", self.file_manager.file_path)

            # 4. Legacy templates file (lowest priority)
            legacy_file = os.path.join(base_dir, "awsprov_templates.json")
            if os.path.exists(legacy_file) and legacy_file not in template_files:
                template_files.append(legacy_file)
                self.logger.debug("Found legacy template file: %s", legacy_file)

        except Exception as e:
            self.logger.warning("Error discovering template files: %s", e)
            # Fallback to main file only
            if os.path.exists(self.file_manager.file_path):
                template_files = [str(self.file_manager.file_path)]

        return template_files

    def _load_merged_templates(self) -> Dict[str, Dict[str, Any]]:
        """
        Load and merge templates from all discovered files.

        Returns:
            Dictionary of merged templates by template_id
        """
        merged_templates = {}

        # Load templates from all files in reverse priority order (lowest to highest)
        for file_path in reversed(self._template_files):
            try:
                if os.path.exists(file_path):
                    with open(file_path, "r", encoding="utf-8") as f:
                        file_data = json.load(f)

                    # Handle different file formats
                    if isinstance(file_data, list):
                        # Array format: [{"template_id": "...", ...}, ...]
                        for template_data in file_data:
                            if isinstance(template_data, dict) and "template_id" in template_data:
                                template_id = template_data["template_id"]
                                merged_templates[template_id] = template_data
                                self.logger.debug(
                                    "Loaded template '%s' from %s", template_id, file_path
                                )

                    elif isinstance(file_data, dict):
                        # Object format: {"template1": {...}, "template2": {...}}
                        for template_id, template_data in file_data.items():
                            if isinstance(template_data, dict):
                                # Ensure template_id is set
                                template_data["template_id"] = template_id
                                merged_templates[template_id] = template_data
                                self.logger.debug(
                                    "Loaded template '%s' from %s", template_id, file_path
                                )

                    self.logger.info(
                        "Loaded %s templates from %s",  len(file_data) if isinstance( file_data, (list, dict)) else 0, file_path
                    )

            except Exception as e:
                self.logger.error("Error loading templates from %s: %s", file_path, e)
                continue

        self.logger.info(
            "Merged %s unique templates from %s files", len(merged_templates), len(self._template_files)
        )
        return merged_templates

    def _get_templates_cache(self) -> Dict[str, Dict[str, Any]]:
        """
        Get templates with caching support.

        Returns:
            Dictionary of templates by template_id
        """
        # Check if we need to refresh cache
        current_time = (
            os.path.getmtime(max(self._template_files, key=os.path.getmtime))
            if self._template_files
            else 0
        )

        if self._template_cache is None or self._cache_timestamp != current_time:
            self._template_cache = self._load_merged_templates()
            self._cache_timestamp = current_time
            self.logger.debug("Refreshed template cache")

        return self._template_cache

    def find_all(self) -> List[Dict[str, Any]]:
        """
        Find all templates from all provider-specific files.

        Returns:
            List of all template data
        """
        templates = self._get_templates_cache()
        return list(templates.values())

    def find_by_id(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """
        Find template by ID from merged provider-specific files.

        Args:
            entity_id: Template ID

        Returns:
            Template data if found, None otherwise
        """
        templates = self._get_templates_cache()
        return templates.get(entity_id)

    def save(self, entity_data: Dict[str, Any]) -> None:
        """
        Save template to the appropriate provider-specific file.

        Args:
            entity_data: Template data to save
        """
        template_id = entity_data.get("template_id")
        if not template_id:
            raise ValueError("Template data must include 'template_id'") from e

        # Determine which file to save to based on provider information
        target_file = self._determine_target_file(entity_data)

        try:
            # Load existing data from target file
            existing_data = []
            if os.path.exists(target_file):
                with open(target_file, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                    if not isinstance(existing_data, list):
                        existing_data = []

            # Update or add template
            updated = False
            for i, template in enumerate(existing_data):
                if template.get("template_id") == template_id:
                    existing_data[i] = entity_data
                    updated = True
                    break

            if not updated:
                existing_data.append(entity_data)

            # Save back to file
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            with open(target_file, "w", encoding="utf-8") as f:
                json.dump(existing_data, f, indent=2, ensure_ascii=False)

            # Clear cache to force reload
            self._template_cache = None

            self.logger.info("Saved template '%s' to %s", template_id, target_file)

        except Exception as e:
            self.logger.error("Error saving template '%s' to %s: %s", template_id, target_file, e)
            raise

    def _determine_target_file(self, entity_data: Dict[str, Any]) -> str:
        """
        Determine which file to save the template to based on provider information.

        Args:
            entity_data: Template data

        Returns:
            Path to target file
        """
        base_dir = os.path.dirname(str(self.file_manager.file_path))

        # Check if template specifies provider information
        provider_name = entity_data.get("provider_name")
        provider_type = entity_data.get("provider_type")

        if provider_name:
            # Save to provider instance-specific file
            return os.path.join(base_dir, f"{provider_name}_templates.json")
        elif provider_type:
            # Save to provider type-specific file
            return os.path.join(base_dir, f"{provider_type}prov_templates.json")
        else:
            # Save to main templates file
            return self.file_manager.file_path

    def delete(self, entity_id: str) -> bool:
        """
        Delete template from all files where it exists.

        Args:
            entity_id: Template ID to delete

        Returns:
            True if template was found and deleted, False otherwise
        """
        deleted = False

        for file_path in self._template_files:
            try:
                if not os.path.exists(file_path):
                    continue

                with open(file_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)

                if isinstance(file_data, list):
                    # Array format
                    original_length = len(file_data)
                    file_data = [t for t in file_data if t.get("template_id") != entity_id]

                    if len(file_data) < original_length:
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(file_data, f, indent=2, ensure_ascii=False)
                        deleted = True
                        self.logger.info("Deleted template '%s' from %s", entity_id, file_path)

                elif isinstance(file_data, dict) and entity_id in file_data:
                    # Object format
                    del file_data[entity_id]
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(file_data, f, indent=2, ensure_ascii=False)
                    deleted = True
                    self.logger.info("Deleted template '%s' from %s", entity_id, file_path)

            except Exception as e:
                self.logger.error("Error deleting template '%s' from %s: %s", entity_id, file_path, e)
                continue

        if deleted:
            # Clear cache to force reload
            self._template_cache = None

        return deleted

    def get_template_source_info(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        Get information about which file a template was loaded from.

        Args:
            template_id: Template ID

        Returns:
            Dictionary with source information or None if not found
        """
        for file_path in self._template_files:
            try:
                if not os.path.exists(file_path):
                    continue

                with open(file_path, "r", encoding="utf-8") as f:
                    file_data = json.load(f)

                # Check if template exists in this file
                template_found = False
                if isinstance(file_data, list):
                    template_found = any(t.get("template_id") == template_id for t in file_data)
                elif isinstance(file_data, dict):
                    template_found = template_id in file_data

                if template_found:
                    return {
                        "source_file": file_path,
                        "file_type": self._classify_file_type(file_path),
                        "priority": self._template_files.index(file_path),
                    }

            except Exception as e:
                self.logger.error("Error checking template source in %s: %s", file_path, e)
                continue

        return None

    def _classify_file_type(self, file_path: str) -> str:
        """
        Classify the type of template file.

        Args:
            file_path: Path to template file

        Returns:
            File type classification
        """
        filename = os.path.basename(file_path)

        if filename == "templates.json":
            return "main"
        elif filename == "awsprov_templates.json":
            return "legacy"
        elif filename.endswith("prov_templates.json"):
            return "provider_type"
        elif filename.endswith("_templates.json"):
            return "provider_instance"
        else:
            return "unknown"

    def refresh_cache(self) -> None:
        """Force refresh of template cache."""
        self._template_cache = None
        self._template_files = self._discover_template_files()
        self.logger.info("Refreshed template file discovery and cache")
