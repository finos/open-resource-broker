"""Unit tests for SQLQueryBuilder covering uncovered branches."""

import pytest

from orb.infrastructure.storage.components.sql_query_builder import SQLQueryBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COLS = {
    "id": "TEXT PRIMARY KEY",
    "name": "TEXT",
    "status": "TEXT",
    "version": "INTEGER",
}


def _builder() -> SQLQueryBuilder:
    return SQLQueryBuilder("items", _COLS)


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConstructorValidation:
    def test_invalid_table_name_raises(self) -> None:
        with pytest.raises(ValueError):
            SQLQueryBuilder("bad-table!", {"id": "TEXT"})

    def test_invalid_column_name_raises(self) -> None:
        with pytest.raises(ValueError):
            SQLQueryBuilder("valid_table", {"bad col": "TEXT"})

    def test_valid_identifiers_accepted(self) -> None:
        qb = SQLQueryBuilder("my_table", {"col1": "TEXT", "col_2": "INTEGER"})
        assert qb.table_name == "my_table"


# ---------------------------------------------------------------------------
# build_create_table
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildCreateTable:
    def test_contains_table_name(self) -> None:
        sql = _builder().build_create_table()
        assert "items" in sql

    def test_contains_all_columns(self) -> None:
        sql = _builder().build_create_table()
        for col in _COLS:
            assert col in sql

    def test_contains_create_if_not_exists(self) -> None:
        sql = _builder().build_create_table()
        assert "CREATE TABLE IF NOT EXISTS" in sql


# ---------------------------------------------------------------------------
# build_insert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildInsert:
    def test_known_columns_included(self) -> None:
        sql, params = _builder().build_insert({"id": "1", "name": "Alice", "version": 0})
        assert "id" in sql
        assert "name" in sql
        assert params["name"] == "Alice"

    def test_unknown_columns_filtered_out(self) -> None:
        sql, params = _builder().build_insert({"id": "1", "unknown_col": "x"})
        assert "unknown_col" not in sql
        assert "unknown_col" not in params

    def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_insert({})

    def test_only_unknown_columns_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_insert({"not_a_col": "x"})

    def test_uses_parameterized_values(self) -> None:
        sql, _ = _builder().build_insert({"id": "1", "name": "Bob"})
        assert ":id" in sql or ":name" in sql


# ---------------------------------------------------------------------------
# build_select_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSelectById:
    def test_generates_where_clause(self) -> None:
        sql, param_name = _builder().build_select_by_id("id")
        assert "WHERE id = :id" in sql
        assert param_name == "id"

    def test_custom_id_column(self) -> None:
        qb = SQLQueryBuilder("tbl", {"ref_id": "TEXT PRIMARY KEY", "val": "TEXT"})
        sql, param = qb.build_select_by_id("ref_id")
        assert "ref_id = :ref_id" in sql
        assert param == "ref_id"

    def test_invalid_id_column_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_select_by_id("bad-col!")


# ---------------------------------------------------------------------------
# build_select_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSelectAll:
    def test_contains_table_name(self) -> None:
        sql = _builder().build_select_all()
        assert "items" in sql
        assert "SELECT *" in sql


# ---------------------------------------------------------------------------
# build_update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildUpdate:
    def test_basic_update(self) -> None:
        sql, params = _builder().build_update({"name": "updated", "version": 2}, "id", "e1")
        assert "UPDATE items" in sql
        assert "WHERE id = :entity_id" in sql
        assert params["entity_id"] == "e1"
        assert params["name"] == "updated"

    def test_id_column_excluded_from_set(self) -> None:
        sql, params = _builder().build_update({"id": "e1", "name": "x"}, "id", "e1")
        # id should be in WHERE not in SET
        assert "id = :id" not in sql.split("WHERE")[0]
        assert "entity_id" in params

    def test_empty_data_after_filtering_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_update({"id": "e1"}, "id", "e1")

    def test_with_expected_version_adds_cas_predicate(self) -> None:
        sql, params = _builder().build_update(
            {"name": "y", "version": 3}, "id", "e1", expected_version=2
        )
        assert "AND version = :expected_version" in sql
        assert params["expected_version"] == 2

    def test_without_expected_version_no_cas(self) -> None:
        sql, _ = _builder().build_update({"name": "y", "version": 3}, "id", "e1")
        assert "AND version" not in sql

    def test_invalid_id_column_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_update({"name": "x"}, "bad-col!", "e1")


# ---------------------------------------------------------------------------
# build_delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildDelete:
    def test_generates_delete_statement(self) -> None:
        sql, param = _builder().build_delete("id")
        assert "DELETE FROM items" in sql
        assert "WHERE id = :id" in sql
        assert param == "id"

    def test_invalid_column_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_delete("bad col!")


# ---------------------------------------------------------------------------
# build_exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildExists:
    def test_generates_select_1_with_limit(self) -> None:
        sql, param = _builder().build_exists("id")
        assert "SELECT 1" in sql
        assert "LIMIT 1" in sql
        assert param == "id"


# ---------------------------------------------------------------------------
# build_count
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildCount:
    def test_generates_count_query(self) -> None:
        sql = _builder().build_count()
        assert "COUNT(*)" in sql
        assert "items" in sql


