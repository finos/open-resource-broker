"""Entity -> table mapping for SQL storage.

Requests, machines and templates are stored in typed tables defined by the ORM
models. Every SQL entry point (unit of work, repositories built through the
storage registry, health probes) builds its strategy through this module, so they
all read and write the same tables.
"""

from typing import Any

from orb.domain.base.exceptions import ConfigurationError

SQL_ENTITY_TABLES: tuple[str, ...] = ("requests", "machines", "templates")

# Entity used when a caller has none (health probes only read the schema)
DEFAULT_ENTITY = "requests"


def entity_columns(table: str) -> dict[str, str]:
    """Column dict ``{name: "TEXT"}`` for an entity table, derived from its ORM model.

    The ORM stays the schema authority; the dict only gives the query builder the
    column names it may write.
    """
    from orb.infrastructure.storage.sql.models import MachineModel, RequestModel, TemplateModel

    models = {"requests": RequestModel, "machines": MachineModel, "templates": TemplateModel}
    model = models.get(table)
    if model is None:
        raise ConfigurationError(
            f"Unknown SQL entity '{table}'. Expected one of: {', '.join(SQL_ENTITY_TABLES)}."
        )
    return {col.key: "TEXT" for col in model.__table__.columns}  # type: ignore[attr-defined]


def create_entity_strategy(config: dict[str, Any], table: str) -> Any:
    """SQL storage strategy for one entity table."""
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    return SQLStorageStrategy(config=config, table_name=table, columns=entity_columns(table))
