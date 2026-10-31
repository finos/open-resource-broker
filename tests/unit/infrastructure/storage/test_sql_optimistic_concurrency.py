"""
Unit tests for SQL-level optimistic concurrency control (OCC).

These tests verify the compare-and-swap predicate in SQLStorageStrategy.save()
and the version-predicate generation in SQLQueryBuilder.build_update().

All tests use SQLite :memory: — they are deterministic and have no I/O
dependencies beyond the in-process SQLite engine.
"""

import pytest
from sqlalchemy import text

from orb.domain.base.exceptions import ConcurrencyError
from orb.infrastructure.storage.components.sql_query_builder import (
    SQLQueryBuilder,
)
from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "name": "TEXT",
    "version": "INTEGER",
    "updated_at": "TEXT",
}


def _make_strategy() -> SQLStorageStrategy:
    """Construct an in-memory SQLite strategy with a versioned table."""
    return SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name="entities",
        columns=_COLUMNS,
    )


def _save_initial(strategy: SQLStorageStrategy, entity_id: str, version: int = 0) -> None:
    """Insert a brand-new entity (version starts at 0)."""
    strategy.save(entity_id, {"id": entity_id, "name": "initial", "version": version})


# ---------------------------------------------------------------------------
# Test group 1: SQLQueryBuilder.build_update CAS predicate
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildUpdateVersionPredicate:
    """build_update emits the correct SQL when expected_version is provided."""

    def setup_method(self):
        self.builder = SQLQueryBuilder(
            "t", {"id": "TEXT PRIMARY KEY", "name": "TEXT", "version": "INTEGER"}
        )

    def test_no_expected_version_omits_predicate(self):
        """Default (no expected_version) produces unconditional WHERE id = :entity_id."""
        sql, params = self.builder.build_update({"name": "x", "version": 1}, "id", "e1")
        assert "AND version" not in sql
        assert "expected_version" not in params
        assert "WHERE id = :entity_id" in sql

    def test_expected_version_adds_cas_predicate(self):
        """Providing expected_version adds AND version = :expected_version to WHERE."""
        sql, params = self.builder.build_update(
            {"name": "x", "version": 1}, "id", "e1", expected_version=0
        )
        assert "AND COALESCE(version, 0) = :expected_version" in sql
        assert params["expected_version"] == 0

    def test_expected_version_zero_is_valid(self):
        """expected_version=0 is a legitimate first-update guard (server_default row)."""
        sql, params = self.builder.build_update(
            {"name": "x", "version": 1}, "id", "e1", expected_version=0
        )
        assert params["expected_version"] == 0
        assert "AND COALESCE(version, 0) = :expected_version" in sql

    def test_version_column_included_in_set_clause(self):
        """The new version value appears in the SET clause."""
        sql, params = self.builder.build_update(
            {"name": "x", "version": 3}, "id", "e1", expected_version=2
        )
        assert "version = :version" in sql
        assert params["version"] == 3

    def test_none_expected_version_keeps_original_behaviour(self):
        """Explicitly passing None keeps original single-predicate WHERE."""
        sql, _ = self.builder.build_update(
            {"name": "x", "version": 1}, "id", "e1", expected_version=None
        )
        assert "AND version" not in sql


# ---------------------------------------------------------------------------
# Test group 2: INSERT path is unaffected
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInsertPathUnaffected:
    """New entities (INSERT) must not trigger ConcurrencyError."""

    def test_first_save_inserts_without_concurrency_check(self):
        """Saving a new entity with version=0 must succeed without any CAS logic."""
        strategy = _make_strategy()
        # No exception should be raised
        strategy.save("new-entity", {"id": "new-entity", "name": "initial", "version": 0})
        loaded = strategy.find_by_id("new-entity")
        assert loaded is not None

    def test_version_zero_insert_roundtrips(self):
        """Version is stored and retrieved correctly on the happy INSERT path."""
        strategy = _make_strategy()
        strategy.save("e1", {"id": "e1", "name": "alpha", "version": 0})
        data = strategy.find_by_id("e1")
        assert data is not None
        assert data.get("version") == 0


# ---------------------------------------------------------------------------
# Test group 3: Version increments on successful save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVersionIncrementsOnSave:
    """Each successful save bumps the version stored in the DB."""

    def test_version_increments_single_writer(self):
        """Sequential single-writer saves bump the version column each time."""
        strategy = _make_strategy()

        # First write: new entity with version 0
        strategy.save("e1", {"id": "e1", "name": "v0", "version": 0})

        # Second write: aggregate mutation increments to 1
        strategy.save("e1", {"id": "e1", "name": "v1", "version": 1})
        data = strategy.find_by_id("e1")
        assert data is not None
        assert data.get("version") == 1

    def test_multiple_sequential_saves_track_version(self):
        """Five sequential saves correctly advance the version to 4."""
        strategy = _make_strategy()
        strategy.save("e1", {"id": "e1", "name": "v0", "version": 0})
        for v in range(1, 5):
            strategy.save("e1", {"id": "e1", "name": f"v{v}", "version": v})
        data = strategy.find_by_id("e1")
        assert data is not None
        assert data.get("version") == 4