# ---------------------------------------------------------------------------
# build_select_by_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildSelectByCriteria:
    def test_empty_criteria_falls_back_to_select_all(self) -> None:
        sql, params = _builder().build_select_by_criteria({})
        assert "WHERE" not in sql
        assert params == {}

    def test_unknown_columns_fall_back_to_select_all(self) -> None:
        sql, _ = _builder().build_select_by_criteria({"ghost": "x"})
        assert "WHERE" not in sql

    def test_simple_equality_criteria(self) -> None:
        sql, params = _builder().build_select_by_criteria({"status": "running"})
        assert "WHERE" in sql
        assert "status = :status_eq" in sql
        assert params["status_eq"] == "running"

    def test_in_operator(self) -> None:
        sql, params = _builder().build_select_by_criteria({"status": {"$in": ["a", "b"]}})
        assert "IN" in sql
        assert "status_in_0" in params
        assert "status_in_1" in params

    def test_like_operator(self) -> None:
        sql, params = _builder().build_select_by_criteria({"name": {"$like": "Al%"}})
        assert "LIKE" in sql
        assert params["name_like"] == "Al%"

    def test_dict_without_known_operator_uses_equality(self) -> None:
        sql, _ = _builder().build_select_by_criteria({"status": {"$unknown": "val"}})
        assert "status = :status_eq" in sql

    def test_multiple_criteria_joined_with_and(self) -> None:
        sql, params = _builder().build_select_by_criteria({"status": "ok", "name": "Bob"})
        assert "AND" in sql
        assert len(params) == 2


# ---------------------------------------------------------------------------
# build_batch_insert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildBatchInsert:
    def test_generates_insert_for_all_items(self) -> None:
        data = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
        sql, params_list = _builder().build_batch_insert(data)
        assert "INSERT INTO items" in sql
        assert len(params_list) == 2

    def test_empty_data_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_batch_insert([])

    def test_unknown_columns_filtered(self) -> None:
        data = [{"id": "1", "ghost": "x"}]
        sql, params_list = _builder().build_batch_insert(data)
        assert "ghost" not in sql
        assert "ghost" not in params_list[0]

    def test_all_unknown_columns_raises(self) -> None:
        data = [{"totally_unknown": "x"}]
        with pytest.raises(ValueError):
            _builder().build_batch_insert(data)


# ---------------------------------------------------------------------------
# build_query dispatcher
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildQueryDispatcher:
    def test_dispatches_create_table(self) -> None:
        sql = _builder().build_query({"type": "CREATE_TABLE"})
        assert "CREATE TABLE" in sql

    def test_dispatches_select_all(self) -> None:
        sql = _builder().build_query({"type": "SELECT_ALL"})
        assert "SELECT *" in sql

    def test_dispatches_select_by_id(self) -> None:
        sql = _builder().build_query({"type": "SELECT_BY_ID", "id_column": "id"})
        assert "WHERE id = :id" in sql

    def test_dispatches_insert(self) -> None:
        sql = _builder().build_query({"type": "INSERT", "data": {"id": "1", "name": "x"}})
        assert "INSERT INTO" in sql

    def test_dispatches_update(self) -> None:
        sql = _builder().build_query(
            {"type": "UPDATE", "data": {"name": "y"}, "id_column": "id", "entity_id": "e1"}
        )
        assert "UPDATE items" in sql

    def test_dispatches_delete(self) -> None:
        sql = _builder().build_query({"type": "DELETE", "id_column": "id"})
        assert "DELETE FROM" in sql

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError):
            _builder().build_query({"type": "EXPLODE"})


# ---------------------------------------------------------------------------
# execute_query always raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteQuery:
    def test_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError):
            _builder().execute_query("SELECT 1")


# ---------------------------------------------------------------------------
# validate_query
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateQuery:
    def test_valid_select_passes(self) -> None:
        assert _builder().validate_query("SELECT * FROM t") is True

    def test_empty_string_fails(self) -> None:
        assert _builder().validate_query("") is False

    def test_whitespace_only_fails(self) -> None:
        assert _builder().validate_query("   ") is False

    def test_no_keyword_fails(self) -> None:
        assert _builder().validate_query("hello world") is False

    def test_unbalanced_quote_fails(self) -> None:
        assert _builder().validate_query("SELECT * FROM t WHERE name = 'open") is False

    def test_balanced_quotes_pass(self) -> None:
        assert _builder().validate_query("SELECT * FROM t WHERE name = 'ok'") is True


# ---------------------------------------------------------------------------
# build_read_query dispatch helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildReadQuery:
    def test_with_entity_id_uses_select_by_id(self) -> None:
        sql, _ = _builder().build_read_query(entity_id="e1", id_column="id")
        assert "WHERE id = :id" in sql

    def test_with_criteria_uses_select_by_criteria(self) -> None:
        sql, _ = _builder().build_read_query(criteria={"status": "active"})
        assert "WHERE" in sql

    def test_no_args_returns_select_all(self) -> None:
        sql, params = _builder().build_read_query()
        assert "WHERE" not in sql
        assert params == {}


# ---------------------------------------------------------------------------
# build_update_query and build_delete_query adapter methods
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAdapterMethods:
    def test_build_update_query_delegates(self) -> None:
        sql, params = _builder().build_update_query({"name": "z"}, "e1", id_column="id")
        assert "UPDATE items" in sql
        assert params["entity_id"] == "e1"

    def test_build_delete_query_delegates(self) -> None:
        sql, param = _builder().build_delete_query("e1", id_column="id")
        assert "DELETE FROM" in sql
        assert param == "id"

    def test_build_create_query_delegates(self) -> None:
        sql = _builder().build_create_query()
        assert "CREATE TABLE" in sql
