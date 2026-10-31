"""SQL storage strategy implementation using componentized architecture."""

import time
from contextlib import contextmanager
from typing import Any, Optional

from sqlalchemy import MetaData, Table, column as sa_column, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from orb.application.ports.exceptions import RepositoryQueryError
from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.logging.logger import get_logger
from orb.infrastructure.storage.base.strategy import BaseStorageStrategy

# Import components
from orb.infrastructure.storage.components import (
    LockManager,
    SQLConnectionManager,
    SQLQueryBuilder,
    SQLSerializer,
)
from orb.infrastructure.storage.exceptions import StorageError


def _is_duplicate_key(exc: IntegrityError) -> bool:
    """Whether an integrity error is a unique / primary-key violation."""
    orig = exc.orig
    if getattr(orig, "pgcode", None) == "23505" or type(orig).__name__ == "UniqueViolation":
        return True
    args = getattr(orig, "args", ())
    if args and args[0] == 1062:  # MySQL ER_DUP_ENTRY
        return True
    message = str(orig)
    return "UNIQUE constraint failed" in message or "PRIMARY KEY" in message


def _is_already_exists(exc: Exception) -> bool:
    """Whether a DDL error means the object was created by someone else first."""
    orig = getattr(exc, "orig", None)
    if getattr(orig, "pgcode", None) in ("42P07", "42710"):  # duplicate table / object
        return True
    args = getattr(orig, "args", ())
    if args and args[0] in (1050, 1061):  # MySQL ER_TABLE_EXISTS_ERROR / ER_DUP_KEYNAME
        return True
    return "already exists" in str(orig or exc).lower()


def _is_pg_type_race(exc: Exception) -> bool:
    """Postgres: two sessions creating the same table collide on its row type in pg_type."""
    message = str(getattr(exc, "orig", None) or exc)
    return "pg_type" in message and "typname" in message