# ---------------------------------------------------------------------------
# Test group 4: Stale-version write raises ConcurrencyError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStaleVersionRaisesConcurrencyError:
    """A save made from an out-of-date baseline raises ConcurrencyError."""

    def test_second_stale_save_raises(self):
        """
        Two writers both loaded version 0. Writer A saves first (0 -> 1).
        Writer B still holds baseline 0, so its conditional UPDATE matches no row.
        """
        strategy = _make_strategy()
        _save_initial(strategy, "e1", version=0)

        assert strategy.save_versioned("e1", {"id": "e1", "name": "writer-a", "version": 1}, 0) == 1

        with pytest.raises(ConcurrencyError) as exc_info:
            strategy.save_versioned("e1", {"id": "e1", "name": "writer-b", "version": 1}, 0)

        assert "e1" in str(exc_info.value)
        data = strategy.find_by_id("e1")
        assert data is not None
        assert data.get("name") == "writer-a"
        assert data.get("version") == 1

    def test_concurrency_error_includes_entity_id(self):
        strategy = _make_strategy()
        _save_initial(strategy, "some-entity", version=0)
        strategy.save_versioned("some-entity", {"id": "some-entity", "name": "a", "version": 1}, 0)

        with pytest.raises(ConcurrencyError) as exc_info:
            strategy.save_versioned(
                "some-entity", {"id": "some-entity", "name": "b", "version": 1}, 0
            )

        assert "some-entity" in str(exc_info.value)

    def test_concurrent_writes_only_one_wins(self):
        """Three writers hold baseline 0; only the first save lands."""
        strategy = _make_strategy()
        _save_initial(strategy, "shared", version=0)

        strategy.save_versioned("shared", {"id": "shared", "name": "winner", "version": 1}, 0)

        for loser_name in ("loser-2", "loser-3"):
            with pytest.raises(ConcurrencyError):
                strategy.save_versioned(
                    "shared", {"id": "shared", "name": loser_name, "version": 1}, 0
                )

        data = strategy.find_by_id("shared")
        assert data is not None
        assert data.get("name") == "winner"
        assert data.get("version") == 1

    def test_several_in_memory_mutations_between_saves_are_fine(self):
        """The baseline is the stored version, not the local counter minus one."""
        strategy = _make_strategy()
        _save_initial(strategy, "multi", version=0)

        written = strategy.save_versioned("multi", {"id": "multi", "name": "v3", "version": 3}, 0)

        assert written == 3
        assert strategy.find_by_id("multi")["version"] == 3  # type: ignore[index]

    def test_a_lagging_counter_still_moves_the_stored_version_forward(self):
        strategy = _make_strategy()
        _save_initial(strategy, "lag", version=4)

        written = strategy.save_versioned("lag", {"id": "lag", "name": "x", "version": 3}, 4)

        assert written == 5
        assert strategy.find_by_id("lag")["version"] == 5  # type: ignore[index]

    def test_row_deleted_since_load_conflicts(self):
        strategy = _make_strategy()
        _save_initial(strategy, "gone", version=0)
        strategy.delete("gone")

        with pytest.raises(ConcurrencyError):
            strategy.save_versioned("gone", {"id": "gone", "name": "x", "version": 1}, 0)

        assert strategy.find_by_id("gone") is None

    def test_null_version_row_counts_as_zero(self):
        strategy = _make_strategy()
        with strategy.connection_manager.get_session() as session:
            session.execute(
                text("INSERT INTO entities (id, name, version) VALUES ('legacy', 'old', NULL)")
            )
            session.commit()

        assert (
            strategy.save_versioned("legacy", {"id": "legacy", "name": "new", "version": 1}, 0) == 1
        )

    def test_without_a_baseline_the_write_is_an_unconditional_upsert(self):
        """A never-loaded aggregate (e.g. a machine discovered twice) must not conflict."""
        strategy = _make_strategy()

        strategy.save_versioned("d1", {"id": "d1", "name": "first", "version": 0}, None)
        strategy.save_versioned("d1", {"id": "d1", "name": "second", "version": 0}, None)

        assert strategy.find_by_id("d1")["name"] == "second"  # type: ignore[index]

    def test_batch_is_all_or_nothing(self):
        strategy = _make_strategy()
        _save_initial(strategy, "a", version=0)
        _save_initial(strategy, "b", version=0)
        strategy.save_versioned("b", {"id": "b", "name": "moved", "version": 1}, 0)

        with pytest.raises(ConcurrencyError):
            strategy.save_versioned_batch(
                [
                    ("a", {"id": "a", "name": "changed", "version": 1}, 0),
                    ("b", {"id": "b", "name": "stale", "version": 1}, 0),
                ]
            )

        assert strategy.find_by_id("a")["name"] == "initial"  # type: ignore[index]
        assert strategy.find_by_id("b")["name"] == "moved"  # type: ignore[index]

    def test_plain_save_is_a_last_writer_wins_upsert(self):
        strategy = _make_strategy()
        _save_initial(strategy, "lw", version=0)
        strategy.save("lw", {"id": "lw", "name": "one", "version": 1})
        strategy.save("lw", {"id": "lw", "name": "two", "version": 1})

        assert strategy.find_by_id("lw")["name"] == "two"  # type: ignore[index]

    def test_non_versioned_table_save_never_raises(self):
        """
        A table without a ``version`` column in its schema must never raise
        ConcurrencyError — the opt-in CAS logic must not fire.
        """
        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": ":memory:"},
            table_name="unversioned",
            columns={"id": "TEXT PRIMARY KEY", "val": "TEXT"},
        )
        strategy.save("u1", {"id": "u1", "val": "first"})
        # Unconditional overwrite — no ConcurrencyError
        strategy.save("u1", {"id": "u1", "val": "second"})
        data = strategy.find_by_id("u1")
        assert data is not None
        assert data.get("val") == "second"

    def test_happy_path_sequential_saves_never_raise(self):
        """
        The single-writer happy path must not raise ConcurrencyError at any
        version — the CAS predicate must match on every sequential write.
        """
        strategy = _make_strategy()
        _save_initial(strategy, "seq", version=0)
        for v in range(1, 10):
            # Each save correctly carries the incremented version.
            strategy.save("seq", {"id": "seq", "name": f"step-{v}", "version": v})
        data = strategy.find_by_id("seq")
        assert data is not None
        assert data.get("version") == 9
