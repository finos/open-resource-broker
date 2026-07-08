"""Tests for SQLQueryBuilder component.

Verifies that SQLQueryBuilder can be instantiated and that every abstract method
declared on QueryManager is overridden and behaves correctly.
"""

import pytest

from orb.infrastructure.storage.components.resource_manager import QueryManager
from orb.infrastructure.storage.components.sql_query_builder import QueryType, SQLQueryBuilder

# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "name": "TEXT",
    "status": "TEXT",
    "created_at": "TEXT",
}


@pytest.fixture()
def builder() -> SQLQueryBuilder:
    """Return a fully-initialised SQLQueryBuilder for a 'resources' table."""
    return SQLQueryBuilder("resources", COLUMNS)


# ---------------------------------------------------------------------------
# Abstract-contract tests
# ---------------------------------------------------------------------------


class TestQueryManagerAbstractContract:
    """Verify the abstract/concrete relationship between QueryManager and SQLQueryBuilder."""

    def test_query_manager_is_abstract(self) -> None:
        """QueryManager cannot be instantiated directly."""
        with pytest.raises(TypeError):
            QueryManager()  # type: ignore[abstract]

    def test_sql_query_builder_is_instantiable(self, builder: SQLQueryBuilder) -> None:
        """SQLQueryBuilder can be instantiated without TypeError."""
        # If any abstract method were still abstract this line would raise
        # TypeError: Can't instantiate abstract class …
        assert builder is not None

    def test_sql_query_builder_is_a_query_manager(self, builder: SQLQueryBuilder) -> None:
        """SQLQueryBuilder is a proper subtype of QueryManager."""
        assert isinstance(builder, QueryManager)

    def test_build_query_is_implemented(self, builder: SQLQueryBuilder) -> None:
        """build_query is a concrete method (not abstract)."""
        assert not getattr(builder.build_query, "__isabstractmethod__", False)

    def test_execute_query_is_implemented(self, builder: SQLQueryBuilder) -> None:
        """execute_query is overridden on SQLQueryBuilder."""
        assert not getattr(builder.execute_query, "__isabstractmethod__", False)

    def test_validate_query_is_implemented(self, builder: SQLQueryBuilder) -> None:
        """validate_query is a concrete method (not abstract)."""
        assert not getattr(builder.validate_query, "__isabstractmethod__", False)


# ---------------------------------------------------------------------------
# build_query — QueryManager dispatcher
# ---------------------------------------------------------------------------


class TestBuildQuery:
    """Tests for the QueryManager.build_query dispatch method."""

    def test_build_query_create_table(self, builder: SQLQueryBuilder) -> None:
        result = builder.build_query({"type": "CREATE_TABLE"})
        assert "CREATE TABLE IF NOT EXISTS resources" in result

    def test_build_query_select_all(self, builder: SQLQueryBuilder) -> None:
        result = builder.build_query({"type": "SELECT_ALL"})
        assert result == "SELECT * FROM resources"

    def test_build_query_select_by_id(self, builder: SQLQueryBuilder) -> None:
        result = builder.build_query({"type": "SELECT_BY_ID", "id_column": "id"})
        assert "WHERE id = :id" in result

    def test_build_query_insert(self, builder: SQLQueryBuilder) -> None:
        data = {"id": "x1", "name": "Alice", "status": "active", "created_at": "2024-01-01"}
        result = builder.build_query({"type": "INSERT", "data": data})
        assert "INSERT INTO resources" in result
        assert ":id" in result

    def test_build_query_update(self, builder: SQLQueryBuilder) -> None:
        data = {"name": "Bob", "status": "inactive"}
        result = builder.build_query(
            {"type": "UPDATE", "data": data, "id_column": "id", "entity_id": "x1"}
        )
        assert "UPDATE resources SET" in result
        assert "WHERE id = :entity_id" in result

    def test_build_query_delete(self, builder: SQLQueryBuilder) -> None:
        result = builder.build_query({"type": "DELETE", "id_column": "id"})
        assert "DELETE FROM resources WHERE id = :id" in result

    def test_build_query_unknown_type_raises_value_error(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="Unknown query type"):
            builder.build_query({"type": "NONSENSE"})

    def test_build_query_case_insensitive_type(self, builder: SQLQueryBuilder) -> None:
        """query_spec['type'] is uppercased before dispatch."""
        result = builder.build_query({"type": "select_all"})
        assert result == "SELECT * FROM resources"


