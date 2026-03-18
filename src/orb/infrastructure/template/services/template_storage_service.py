"""Template Storage Service

Handles CRUD operations for templates while delegating to scheduler strategies
for format conversion and file operations.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.dependency_injection import injectable
from orb.domain.base.events.domain_events import (
    TemplateCreatedEvent,
    TemplateDeletedEvent,
    TemplateUpdatedEvent,
)
from orb.domain.base.exceptions import EntityNotFoundError
from orb.domain.base.ports.event_publisher_port import EventPublisherPort
from orb.domain.base.ports.logging_port import LoggingPort
from orb.infrastructure.template.dtos import TemplateDTO


@injectable
class TemplateStorageService:
    """
    Service for storing template changes to files.

    Delegates to scheduler strategy for file format handling while
    managing the storage operations and event publishing.
    """

    def __init__(
        self,
        scheduler_strategy: SchedulerPort,
        logger: LoggingPort,
        event_publisher: Optional[EventPublisherPort] = None,
    ) -> None:
        """
        Initialize the template storage service.

        Args:
            scheduler_strategy: Strategy for file operations and format conversion
            logger: Logger for operations and debugging
            event_publisher: Optional event publisher for domain events
        """
        self.scheduler_strategy = scheduler_strategy
        self.logger = logger
        self.event_publisher = event_publisher

        self.logger.debug("Initialized template storage service")

    async def save_template(self, template: TemplateDTO) -> None:
        """
        Save template to configuration files.

        Args:
            template: Template to save
        """
        try:
            # Get template file paths from scheduler strategy
            template_paths = self.scheduler_strategy.get_template_paths()  # type: ignore[attr-defined]
            if not template_paths:
                raise ValueError("No template paths available from scheduler strategy")

            # Use first path as primary target (scheduler strategy determines priority)
            target_file = Path(template_paths[0])

            # Load existing templates from target file
            existing_templates = await self._load_templates_from_file(target_file)

            # Convert to scheduler-native format before writing
            template_dict = template.model_dump(exclude_none=True)
            template_dict = self.scheduler_strategy.serialize_template_for_storage(template_dict)

            # Update or add the template (on-disk entries may use native camelCase keys)
            template_found = False
            for i, existing_template in enumerate(existing_templates):
                existing_id = existing_template.get("template_id") or existing_template.get(
                    "templateId"
                )
                if existing_id == template.template_id:
                    existing_templates[i] = template_dict
                    template_found = True
                    break

            if not template_found:
                existing_templates.append(template_dict)

            # Write back to file using scheduler strategy format
            await self._write_templates_to_file(target_file, existing_templates)

            # Publish domain event
            if self.event_publisher:
                if template_found:
                    event = TemplateUpdatedEvent(
                        aggregate_id=template.template_id,
                        aggregate_type="template",
                        template_id=template.template_id,
                        template_name=template.name or template.template_id,
                        changes=template_dict,
                        version=getattr(template, "version", 1),
                    )
                else:
                    event = TemplateCreatedEvent(
                        aggregate_id=template.template_id,
                        aggregate_type="template",
                        template_id=template.template_id,
                        template_name=template.name or template.template_id,
                        template_type=template.provider_api or "",
                        configuration=template_dict,
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
                existing_templates = await self._load_templates_from_file(target_file)
                original_count = len(existing_templates)
                existing_templates = [
                    t
                    for t in existing_templates
                    if t.get("template_id") != template_id and t.get("templateId") != template_id
                ]
                if len(existing_templates) == original_count:
                    raise EntityNotFoundError("Template", template_id)
            else:
                # Search all paths for the template
                template_paths = self.scheduler_strategy.get_template_paths()  # type: ignore[attr-defined]
                if not template_paths:
                    raise ValueError("No template paths available from scheduler strategy")

                target_file = None
                existing_templates = []
                for path in template_paths:
                    candidate = Path(path)
                    templates = await self._load_templates_from_file(candidate)
                    filtered = [
                        t
                        for t in templates
                        if t.get("template_id") != template_id
                        and t.get("templateId") != template_id
                    ]
                    if len(filtered) < len(templates):
                        target_file = candidate
                        existing_templates = filtered
                        break

                if target_file is None:
                    raise EntityNotFoundError("Template", template_id)

            # Write back to file
            await self._write_templates_to_file(target_file, existing_templates)

            # Publish domain event
            if self.event_publisher:
                event = TemplateDeletedEvent(
                    aggregate_id=template_id,
                    aggregate_type="template",
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

    async def _load_templates_from_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Load raw on-disk template dicts without field mapping."""
        if not file_path.exists():
            return []
        try:
            with open(file_path, encoding="utf-8") as f:
                if file_path.suffix.lower() in {".yml", ".yaml"}:
                    import yaml

                    raw = yaml.safe_load(f) or {}
                else:
                    import json

                    raw = json.load(f)
            return raw.get("templates", []) if isinstance(raw, dict) else []
        except Exception as e:
            self.logger.error("Failed to load templates from %s: %s", file_path, e)
            return []

    async def _write_templates_to_file(
        self, file_path: Path, templates: list[dict[str, Any]]
    ) -> None:
        """Write templates to a file using appropriate format."""
        try:
            import json

            import yaml

            # Ensure directory exists
            file_path.parent.mkdir(parents=True, exist_ok=True)

            # Embed scheduler_type so round-trip loading can delegate correctly
            data: dict[str, Any] = {
                "scheduler_type": self.scheduler_strategy.get_scheduler_type(),
                "templates": templates,
            }

            # Write in appropriate format based on file extension
            if file_path.suffix.lower() in {".yml", ".yaml"}:
                with open(file_path, "w", encoding="utf-8") as f:
                    yaml.dump(data, f, default_flow_style=False, indent=2)
            else:
                from datetime import date, datetime

                def _json_default(obj: Any) -> Any:
                    if isinstance(obj, (datetime, date)):
                        return obj.isoformat()
                    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False, default=_json_default)

            self.logger.debug("Wrote %s templates to %s", len(templates), file_path)

        except Exception as e:
            self.logger.error("Failed to write templates to %s: %s", file_path, e)
            raise