class SQLStorageStrategy(BaseStorageStrategy):
    """
    SQL storage strategy using componentized architecture.

    Orchestrates components for database connections, query building,
    serialization, and locking. Reduced from 769 lines to ~200 lines.
    """

    def __init__(self, config: dict[str, Any], table_name: str, columns: dict[str, str]) -> None:
        """
        Initialize SQL storage strategy with components.

        Args:
            config: Database configuration
            table_name: Name of the database table
            columns: Column definitions (name -> type)
        """
        super().__init__()

        self.table_name = table_name
        self.columns = columns
        self.logger = get_logger(__name__)

        # Initialize components
        self.connection_manager = SQLConnectionManager(config)
        self.query_builder = SQLQueryBuilder(table_name, columns)
        self.serializer = SQLSerializer(id_column=self._get_id_column())
        self.lock_manager = LockManager("simple")  # Simple lock for SQL

        # Initialize database table
        self._initialize_table()

        self.logger.debug("Initialized SQL storage strategy for table %s", table_name)

    def is_healthy(self) -> tuple[bool, dict[str, Any]]:
        """Probe SQL: confirm connection works AND the configured table exists.

        Two cheap calls:
          - ``SELECT 1`` via the connection manager
          - ``table_exists(self.table_name)`` to catch schema-not-deployed
        """
        info = self.connection_manager.get_connection_info()
        details: dict[str, Any] = {
            "type": "sql",
            "database_type": info.get("database_type", "unknown"),
            "table": self.table_name,
        }
        if not info.get("healthy", False):
            details["reason"] = "connection manager reports unhealthy"
            return False, details
        try:
            table_present = self.connection_manager.table_exists(self.table_name)
        except Exception as exc:
            details["error"] = f"table_exists check failed: {exc}"
            return False, details
        details["table_exists"] = table_present
        if not table_present:
            details["reason"] = "configured table does not exist"
            return False, details
        return True, details

    def _get_id_column(self) -> str:
        """Get the primary key column name.

        A column declared ``PRIMARY KEY`` wins. Tables defined by the ORM models
        (requests, machines, templates) declare their key on the model, and their
        column dicts carry no key marker, so the model's primary key is used.
        Anything else falls back to ``id``.
        """
        for column_name, column_type in self.columns.items():
            if "PRIMARY KEY" in column_type.upper():
                return column_name

        from orb.infrastructure.storage.sql.models import Base

        orm_table = Base.metadata.tables.get(self.table_name)
        if orm_table is not None:
            key_columns = [c.name for c in orm_table.primary_key.columns]
            if len(key_columns) == 1:
                return key_columns[0]
        return "id"  # Default fallback

    def _initialize_table(self) -> None:
        """Initialize database tables.

        For tables that are defined in the ORM (requests, machines, templates)
        ``Base.metadata.create_all`` is used — this is the authoritative DDL path.

        Pre-existing SQL installs (tables present but no ``alembic_version``
        row) are auto-stamped at head so Alembic knows the current schema
        position without re-running migrations.  This only fires once on first
        boot after the upgrade; subsequent starts find the version row and skip
        the stamp.

        For any other table name (e.g. ad-hoc tables used in tests or generic
        storage) the legacy column-dict driven ``build_create_table`` path is
        used as a fallback so existing behaviour is preserved.
        """
        try:
            from orb.infrastructure.storage.sql.models import Base

            engine = self.connection_manager.get_engine()
            orm_tables = set(Base.metadata.tables.keys())

            if self.table_name in orm_tables:
                # Check before create_all whether this is a pre-existing install
                # without Alembic version tracking so we can stamp it afterwards.
                alembic_version_exists = self.connection_manager.table_exists("alembic_version")
                tables_already_exist = self.connection_manager.table_exists(self.table_name)

                self._create_schema(engine, Base.metadata)
                self.logger.debug(
                    "Applied Base.metadata.create_all for ORM table %s", self.table_name
                )

                # Auto-stamp head for pre-existing installs that have real data
                # tables but have never been managed by Alembic.  Do NOT stamp
                # when alembic_version already exists — that would overwrite a
                # legitimate mid-migration state.
                if tables_already_exist and not alembic_version_exists:
                    self._auto_stamp_head(engine)

            # Fallback: build CREATE TABLE from the column dict (legacy path).
            elif not self.connection_manager.table_exists(self.table_name):
                create_table_sql = self.query_builder.build_create_table()
                self.connection_manager.execute_query(create_table_sql)
                self.logger.info("Created non-ORM table via column-dict DDL: %s", self.table_name)
        except Exception as e:
            self.logger.error("Failed to initialize table %s: %s", self.table_name, e)
            raise

    _SCHEMA_ATTEMPTS = 5

    def _create_schema(self, engine: Any, metadata: Any) -> None:
        """Create the ORM tables one by one; tables a concurrent first start made are fine.

        Each table is created with an existence check, and an "already exists"
        failure (another process won the race for that table) is retried, which
        then finds the table and does nothing. Only a bounded number of attempts
        is made per table, and any other error is raised.
        """
        for table in metadata.sorted_tables:
            for attempt in range(1, self._SCHEMA_ATTEMPTS + 1):
                try:
                    table.create(engine, checkfirst=True)
                    break
                except (OperationalError, ProgrammingError, IntegrityError) as exc:
                    if not (_is_already_exists(exc) or _is_pg_type_race(exc)):
                        raise
                    if attempt == self._SCHEMA_ATTEMPTS:
                        raise
                    self.logger.debug(
                        "Table %s was created concurrently (attempt %d): %s",
                        table.name,
                        attempt,
                        exc,
                    )
                    time.sleep(0.05 * attempt)

    def _auto_stamp_head(self, engine: Any) -> None:
        """Stamp the Alembic revision table at head for pre-existing installs.

        Called only when the application tables already exist but no
        ``alembic_version`` row is present — i.e. the database was created by
        a previous ``Base.metadata.create_all`` call that predates Alembic
        management.  Stamping records the current head revision without
        re-running any DDL, so subsequent ``alembic upgrade head`` runs are
        no-ops rather than failures.

        Race-safety: concurrent workers may all detect the missing
        ``alembic_version`` row and arrive here simultaneously.  To ensure
        exactly one worker inserts the revision row this method wraps the
        detection + insert in a serialised transaction:

        * SQLite — ``BEGIN IMMEDIATE`` acquires a RESERVED lock before the
                   SELECT, preventing any other writer from inserting between
                   the check and the INSERT.
        * PostgreSQL — the connection is set to ``SERIALIZABLE`` isolation so
                       a concurrent phantom insert is detected and the loser
                       rolls back and logs at INFO (not a failure).
        * Other dialects — best-effort: try the inline INSERT; let the
                           caller's exception handler catch duplicates.
        """
        try:
            import os

            import alembic.config
            import alembic.script

            # Resolve the head revision from the script directory so the
            # stamped value is always the current migration head — never a
            # hard-coded string that could go stale.
            alembic_ini = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "migrations",
                "alembic.ini",
            )
            cfg = alembic.config.Config(alembic_ini)
            cfg.set_main_option("sqlalchemy.url", str(engine.url))
            script_dir = alembic.script.ScriptDirectory.from_config(cfg)
            head_revision = script_dir.get_current_head()
            if head_revision is None:
                self.logger.warning(
                    "Cannot resolve Alembic head revision; skipping auto-stamp for table %s.",
                    self.table_name,
                )
                return

            dialect = engine.dialect.name.lower()

            # Use a fresh DBAPI connection for the stamp so we control the
            # transaction isolation level independently of the pooled
            # application connections.
            raw_conn = engine.raw_connection()
            try:
                raw_cursor = raw_conn.cursor()

                # Acquire a write-intent lock BEFORE the IF-NOT-EXISTS check so
                # that no other concurrent worker can insert between our check and
                # our INSERT.
                #
                # SQLite:     BEGIN IMMEDIATE upgrades to RESERVED lock so only
                #             one writer proceeds; others queue or get BUSY.
                # PostgreSQL: Set SERIALIZABLE isolation; a concurrent phantom
                #             INSERT will cause the loser's transaction to abort.
                # Other:      No explicit lock — best-effort serialisation via
                #             the database's default isolation.
                if dialect == "sqlite":
                    raw_cursor.execute("BEGIN IMMEDIATE")
                elif dialect in ("postgresql", "postgres"):
                    raw_conn.set_isolation_level(
                        # psycopg2 SERIALIZABLE constant
                        getattr(raw_conn, "ISOLATION_LEVEL_SERIALIZABLE", 4)
                    )
                    raw_cursor.execute("BEGIN")

                # Ensure alembic_version table exists (may be absent on very
                # old installs that pre-date even the table creation).
                raw_cursor.execute(
                    "CREATE TABLE IF NOT EXISTS alembic_version "
                    "(version_num VARCHAR(32) NOT NULL, "
                    "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                )

                # Re-check inside the lock: another worker may have already stamped.
                raw_cursor.execute("SELECT version_num FROM alembic_version LIMIT 1")
                existing = raw_cursor.fetchone()

                if existing is not None:
                    self.logger.info(
                        "Auto-stamp skipped for table %s: alembic_version already contains %s "
                        "(another worker stamped first).",
                        self.table_name,
                        existing[0],
                    )
                    raw_conn.rollback()
                    return

                raw_cursor.execute(
                    "INSERT INTO alembic_version (version_num) VALUES (?)"
                    if dialect == "sqlite"
                    else "INSERT INTO alembic_version (version_num) VALUES (%s)",
                    (head_revision,),
                )
                raw_conn.commit()
                raw_cursor.close()
            except Exception:
                raw_conn.rollback()
                raise
            finally:
                raw_conn.close()

            self.logger.info(
                "Auto-stamped Alembic revision %s at head for pre-existing install "
                "(tables existed without alembic_version row). "
                "Run 'orb storage migrate current' to verify.",
                head_revision,
            )
        except Exception as exc:
            # Stamping is best-effort: log the failure but do not abort startup.
            self.logger.warning(
                "Could not auto-stamp Alembic head for table %s: %s. "
                "Run 'orb storage migrate stamp head' manually.",
                self.table_name,
                exc,
            )

    # Repositories route aggregates through save_versioned when this is True
    supports_versioned_save = True

    def save(self, entity_id: str, data: dict[str, Any]) -> None:
        """Insert or update one entity unconditionally (last writer wins)."""
        self.save_versioned(entity_id, data, None)

    def save_versioned(
        self, entity_id: str, data: dict[str, Any], expected_version: Optional[int]
    ) -> int:
        """Save one entity and return the version written.

        With ``expected_version`` the row is updated only while its stored version
        still equals it (compare-and-swap in a single UPDATE); a row that changed
        or disappeared raises ``ConcurrencyError``. Without it (the entity was
        never loaded or saved) the write is an unconditional insert-or-update.
        """
        return self.save_versioned_batch([(entity_id, data, expected_version)])[0]

    def save_versioned_batch(
        self, items: list[tuple[str, dict[str, Any], Optional[int]]]
    ) -> list[int]:
        """Save several entities in one transaction; returns the versions written.

        Nothing is written when any item conflicts. A concurrent insert of the
        same new key is retried once as an update.
        """
        if not items:
            return []
        with self.lock_manager.write_lock():
            try:
                for attempt in (1, 2):
                    try:
                        with self.connection_manager.get_session() as session:
                            versions = [
                                self._write_one(session, entity_id, data, expected)
                                for entity_id, data, expected in items
                            ]
                            session.commit()
                        break
                    except IntegrityError as exc:
                        # Another writer inserted the same new key between our
                        # UPDATE (0 rows) and INSERT: redo as an update. Any other
                        # integrity failure (NOT NULL, foreign key) is a real error.
                        if attempt == 2 or not _is_duplicate_key(exc):
                            raise
                self.logger.debug("Saved %s entities", len(items))
                return versions
            except ConcurrencyError:
                self.logger.warning(
                    "Concurrency conflict on save for entities %s", [i[0] for i in items]
                )
                raise
            except Exception as e:
                self.logger.error("Failed to save %s entities: %s", len(items), e)
                raise StorageError(f"Failed to save entity {items[0][0]}: {e}")

    def _write_one(
        self, session: Any, entity_id: str, data: dict[str, Any], expected: Optional[int]
    ) -> int:
        """Write one entity in the caller's transaction; returns the version written."""
        id_column = self._get_id_column()
        has_version = "version" in self.columns
        local_version = data.get("version")
        if not isinstance(local_version, int):
            local_version = 0

        if expected is not None and has_version:
            # Every write strictly increases the stored version, even when this
            # copy's own counter is behind the baseline
            written = max(local_version, expected + 1)
            row = {**data, "version": written}
            query, params = self.query_builder.build_update(
                self.serializer.serialize_for_update(row),
                id_column,
                entity_id,
                expected_version=expected,
            )
            if session.execute(text(query), params).rowcount == 0:
                raise ConcurrencyError(
                    f"Concurrent write detected for entity '{entity_id}': the stored row "
                    f"is no longer at version {expected} (it was changed or deleted by "
                    f"another writer). Re-read the entity, re-apply your change, and retry.",
                    details={"entity_id": entity_id, "expected_version": expected},
                )
            return written

        # No baseline: insert-or-update without a version check
        written = local_version
        try:
            query, params = self.query_builder.build_update(
                self.serializer.serialize_for_update(data), id_column, entity_id
            )
            if has_version and "version = :version" in query:
                # Stored versions never go down: a freshly built aggregate (local
                # counter behind the row) still moves the row forward
                query = query.replace(
                    "version = :version",
                    "version = CASE WHEN COALESCE(version, 0) + 1 > :version "
                    "THEN COALESCE(version, 0) + 1 ELSE :version END",
                    1,
                )
            updated = session.execute(text(query), params).rowcount
            if updated and has_version:
                written = self._stored_version(session, id_column, entity_id, local_version)
        except ValueError:
            # Only the key was given, so there is nothing to update: just ensure the row exists
            exists_query, id_param = self.query_builder.build_exists(id_column)
            updated = int(
                session.execute(text(exists_query), {id_param: entity_id}).first() is not None
            )
        if updated == 0:
            query, params = self.query_builder.build_insert(
                self.serializer.serialize_for_insert(entity_id, data)
            )
            session.execute(text(query), params)
        return written

    def _stored_version(self, session: Any, id_column: str, entity_id: str, default: int) -> int:
        """Version currently stored for a row, read in the caller's transaction."""
        query, param_name = self.query_builder.build_select_by_id(id_column)
        row = session.execute(text(query), {param_name: entity_id}).first()
        if row is None:
            return default
        row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
        version = row_dict.get("version")
        return int(version) if version is not None else default

    def find_by_id(self, entity_id: str) -> Optional[dict[str, Any]]:
        """
        Find entity by ID.

        Args:
            entity_id: Entity identifier

        Returns:
            Entity data if found, None otherwise
        """
        with self.lock_manager.read_lock():
            try:
                query, param_name = self.query_builder.build_select_by_id(self._get_id_column())
                params = {param_name: entity_id}

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    row = result.fetchone()

                if row:
                    # Convert row to dictionary
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    self.logger.debug("Found entity: %s", entity_id)
                    return entity_data
                else:
                    self.logger.debug("Entity not found: %s", entity_id)
                    return None

            except Exception as e:
                self.logger.error("Failed to find entity %s: %s", entity_id, e)
                raise StorageError(f"Failed to find entity {entity_id}: {e}")

    def find_all(self) -> dict[str, dict[str, Any]]:
        """
        Find all entities.

        Returns:
            Dictionary of all entities keyed by ID
        """
        with self.lock_manager.read_lock():
            try:
                query = self.query_builder.build_select_all()

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query))
                    rows = result.fetchall()

                entities = {}
                id_column = self._get_id_column()

                for row in rows:
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    entity_id = entity_data.get(id_column)
                    if entity_id:
                        entities[str(entity_id)] = entity_data

                self.logger.debug("Loaded %s entities", len(entities))
                return entities

            except Exception as e:
                self.logger.error("Failed to load all entities: %s", e)
                raise StorageError(f"Failed to load all entities: {e}")

    def delete(self, entity_id: str) -> None:
        """
        Delete entity by ID.

        Args:
            entity_id: Entity identifier
        """
        with self.lock_manager.write_lock():
            try:
                query, param_name = self.query_builder.build_delete(self._get_id_column())
                params = {param_name: entity_id}

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    session.commit()

                    if result.rowcount == 0:
                        self.logger.warning("Entity not found for deletion: %s", entity_id)
                    else:
                        self.logger.debug("Deleted entity: %s", entity_id)

            except Exception as e:
                self.logger.error("Failed to delete entity %s: %s", entity_id, e)
                raise StorageError(f"Failed to delete entity {entity_id}: {e}")

    def _row_exists(self, entity_id: str) -> bool:
        """Whether a row with this primary key exists; errors propagate."""
        query, param_name = self.query_builder.build_exists(self._get_id_column())
        with self.connection_manager.get_session() as session:
            result = session.execute(text(query), {param_name: entity_id})
            return result.fetchone() is not None

    def exists(self, entity_id: str) -> bool:
        """
        Check if entity exists.

        Args:
            entity_id: Entity identifier

        Returns:
            True if entity exists, False otherwise
        """
        try:
            exists = self._row_exists(entity_id)
            self.logger.debug("Entity %s exists: %s", entity_id, exists)
            return exists

        except Exception as e:
            self.logger.error("Failed to check existence of entity %s: %s", entity_id, e)
            return False

    def find_by_criteria(self, criteria: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Find entities matching criteria.

        Args:
            criteria: Search criteria

        Returns:
            List of matching entities
        """
        with self.lock_manager.read_lock():
            try:
                prepared_criteria = self.serializer.prepare_criteria(criteria)
                query, params = self.query_builder.build_select_by_criteria(prepared_criteria)

                with self.connection_manager.get_session() as session:
                    result = session.execute(text(query), params)
                    rows = result.fetchall()

                entities = []
                for row in rows:
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    entity_data = self.serializer.deserialize_from_row(row_dict)
                    entities.append(entity_data)

                self.logger.debug("Found %s entities matching criteria", len(entities))
                return entities

            except Exception as e:
                self.logger.error("Failed to search entities: %s", e)
                raise StorageError(f"Failed to search entities: {e}")

    def save_batch(self, entities: dict[str, dict[str, Any]]) -> None:
        """Insert or update several entities unconditionally in one transaction."""
        self.save_versioned_batch([(eid, data, None) for eid, data in entities.items()])

    def delete_batch(self, entity_ids: list[str]) -> None:
        """
        Delete multiple entities in batch.

        Args:
            entity_ids: List of entity IDs to delete
        """
        with self.lock_manager.write_lock():
            try:
                query, param_name = self.query_builder.build_delete(self._get_id_column())

                with self.connection_manager.get_session() as session:
                    for entity_id in entity_ids:
                        params = {param_name: entity_id}
                        session.execute(text(query), params)
                    session.commit()

                self.logger.debug("Deleted batch of %s entities", len(entity_ids))

            except Exception as e:
                self.logger.error("Failed to delete batch: %s", e)
                raise StorageError(f"Failed to delete batch: {e}")

    def count_by_column(self, column: str) -> dict[str, int]:
        """Return ``{column_value: count}`` via a single SQL GROUP BY query.

        Used by the dashboard to get per-status (or per-provider-api) counts
        without loading every row into Python first.

        Falls back to an empty dict on any error so callers can degrade
        gracefully to the list-and-count slow path if needed.
        """
        # Validate the column against the strategy's registered columns dict
        # before building the query.  This is the only place untrusted strings
        # could ever enter the SQL build; rejecting unknown columns keeps the
        # query construction below restricted to identifiers we registered at
        # construction time.
        if column not in self.columns:
            raise StorageError(
                f"count_by_column: column {column!r} is not in the registered "
                f"schema for table {self.table_name!r}"
            )
        # Build the SELECT via SQLAlchemy Core constructs — no raw SQL string
        # interpolation.  The column object is created by name (validated
        # above) and the Table is reflected from MetaData by name (also
        # validated since self.table_name is a constructor-time constant).
        bucket = sa_column(column)
        table = Table(self.table_name, MetaData())
        stmt = (
            select(bucket.label("bucket"), func.count().label("cnt"))
            .select_from(table)
            .group_by(bucket)
        )
        with self.lock_manager.read_lock():
            try:
                with self.connection_manager.get_session() as session:
                    result = session.execute(stmt)
                    rows = result.fetchall()
                counts: dict[str, int] = {}
                for row in rows:
                    row_dict = dict(row._mapping) if hasattr(row, "_mapping") else dict(row)
                    key = str(row_dict.get("bucket") or "unknown")
                    counts[key] = int(row_dict.get("cnt", 0))
                return counts
            except Exception as exc:
                from sqlalchemy.exc import SQLAlchemyError

                if isinstance(exc, SQLAlchemyError):
                    raise RepositoryQueryError(str(exc)) from exc
                raise

    def begin_transaction(self) -> None:
        """Begin transaction (handled by session)."""
        self.logger.debug("Transaction begin (handled by session)")

    def commit_transaction(self) -> None:
        """Commit transaction (handled by session)."""
        self.logger.debug("Transaction commit (handled by session)")

    def rollback_transaction(self) -> None:
        """Rollback transaction (handled by session)."""
        self.logger.debug("Transaction rollback (handled by session)")

    @contextmanager
    def transaction(self):  # type: ignore[override]
        """Context manager for database transactions."""
        with self.connection_manager.get_session() as session:
            try:
                yield session
                session.commit()
            except Exception as e:
                session.rollback()
                self.logger.error("Transaction failed: %s", e)
                raise

    def count(self) -> int:
        """Count total entities in the table."""
        try:
            query = self.query_builder.build_count()
            with self.connection_manager.get_session() as session:
                result = session.execute(text(query))
                row = result.fetchone()
                return int(row[0]) if row else 0
        except Exception as e:
            self.logger.error("Failed to count entities: %s", e)
            return 0

    def cleanup(self) -> None:
        """Clean up resources."""
        self.connection_manager.close()
        self.logger.debug("Cleaned up SQL storage strategy for %s", self.table_name)
