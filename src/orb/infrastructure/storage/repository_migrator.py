"""Repository migration utilities for data storage strategies.

This module provides utilities for migrating data between different repository
implementations and storage strategies:
- Template migration from legacy formats
- Data backup and restore operations
- Repository format conversion utilities
- Migration validation and rollback support
"""

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from orb.config.constants import (
    STORAGE_BACKEND_DYNAMODB,
    STORAGE_BACKEND_JSON,
    STORAGE_BACKEND_SQL,
)
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.template.repository import TemplateRepository as TemplateRepositoryInterface
from orb.infrastructure.di.container import DIContainer
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.registry import get_storage_registry

if TYPE_CHECKING:
    from orb.domain.base.domain_interfaces import Repository

logger = get_logger(__name__)


class RepositoryMigrator:
    """
    Repository migrator for transferring data between different storage types.

    This class handles the migration of data between different repository implementations,
    such as JSON, SQLite, or DynamoDB. It supports batch processing and provides
    detailed statistics about the migration process.
    """

    def __init__(self, container: DIContainer) -> None:
        """
        Initialize repository migrator.

        Args:
            container: DI container
        """
        self.container = container
        self.logger = logger
        self.collections = ["templates", "requests", "machines"]

        # Get configuration manager from container
        self.config_manager = self.container.get(ConfigurationPort)

    def migrate(
        self,
        source_type: str,
        target_type: str,
        batch_size: int = 100,
        create_backup: bool = True,
    ) -> dict[str, Any]:
        """
        Migrate data between repository types.

        Args:
            source_type: Source repository type (json, sqlite, dynamodb)
            target_type: Target repository type (json, sqlite, dynamodb)
            batch_size: Number of items to process in each batch
            create_backup: Whether to create a backup before migration

        Returns:
            Migration statistics
        """
        if source_type == target_type:
            return {
                "status": "skipped",
                "reason": "Source and target types are the same",
            }

        stats = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "source_type": source_type,
            "target_type": target_type,
            "batch_size": batch_size,
            "collections": {},
            "backup_created": None,
            "total_migrated": 0,
            "total_failed": 0,
        }

        try:
            # Create repositories for source and target storage types
            source_repos = self._create_repositories_for_storage_type(source_type)
            target_repos = self._create_repositories_for_storage_type(target_type)

            # Create backup if requested
            if create_backup:
                backup_path = self._create_backup(target_repos)
                stats["backup_created"] = backup_path
                self.logger.info("Created backup at %s", backup_path)

            # Perform migration
            for collection in self.collections:
                collection_stats = self._migrate_collection(
                    collection,
                    source_repos[collection],
                    target_repos[collection],
                    batch_size,
                )
                stats["collections"][collection] = collection_stats
                stats["total_migrated"] += collection_stats["migrated"]
                stats["total_failed"] += collection_stats["failed"]

            stats["status"] = "success"
            stats["completed_at"] = datetime.now(timezone.utc).isoformat()

            self.logger.info("Migration completed: %s items migrated", stats["total_migrated"])

        except Exception as e:
            self.logger.error("Migration failed: %s", str(e))
            stats["status"] = "error"
            stats["error"] = str(e)
            stats["completed_at"] = datetime.now(timezone.utc).isoformat()

        return stats

    def _create_repositories_for_storage_type(self, storage_type: str) -> dict[str, Any]:
        """
        Create repositories for a specific storage type via the storage registry.

        Args:
            storage_type: Storage type constant (STORAGE_BACKEND_* from config.constants)

        Returns:
            Dictionary of repositories keyed by collection name
        """
        # Always get template repository from DI container
        template_repo = self.container.get(TemplateRepositoryInterface)

        registry = get_storage_registry()

        if storage_type == STORAGE_BACKEND_DYNAMODB:
            uow = registry.create_unit_of_work(STORAGE_BACKEND_DYNAMODB, self.config_manager)
            if uow is None:
                raise ValueError(
                    f"Storage type '{STORAGE_BACKEND_DYNAMODB}' has no unit-of-work factory"
                )
            machine_repo = uow.machines
            request_repo = uow.requests

        elif storage_type == STORAGE_BACKEND_SQL:
            uow = registry.create_unit_of_work(STORAGE_BACKEND_SQL, self.config_manager)
            if uow is None:
                raise ValueError(
                    f"Storage type '{STORAGE_BACKEND_SQL}' has no unit-of-work factory"
                )
            machine_repo = uow.machines
            request_repo = uow.requests

        elif storage_type == STORAGE_BACKEND_JSON:
            uow = registry.create_unit_of_work(STORAGE_BACKEND_JSON, self.config_manager)
            if uow is None:
                # JSON UoW is optional; fall back to direct strategy-based repos
                json_config = self.config_manager.get(STORAGE_BACKEND_JSON, {})
                data_dir = json_config.get("data_dir", "data")

                from orb.infrastructure.storage.json import (  # type: ignore[import]
                    JSONMachineRepository,  # type: ignore[no-redef]
                    JSONRequestRepository,  # type: ignore[no-redef]
                )

                machine_repo = JSONMachineRepository(
                    file_path=os.path.join(data_dir, "machines.json"), create_dirs=True
                )
                request_repo = JSONRequestRepository(
                    file_path=os.path.join(data_dir, "requests.json"), create_dirs=True
                )
            else:
                machine_repo = uow.machines
                request_repo = uow.requests

        else:
            raise ValueError(f"Unsupported storage type for migration: '{storage_type}'")

        return {
            "machines": machine_repo,
            "requests": request_repo,
            "templates": template_repo,
        }

    def _create_backup(self, repos: dict[str, Repository]) -> str:
        """
        Create backup of current data.

        Args:
            repos: Dictionary of repositories

        Returns:
            Path to backup directory
        """
        from orb.application.ports.scheduler_port import SchedulerPort

        # Get work directory from scheduler strategy (via DI container)
        try:
            scheduler = self.container.get(SchedulerPort)
            work_dir = scheduler.get_working_directory()
        except Exception:
            # Fallback to config manager if scheduler not available
            config_manager = self.container.get(ConfigurationPort)
            work_dir = config_manager.get_cache_dir()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(work_dir, "backups", f"repository_backup_{timestamp}")
        os.makedirs(backup_dir, exist_ok=True)

        for collection in self.collections:
            try:
                items = repos[collection].find_all()
                if items:
                    backup_file = os.path.join(backup_dir, f"{collection}.json")
                    with open(backup_file, "w") as f:
                        json.dump(
                            [
                                item.to_dict() if hasattr(item, "to_dict") else item
                                for item in items
                            ],
                            f,
                            indent=2,
                        )
                    self.logger.debug(
                        "Backed up %s items from %s to %s",
                        len(items),
                        collection,
                        backup_file,
                    )
            except Exception as e:
                self.logger.warning("Failed to backup %s: %s", collection, str(e))

        return backup_dir

    def _migrate_collection(
        self,
        collection_name: str,
        source_repo: Repository,
        target_repo: Repository,
        batch_size: int,
    ) -> dict[str, Any]:
        """
        Migrate a single collection.

        Args:
            collection_name: Name of the collection
            source_repo: Source repository
            target_repo: Target repository
            batch_size: Number of items to process in each batch

        Returns:
            Collection migration statistics
        """
        stats = {
            "total_items": 0,
            "migrated": 0,
            "failed": 0,
            "batches": 0,
            "errors": [],
        }

        try:
            items = source_repo.find_all()
            stats["total_items"] = len(items)
            self.logger.info("Migrating %s items from %s", stats["total_items"], collection_name)

            # Process in batches
            for i in range(0, len(items), batch_size):
                batch = items[i : i + batch_size]
                stats["batches"] += 1

                for item in batch:
                    # Initialize item_id as None before trying to get it
                    item_id = None
                    try:
                        item_id = self._get_item_id(item, collection_name)
                        if not item_id:
                            raise ValueError(f"Item without ID found in {collection_name}")

                        # Check if the item is already an entity object
                        if (
                            hasattr(item, "id")
                            or hasattr(item, "request_id")
                            or hasattr(item, "machine_id")
                        ):
                            # Item is already an entity, just save it
                            target_repo.save(item)
                        else:
                            # Item is a dictionary, we need to convert it to an entity
                            # first
                            try:
                                # Check if the repository is a StrategyBasedRepository
                                from orb.infrastructure.storage.base.repository import (
                                    StrategyBasedRepository,
                                )

                                if isinstance(target_repo, StrategyBasedRepository):
                                    # Use the _from_dict method to convert dictionary to
                                    # entity
                                    try:
                                        entity = target_repo._from_dict(item)
                                        target_repo.save(entity)
                                    except Exception as conversion_error:
                                        self.logger.error(
                                            "Failed to convert item %s to entity: %s",
                                            item_id,
                                            str(conversion_error),
                                        )
                                        raise ValueError(
                                            f"Entity conversion failed: {conversion_error!s}"
                                        )
                                else:
                                    # Try to determine the entity class from the
                                    # repository
                                    entity_class = None
                                    # Use getattr with a default value to safely access
                                    # the attribute
                                    entity_class = getattr(target_repo, "entity_class", None)

                                    if entity_class:
                                        # Try to create entity using the entity class
                                        try:
                                            if hasattr(entity_class, "model_validate"):
                                                # Use Pydantic's model_validate if
                                                # available
                                                entity = entity_class.model_validate(item)
                                            elif hasattr(entity_class, "from_dict"):
                                                # Use from_dict if available
                                                entity = entity_class.from_dict(item)
                                            else:
                                                # Fall back to constructor
                                                entity = entity_class(**item)

                                            target_repo.save(entity)
                                        except Exception as conversion_error:
                                            self.logger.error(
                                                "Failed to create entity from item %s: %s",
                                                item_id,
                                                str(conversion_error),
                                            )
                                            raise ValueError(
                                                f"Entity creation failed: {conversion_error!s}"
                                            )
                                    else:
                                        # Fallback to direct save, which might fail
                                        self.logger.warning(
                                            "No entity class found for repository, attempting direct save for item %s",
                                            item_id,
                                        )
                                        target_repo.save(item)
                            except Exception as e:
                                raise ValueError(f"Failed to save item {item_id}: {e!s}")
                        stats["migrated"] += 1

                    except Exception as e:
                        stats["failed"] += 1
                        error_id = item_id if item_id else "unknown"
                        stats["errors"].append({"item_id": error_id, "error": str(e)})
                        self.logger.warning("Failed to migrate item %s: %s", error_id, str(e))

                self.logger.debug(
                    "Migrated batch %s (%s-%s) of %s",
                    stats["batches"],
                    i + 1,
                    min(i + batch_size, len(items)),
                    collection_name,
                )

        except Exception as e:
            stats["failed"] = stats["total_items"] - stats["migrated"]
            stats["errors"].append({"collection": collection_name, "error": str(e)})
            self.logger.error("Failed to migrate collection %s: %s", collection_name, str(e))

        return stats

    def _get_item_id(self, item: Any, collection_name: str) -> Optional[str]:
        """
        Get ID from an item based on collection type.

        Args:
            item: Item to get ID from
            collection_name: Name of the collection

        Returns:
            Item ID or None if not found
        """
        if hasattr(item, "id"):
            return item.id

        if isinstance(item, dict):
            # Try collection-specific ID fields
            if collection_name == "templates":
                return item.get("id") or item.get("template_id") or item.get("templateId")
            elif collection_name == "requests":
                return item.get("id") or item.get("request_id") or item.get("requestId")
            elif collection_name == "machines":
                return item.get("id") or item.get("machine_id") or item.get("machineId")

            # Try generic ID fields
            for id_field in ["id", "ID", "_id", "uuid", "UUID"]:
                if id_field in item:
                    return item[id_field]

        return None
