"""Repository migration utilities for data persistence strategies.

This module provides utilities for migrating data between different repository
implementations and storage strategies:
- Template migration from legacy formats
- Data backup and restore operations
- Repository format conversion utilities
- Migration validation and rollback support
"""

import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from src.domain.base.domain_interfaces import Repository
from src.domain.base.ports.configuration_port import ConfigurationPort
from src.domain.template.repository import (
    TemplateRepository as TemplateRepositoryInterface,
)
from src.infrastructure.di.container import DIContainer
from src.infrastructure.logging.logger import get_logger

logger = get_logger()


class RepositoryMigrator:
    """
    Repository migrator for transferring data between different storage types.

    This class handles the migration of data between different repository implementations,
    such as JSON, SQLite, or DynamoDB. It supports batch processing and provides
    detailed statistics about the migration process.
    """

    def __init__(self, container: DIContainer):
        """
        Initialize repository migrator.

        Args:
            container: DI container
        """
        self.container = container
        self.logger = get_logger(__name__)
        self.collections = ["templates", "requests", "machines"]

        # Get configuration manager from container

        self.config_manager = self.container.get(ConfigurationPort)

    def migrate(
        self,
        source_type: str,
        target_type: str,
        batch_size: int = 100,
        create_backup: bool = True,
    ) -> Dict[str, Any]:
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
            "started_at": datetime.utcnow().isoformat(),
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
                self.logger.info(f"Created backup at {backup_path}")

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
            stats["completed_at"] = datetime.utcnow().isoformat()

            self.logger.info(f"Migration completed: {stats['total_migrated']} items migrated")

        except Exception as e:
            self.logger.error(f"Migration failed: {str(e)}")
            stats["status"] = "error"
            stats["error"] = str(e)
            stats["completed_at"] = datetime.utcnow().isoformat()

        return stats

    def _create_repositories_for_storage_type(self, storage_type: str) -> Dict[str, Repository]:
        """
        Create repositories for a specific storage type.

        Args:
            storage_type: Storage type (json, sqlite, dynamodb)

        Returns:
            Dictionary of repositories
        """
        # We'll pass the storage_type directly to the repository creation methods
        # instead of trying to update the config

        try:
            # For templates, always use template repository from DI container
            template_repo = self.container.get(TemplateRepositoryInterface)

            # If not registered, create a new JSON template repository
            if not template_repo:
                from src.config.manager import get_config_manager
                from src.infrastructure.persistence.json import JSONTemplateRepository

                # Get configuration manager
                config_manager = get_config_manager()

                # Use centralized file resolution for consistent HF_PROVIDER_CONFDIR
                # support
                templates_path = config_manager.resolve_file("template", "templates.json")
                legacy_templates_path = config_manager.resolve_file(
                    "template", "awsprov_templates.json"
                )

                get_logger().info(
                    "Repository migrator using centralized resolution for template files:"
                )
                get_logger().info(f"  templates.json: {templates_path}")
                get_logger().info(f"  awsprov_templates.json: {legacy_templates_path}")

                # Create a new template repository
                template_repo = JSONTemplateRepository(templates_path, legacy_templates_path)

                # Register it with the container
                self.container.register_singleton(TemplateRepositoryInterface, template_repo)

            # For other repositories, create based on storage type
            if storage_type == "dynamodb":
                from src.providers.aws.persistence.dynamodb import (
                    DynamoDBMachineRepository,
                    DynamoDBRequestRepository,
                )

                # Get DynamoDB config
                dynamodb_config = self.config_manager.get("dynamodb", {})
                region = dynamodb_config.get("region", "us-east-1")
                profile = dynamodb_config.get("profile")

                machine_repo = DynamoDBMachineRepository(
                    table_name=dynamodb_config.get("machine_table", "machines"),
                    region=region,
                    profile=profile,
                )

                request_repo = DynamoDBRequestRepository(
                    table_name=dynamodb_config.get("request_table", "requests"),
                    region=region,
                    profile=profile,
                )

            elif storage_type == "sql":
                from sqlalchemy import create_engine
                from sqlalchemy.orm import sessionmaker

                from src.infrastructure.persistence.sql import (
                    MachineModel,
                    RequestModel,
                    SQLMachineRepository,
                    SQLRequestRepository,
                )

                # Get SQL config
                sql_config = self.config_manager.get("sql", {})
                connection_string = sql_config.get("connection_string", "sqlite:///data.db")

                # Create engine and session
                engine = create_engine(connection_string)
                Session = sessionmaker(bind=engine)
                session = Session()

                # Import domain entities
                from src.domain.machine.aggregate import Machine
                from src.domain.request.aggregate import Request

                machine_repo = SQLMachineRepository(
                    entity_class=Machine, model_class=MachineModel, session=session
                )

                request_repo = SQLRequestRepository(
                    entity_class=Request, model_class=RequestModel, session=session
                )

            else:  # Default to JSON
                from src.infrastructure.persistence.json import (
                    JSONMachineRepository,
                    JSONRequestRepository,
                )

                # Get JSON config
                json_config = self.config_manager.get("json", {})
                data_dir = json_config.get("data_dir", "data")

                machine_repo = JSONMachineRepository(
                    file_path=os.path.join(data_dir, "machines.json"), create_dirs=True
                )

                request_repo = JSONRequestRepository(
                    file_path=os.path.join(data_dir, "requests.json"), create_dirs=True
                )

            # Create repository dictionary
            repositories = {
                "machines": machine_repo,
                "requests": request_repo,
                "templates": template_repo,
            }

            return repositories

        finally:
            # No need to restore config since we didn't modify it
            pass

    def _create_backup(self, repos: Dict[str, Repository]) -> str:
        """
        Create backup of current data.

        Args:
            repos: Dictionary of repositories

        Returns:
            Path to backup directory
        """
        from src.config.manager import get_config_manager

        # Get work directory from config manager
        config_manager = get_config_manager()
        work_dir = config_manager.get_work_dir()

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
                        f"Backed up { len(items)} items from {collection} to {backup_file}"
                    )
            except Exception as e:
                self.logger.warning(f"Failed to backup {collection}: {str(e)}")

        return backup_dir

    def _migrate_collection(
        self,
        collection_name: str,
        source_repo: Repository,
        target_repo: Repository,
        batch_size: int,
    ) -> Dict[str, Any]:
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
            self.logger.info(f"Migrating {stats['total_items']} items from {collection_name}")

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
                                from src.infrastructure.persistence.base.repository import (
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
                                            f"Failed to convert item {item_id} to entity: { str(conversion_error)}"
                                        )
                                        raise ValueError(
                                            f"Entity conversion failed: { str(conversion_error)}"
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
                                                f"Failed to create entity from item {item_id}: { str(conversion_error)}"
                                            )
                                            raise ValueError(
                                                f"Entity creation failed: { str(conversion_error)}"
                                            )
                                    else:
                                        # Fallback to direct save, which might fail
                                        self.logger.warning(
                                            f"No entity class found for repository, attempting direct save for item {item_id}"
                                        )
                                        target_repo.save(item)
                            except Exception as e:
                                raise ValueError(f"Failed to save item {item_id}: {str(e)}")
                        stats["migrated"] += 1

                    except Exception as e:
                        stats["failed"] += 1
                        error_id = item_id if item_id else "unknown"
                        stats["errors"].append({"item_id": error_id, "error": str(e)})
                        self.logger.warning(f"Failed to migrate item {error_id}: {str(e)}")

                self.logger.debug(
                    f"Migrated batch {stats['batches']} ({i+1}-{min(i+batch_size, len(items))}) of {collection_name}"
                )

        except Exception as e:
            stats["failed"] = stats["total_items"] - stats["migrated"]
            stats["errors"].append({"collection": collection_name, "error": str(e)})
            self.logger.error(f"Failed to migrate collection {collection_name}: {str(e)}")

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
