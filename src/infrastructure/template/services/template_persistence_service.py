"""Template Persistence Service

Handles CRUD operations for templates while delegating to scheduler strategies
for format conversion and file operations.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from domain.base.dependency_injection import injectable
from domain.base.events.domain_events import (
    TemplateCreatedEvent,
    TemplateDeletedEvent,
    TemplateUpdatedEvent,
)
from domain.base.ports.event_publisher_port import EventPublisherPort
from domain.base.ports.logging_port import LoggingPort
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.template.dtos import TemplateDTO


@injectable
class TemplatePersistenceService:
    """
    Service for persisting template changes to files.

    Delegates to scheduler strategy for file format handling while
    managing the persistence operations and event publishing.
    """

    def __init__(
        self,
        scheduler_strategy: SchedulerPort,
        logger: LoggingPort,
        event_publisher: Optional[EventPublisherPort] = None,
    ):
        """
        Initialize the template persistence service.

        Args:
            scheduler_strategy: Strategy for file operations and format conversion
            logger: Logger for operations and debugging
            event_publisher: Optional event publisher for domain events
        """
        self.scheduler_strategy = scheduler_strategy
        self.logger = logger
        self.event_publisher = event_publisher

        self.logger.debug("Initialized template persistence service")

    async def save_template(self, template: TemplateDTO) -> None:
        """
        Save template to configuration files.

        Args:
            template: Template to save
        """
        try:
            # Get template file paths from scheduler strategy
            template_paths = self.scheduler_strategy.get_template_paths()
            if not template_paths:
                raise ValueError("No template paths available from scheduler strategy") from e

            # Use first path as primary target (scheduler strategy determines priority)
            target_file = Path(template_paths[0])

            # Load existing templates from target file
            existing_templates = await self._load_templates_from_file(target_file)

            # Update or add the template
            template_found = False
            for i, existing_template in enumerate(existing_templates):
                if existing_template.get("template_id") == template.template_id:
                    existing_templates[i] = template.configuration
                    template_found = True
                    break

            if not template_found:
                existing_templates.append(template.configuration)

            # Write back to file using scheduler strategy format
            await self._write_templates_to_file(target_file, existing_templates)

            # Publish domain event
            if self.event_publisher:
                if template_found:
                    event = TemplateUpdatedEvent(
                        template_id=template.template_id,
                        template_name=template.name or template.template_id,
                        changes=template.configuration,
                        version=getattr(template, "version", 1),
                    )
                else:
                    event = TemplateCreatedEvent(
                        template_id=template.template_id,
                        template_name=template.name or template.template_id,
                        template_type=template.provider_api,
                        configuration=template.configuration,
                    )
                self.event_publisher.publish(event)
                self.logger.debug("Published domain event for template %s", template.template_id)

            self.logger.info("Saved template %s to %s", template.template_id, target_file)

        except Exception as e:
            self.logger.error("Failed to save template %s: %s", template.template_id, e)
            raise

    async def delete_template(self, template_id: str, source_file: Optional[Path] = None) -> None:
        """
        Delete template from configuration files.

        Args:
            template_id: Template identifier to delete
            source_file: Optional specific file to delete from
        """
        try:
            # Determine source file
            if source_file:
                target_file = source_file
            else:
                # Use first template path as default
                template_paths = self.scheduler_strategy.get_template_paths()
                if not template_paths:
                    raise ValueError("No template paths available from scheduler strategy") from e
                target_file = Path(template_paths[0])

            # Load existing templates from source file
            existing_templates = await self._load_templates_from_file(target_file)

            # Remove the template
            original_count = len(existing_templates)
            existing_templates = [
                t
                for t in existing_templates
                if t.get("template_id") != template_id and t.get("templateId") != template_id
            ]

            if len(existing_templates) == original_count:
                raise ValueError(f"Template {template_id} not found in source file") from e

            # Write back to file
            await self._write_templates_to_file(target_file, existing_templates)

            # Publish domain event
            if self.event_publisher:
                event = TemplateDeletedEvent(
                    template_id=template_id,
                    template_name=template_id,  # We don't have the name at this point
                    deletion_reason="User requested deletion",
                    deletion_time=datetime.now(),
                )
                self.event_publisher.publish(event)
                self.logger.debug("Published deletion event for template %s", template_id)

            self.logger.info("Deleted template %s from %s", template_id, target_file)

        except Exception as e:
            self.logger.error("Failed to delete template %s: %s", template_id, e)
            raise

    async def _load_templates_from_file(self, file_path: Path) -> list[Dict[str, Any]]:
        """Load raw template data from a file using scheduler strategy."""
        try:
            # Use scheduler strategy to load and parse templates
            templates = self.scheduler_strategy.load_templates_from_path(str(file_path))
            return templates
        except Exception as e:
            self.logger.error("Failed to load templates from %s: %s", file_path, e)
            return []

    async def _write_templates_to_file(
        self, file_path: Path, templates: list[Dict[str, Any]]
    ) -> None:
        """Write templates to a file using appropriate format."""
        try:
            import json

            import yaml

            # Ensure directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Prepare data structure (HostFactory format expects templates array)
            data = {"templates": templates}

            # Write in appropriate format based on file extension
            if file_path.suffix.lower() in {".yml", ".yaml"}:
                with open(file_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, default_flow_style=False, indent=2)
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)

            self.logger.debug("Wrote %s templates to %s", len(templates), file_path)

        except Exception as e:
            self.logger.error("Failed to write templates to %s: %s", file_path, e)
            raise
