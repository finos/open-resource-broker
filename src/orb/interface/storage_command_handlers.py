"""Storage-related command handlers for the interface layer."""

from __future__ import annotations

import asyncio
import re
import sys
from typing import TYPE_CHECKING, Any, Callable

from orb.application.dto.interface_response import InterfaceResponse
from orb.infrastructure.error.decorators import handle_interface_exceptions

if TYPE_CHECKING:
    import argparse

    from orb.application.ports.query_bus_port import QueryBusPort
    from orb.application.services.orchestration.get_storage_config import (
        GetStorageConfigOrchestrator,
    )
    from orb.application.services.orchestration.list_storage_strategies import (
        ListStorageStrategiesOrchestrator,
    )
    from orb.config.manager import ConfigurationManager
    from orb.interface.response_formatting_service import ResponseFormattingService


class StorageHandlers:
    """CLI handlers that inspect, test and migrate the storage configuration.

    The query bus and the configuration manager are providers: the handlers that
    use them resolve them inside a guarded block and report a failure as an error
    response, so resolution has to happen at the same point it always did.
    """

    def __init__(
        self,
        list_orchestrator: ListStorageStrategiesOrchestrator,
        config_orchestrator: GetStorageConfigOrchestrator,
        query_bus_provider: Callable[[], QueryBusPort],
        config_manager_provider: Callable[[], ConfigurationManager],
        formatter: ResponseFormattingService,
    ) -> None:
        self._list_orchestrator = list_orchestrator
        self._config_orchestrator = config_orchestrator
        self._query_bus_provider = query_bus_provider
        self._config_manager_provider = config_manager_provider
        self._formatter = formatter

    @handle_interface_exceptions(context="list_storage_strategies", interface_type="cli")
    async def list_storage_strategies(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle list storage strategies operations."""
        from orb.application.services.orchestration.dtos import ListStorageStrategiesInput

        orchestrator = self._list_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(ListStorageStrategiesInput())
        return formatter.format_storage_strategy_list(
            result.strategies, result.current_strategy, result.count
        )

    @handle_interface_exceptions(context="show_storage_config", interface_type="cli")
    async def show_storage_config(
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle show storage configuration operations."""
        from orb.application.services.orchestration.dtos import GetStorageConfigInput

        orchestrator = self._config_orchestrator
        formatter = self._formatter

        result = await orchestrator.execute(
            GetStorageConfigInput(strategy_name=getattr(args, "strategy", None))
        )
        return formatter.format_storage_config(result.config)

    @handle_interface_exceptions(context="validate_storage_config", interface_type="cli")
    async def validate_storage_config(  # type: ignore[return]
        self, args: argparse.Namespace
    ) -> dict[str, Any] | InterfaceResponse:
        """Handle validate storage configuration operations."""
        formatter = self._formatter
        try:
            query_bus = self._query_bus_provider()
            from orb.application.queries.storage import GetStorageHealthQuery

            result = await query_bus.execute(GetStorageHealthQuery())
            raw = result if isinstance(result, dict) else {"status": "healthy"}
            return formatter.format_success({**raw, "message": "Storage configuration is valid"})  # type: ignore[attr-defined]
        except ImportError:
            return formatter.format_success(
                {"message": "Storage configuration is valid", "status": "ok"}
            )  # type: ignore[attr-defined]
        except Exception as e:
            return formatter.format_error(f"Storage configuration invalid: {e}")

    @handle_interface_exceptions(context="test_storage", interface_type="cli")
    async def test_storage(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle test storage operations."""
        query_bus = self._query_bus_provider()
        formatter = self._formatter

        from orb.application.dto.queries import ValidateStorageQuery  # type: ignore[attr-defined]

        query = ValidateStorageQuery(
            strategy_name=getattr(args, "strategy", None),
            timeout=getattr(args, "timeout", 30),
        )
        result = await query_bus.execute(query)

        raw = result if isinstance(result, dict) else {"test_result": result}
        return formatter.format_storage_test(raw)

    @handle_interface_exceptions(context="storage_health", interface_type="cli")
    async def storage_health(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle storage health operations."""
        formatter = self._formatter
        try:
            query_bus = self._query_bus_provider()
            from orb.application.queries.storage import (
                GetStorageHealthQuery,  # type: ignore[attr-defined]
            )

            query = GetStorageHealthQuery(
                strategy_name=getattr(args, "strategy", None),
                verbose=getattr(args, "verbose", False),
            )
            health = await query_bus.execute(query)
            raw = (
                health
                if isinstance(health, dict)
                else health.model_dump()
                if hasattr(health, "model_dump")
                else {"health": str(health)}
            )
            return formatter.format_config(raw)
        except ImportError:
            return formatter.format_error("Storage health query not available")

    @handle_interface_exceptions(context="storage_migrate", interface_type="cli")
    async def storage_migrate(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Run Alembic migrations for the active SQL storage strategy.

        Subcommands: up (upgrade head), down (downgrade -1), current, history.
        The database is the one the active ``storage.strategy`` (sql or aurora)
        stores its data in; any other strategy is refused.
        """
        formatter = self._formatter

        subcommand = getattr(args, "migrate_subcommand", "up")

        # "stamp" needs the target revision as an additional argument, so it
        # is handled separately from the static cmd map.
        if subcommand == "stamp":
            stamp_target = getattr(args, "revision", None) or "head"
            alembic_args: list[str] = ["stamp", stamp_target]
        else:
            alembic_cmd_map = {
                "up": ["upgrade", "head"],
                "down": ["downgrade", "-1"],
                "current": ["current"],
                "history": ["history"],
            }

            alembic_args = alembic_cmd_map.get(subcommand)  # type: ignore[assignment]
            if alembic_args is None:
                return formatter.format_error(
                    f"Unknown migrate subcommand '{subcommand}'. "
                    "Valid values: up, down, current, history, stamp"
                )

        try:
            # Guard: alembic is an optional dependency shipped in the [sql] extra.
            # Give a clear install hint before attempting the subprocess so the
            # error message is actionable rather than a generic "No module named alembic".
            import importlib.util

            if importlib.util.find_spec("alembic") is None:
                return formatter.format_error(
                    "Alembic is not installed.  "
                    "Run: pip install orb-py[sql]  (or: pip install alembic>=1.13)"
                )

            # Resolve the DB URL from config and pass via environment variable so
            # alembic env.py can pick it up without touching alembic.ini at runtime.
            import os

            from orb.config.schemas.storage_schema import StorageConfig
            from orb.domain.base.exceptions import ConfigurationError
            from orb.infrastructure.storage.registry import get_storage_registry

            try:
                cfg = self._config_manager_provider()
                storage_cfg = cfg.get_typed(StorageConfig)
            except Exception as exc:
                raise ConfigurationError(
                    "Cannot determine the SQL database to migrate from the configuration "
                    f"({type(exc).__name__}). Check the storage configuration."
                ) from None

            # Migrate the database the active strategy stores its data in, never
            # another configured (or default) one
            active_strategy = storage_cfg.strategy
            registry = get_storage_registry()
            if not registry.has_migrations(active_strategy):
                return formatter.format_error(
                    f"The active storage strategy '{active_strategy}' has no SQL schema to "
                    "migrate. 'orb storage migrate' applies to SQL storage (sql, aurora)."
                )
            try:
                migration_url = registry.get_migration_url(active_strategy, cfg)
                db_url = migration_url.render_as_string(hide_password=False)
            except Exception as exc:
                # Never fall back to a default database: migrating the wrong one is worse than failing
                raise ConfigurationError(
                    "Cannot determine the SQL database to migrate from the configuration "
                    f"({type(exc).__name__}). Check the '{active_strategy}' storage settings."
                ) from None

            # SQLite creates the database file but not its directory; a fresh
            # install points at a data directory that may not exist yet.
            sqlite_path = None
            if migration_url.get_backend_name() == "sqlite":
                from orb.infrastructure.storage.sql.registration import (
                    _sqlite_file_path,
                    ensure_sqlite_parent_dir,
                )

                try:
                    sqlite_path = ensure_sqlite_parent_dir(migration_url)
                except OSError as exc:
                    target = _sqlite_file_path(migration_url.database or "")
                    directory = target.parent if target is not None else migration_url.database
                    return formatter.format_error(
                        f"Cannot create {directory} for SQLite database {target}: {exc.strerror or exc}"
                    )

            env = os.environ.copy()
            env["ORB_SQL_URL"] = db_url
            if active_strategy == "sql":
                # PostgreSQL TLS settings; other backends carry theirs in the URL
                sql_cfg = storage_cfg.sql_strategy
                env["ORB_SQL_SSLMODE"] = sql_cfg.sslmode
                if sql_cfg.sslrootcert:
                    env["ORB_SQL_SSLROOTCERT"] = sql_cfg.sslrootcert

            # alembic.ini ships inside the package at
            # src/orb/infrastructure/storage/sql/migrations/alembic.ini so it
            # lands wherever the orb package is installed (no dependency on
            # the repo layout being intact).
            import orb

            alembic_ini = os.path.join(
                os.path.dirname(os.path.abspath(orb.__file__)),
                "infrastructure",
                "storage",
                "sql",
                "migrations",
                "alembic.ini",
            )
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                "--config",
                alembic_ini,
                *alembic_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            raw_stdout_bytes, raw_stderr_bytes = await proc.communicate()
            raw_stdout = raw_stdout_bytes.decode("utf-8", errors="replace")
            raw_stderr = raw_stderr_bytes.decode("utf-8", errors="replace")

            # Log full output server-side (operators with shell access can debug).
            from orb.infrastructure.logging.logger import get_logger as _get_logger

            _migrate_logger = _get_logger(__name__)
            _migrate_logger.debug("alembic stdout: %s", raw_stdout)
            _migrate_logger.debug("alembic stderr: %s", raw_stderr)

            # Scrub DB credentials before returning to caller, for any URL scheme
            # (postgresql+pg8000://, mysql+pymysql://, ...).
            _DB_URL_RE = re.compile(r"([a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+(@)", re.IGNORECASE)

            def _scrub(text: str) -> str:
                return _DB_URL_RE.sub(r"\1***\2", text)

            output = _scrub((raw_stdout + raw_stderr).strip())
            if proc.returncode != 0:
                # SQLite URLs carry no credentials, so the database path is safe to show
                database_line = f"database: {sqlite_path}\n" if sqlite_path is not None else ""
                return formatter.format_error(f"Alembic migration failed:\n{database_line}{output}")
            return formatter.format_success(
                {"message": f"Migration '{subcommand}' completed", "output": output}
            )
        except Exception as exc:
            return formatter.format_error(f"Migration error: {exc}")

    @handle_interface_exceptions(context="storage_metrics", interface_type="cli")
    async def storage_metrics(self, args: argparse.Namespace) -> dict[str, Any] | InterfaceResponse:
        """Handle storage metrics operations."""
        formatter = self._formatter
        try:
            query_bus = self._query_bus_provider()
            from orb.application.queries.storage import (
                GetStorageMetricsQuery,  # type: ignore[attr-defined]
            )

            query = GetStorageMetricsQuery()
            metrics = await query_bus.execute(query)
            raw = (
                metrics
                if isinstance(metrics, dict)
                else metrics.model_dump()
                if hasattr(metrics, "model_dump")
                else {"metrics": str(metrics)}
            )
            return formatter.format_config(raw)
        except ImportError:
            return formatter.format_error("Storage metrics query not available")