# ---------------------------------------------------------------------------
# execute_query — intentionally raises NotImplementedError
# ---------------------------------------------------------------------------


class TestExecuteQuery:
    """execute_query satisfies the abstract slot but delegates to the connection manager."""

    def test_execute_query_raises_not_implemented_error(self, builder: SQLQueryBuilder) -> None:
        """SQLQueryBuilder is build-only; direct execution must be rejected."""
        with pytest.raises(NotImplementedError, match="SQLConnectionManager"):
            builder.execute_query("SELECT * FROM resources")

    def test_execute_query_raises_with_parameters(self, builder: SQLQueryBuilder) -> None:
        """execute_query rejects execution regardless of parameters."""
        with pytest.raises(NotImplementedError):
            builder.execute_query("SELECT * FROM resources WHERE id = :id", {"id": "x1"})


# ---------------------------------------------------------------------------
# validate_query — QueryManager.validate_query override
# ---------------------------------------------------------------------------


class TestValidateQuery:
    """Tests for the validate_query override."""

    def test_valid_select_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("SELECT * FROM resources") is True

    def test_valid_insert_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("INSERT INTO resources (id) VALUES (:id)") is True

    def test_valid_update_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("UPDATE resources SET name = :name WHERE id = :id") is True

    def test_valid_delete_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("DELETE FROM resources WHERE id = :id") is True

    def test_valid_create_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("CREATE TABLE IF NOT EXISTS resources (id TEXT)") is True

    def test_empty_string_is_invalid(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("") is False

    def test_whitespace_only_is_invalid(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("   ") is False

    def test_no_keyword_is_invalid(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("resources WHERE id = 1") is False

    def test_unbalanced_single_quote_is_invalid(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("SELECT * FROM resources WHERE name = 'unclosed") is False

    def test_balanced_quotes_are_valid(self, builder: SQLQueryBuilder) -> None:
        assert builder.validate_query("SELECT * FROM resources WHERE name = 'Alice'") is True


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


class TestSQLQueryBuilderInitialization:
    """Verify constructor-time validation and attribute setup."""

    def test_stores_table_name(self, builder: SQLQueryBuilder) -> None:
        assert builder.table_name == "resources"

    def test_stores_columns(self, builder: SQLQueryBuilder) -> None:
        assert "id" in builder.columns
        assert "name" in builder.columns

    def test_rejects_invalid_table_name(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            SQLQueryBuilder("bad-table!", {"id": "TEXT"})

    def test_rejects_invalid_column_name(self) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            SQLQueryBuilder("mytable", {"bad col": "TEXT"})

    def test_logger_is_set(self, builder: SQLQueryBuilder) -> None:
        assert builder.logger is not None


# ---------------------------------------------------------------------------
# Individual build methods
# ---------------------------------------------------------------------------


class TestBuildCreateTable:
    def test_contains_create_table_statement(self, builder: SQLQueryBuilder) -> None:
        sql = builder.build_create_table()
        assert "CREATE TABLE IF NOT EXISTS resources" in sql

    def test_includes_all_columns(self, builder: SQLQueryBuilder) -> None:
        sql = builder.build_create_table()
        for col in COLUMNS:
            assert col in sql


class TestBuildInsert:
    def test_returns_query_and_params(self, builder: SQLQueryBuilder) -> None:
        data = {"id": "u1", "name": "Alice", "status": "active", "created_at": "2024-01-01"}
        query, params = builder.build_insert(data)
        assert "INSERT INTO resources" in query
        assert params == data

    def test_filters_unknown_columns(self, builder: SQLQueryBuilder) -> None:
        data = {"id": "u1", "unknown_col": "value"}
        query, params = builder.build_insert(data)
        assert "unknown_col" not in query
        assert "unknown_col" not in params

    def test_raises_when_no_valid_columns(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="No valid columns"):
            builder.build_insert({"totally_unknown": "x"})


class TestBuildSelectById:
    def test_returns_query_and_param_name(self, builder: SQLQueryBuilder) -> None:
        query, param = builder.build_select_by_id("id")
        assert "WHERE id = :id" in query
        assert param == "id"

    def test_rejects_invalid_id_column(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            builder.build_select_by_id("bad col!")


class TestBuildSelectAll:
    def test_returns_select_star(self, builder: SQLQueryBuilder) -> None:
        assert builder.build_select_all() == "SELECT * FROM resources"


class TestBuildUpdate:
    def test_returns_query_and_params(self, builder: SQLQueryBuilder) -> None:
        data = {"name": "Bob", "status": "inactive"}
        query, params = builder.build_update(data, "id", "u1")
        assert "UPDATE resources SET" in query
        assert "WHERE id = :entity_id" in query
        assert params["entity_id"] == "u1"

    def test_excludes_id_column_from_set_clause(self, builder: SQLQueryBuilder) -> None:
        data = {"id": "u1", "name": "Bob"}
        query, _params = builder.build_update(data, "id", "u1")
        # The SET clause must not contain id = :id
        assert "SET id" not in query

    def test_raises_when_no_valid_update_columns(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="No valid columns"):
            builder.build_update({"unknown_col": "x"}, "id", "u1")


class TestBuildDelete:
    def test_returns_query_and_param_name(self, builder: SQLQueryBuilder) -> None:
        query, param = builder.build_delete("id")
        assert "DELETE FROM resources WHERE id = :id" in query
        assert param == "id"

    def test_rejects_invalid_id_column(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="Invalid SQL identifier"):
            builder.build_delete("bad col!")


class TestBuildExists:
    def test_returns_limit_1_query(self, builder: SQLQueryBuilder) -> None:
        query, param = builder.build_exists("id")
        assert "SELECT 1 FROM resources WHERE id = :id LIMIT 1" in query
        assert param == "id"


class TestBuildCount:
    def test_returns_count_star_query(self, builder: SQLQueryBuilder) -> None:
        assert builder.build_count() == "SELECT COUNT(*) FROM resources"


class TestBuildSelectByCriteria:
    def test_empty_criteria_returns_select_all(self, builder: SQLQueryBuilder) -> None:
        query, params = builder.build_select_by_criteria({})
        assert query == "SELECT * FROM resources"
        assert params == {}

    def test_simple_equality_criterion(self, builder: SQLQueryBuilder) -> None:
        query, params = builder.build_select_by_criteria({"status": "active"})
        assert "WHERE" in query
        assert "status = :status_eq" in query
        assert params["status_eq"] == "active"

    def test_in_operator(self, builder: SQLQueryBuilder) -> None:
        query, params = builder.build_select_by_criteria({"status": {"$in": ["active", "pending"]}})
        assert "IN" in query
        assert params["status_in_0"] == "active"
        assert params["status_in_1"] == "pending"

    def test_like_operator(self, builder: SQLQueryBuilder) -> None:
        query, params = builder.build_select_by_criteria({"name": {"$like": "Al%"}})
        assert "LIKE :name_like" in query
        assert params["name_like"] == "Al%"

    def test_unknown_columns_are_ignored(self, builder: SQLQueryBuilder) -> None:
        query, _params = builder.build_select_by_criteria({"unknown_col": "x"})
        # Falls back to SELECT all when all criteria columns are unknown
        assert query == "SELECT * FROM resources"


class TestBuildBatchInsert:
    def test_returns_query_and_params_list(self, builder: SQLQueryBuilder) -> None:
        data_list = [
            {"id": "u1", "name": "Alice", "status": "active", "created_at": "2024-01-01"},
            {"id": "u2", "name": "Bob", "status": "inactive", "created_at": "2024-01-02"},
        ]
        query, params_list = builder.build_batch_insert(data_list)
        assert "INSERT INTO resources" in query
        assert len(params_list) == 2

    def test_raises_on_empty_list(self, builder: SQLQueryBuilder) -> None:
        with pytest.raises(ValueError, match="No data provided"):
            builder.build_batch_insert([])


class TestQueryTypeEnum:
    """Verify the QueryType enum values used by build_query dispatch."""

    def test_enum_values(self) -> None:
        assert QueryType.SELECT == "SELECT"
        assert QueryType.INSERT == "INSERT"
        assert QueryType.UPDATE == "UPDATE"
        assert QueryType.DELETE == "DELETE"
        assert QueryType.CREATE_TABLE == "CREATE TABLE"
