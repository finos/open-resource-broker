"""find_by_criteria rejects operators a backend does not interpret.

The operator sets differ per backend. An operator outside a backend's set must
raise instead of being compared as a literal value, which would return nothing
or the wrong rows.
"""

from __future__ import annotations

import pytest

from orb.infrastructure.storage.exceptions import StorageError

pytestmark = pytest.mark.integration

BACKENDS = ["json", "sql", "dynamodb"]


def _supported(backend: str) -> tuple[str, ...]:
    if backend == "json":
        from orb.infrastructure.storage.json.strategy import SUPPORTED_OPERATORS
    elif backend == "sql":
        from orb.infrastructure.storage.components.sql_query_builder import SUPPORTED_OPERATORS
    else:
        from orb.providers.aws.storage.components.dynamodb_converter import SUPPORTED_OPERATORS
    return SUPPORTED_OPERATORS


def _seed(strategy) -> None:
    for entity_id, name in (("a", "alpha"), ("b", "beta"), ("c", "gamma")):
        strategy.save(entity_id, {"id": entity_id, "name": name})


def _ids(rows) -> set[str]:
    return {row["id"] for row in rows}


# The operator each backend interprets, one criterion that selects a set of rows
# different from comparing the field to the operator dict by equality.
EXPECTED = {
    "json": {"$in": ({"$in": ["alpha", "gamma"]}, {"a", "c"}), "$regex": ({"$regex": "^b"}, {"b"})},
    "sql": {"$in": ({"$in": ["alpha", "gamma"]}, {"a", "c"}), "$like": ({"$like": "%mm%"}, {"c"})},
    "dynamodb": {
        "$eq": ({"$eq": "beta"}, {"b"}),
        "$ne": ({"$ne": "beta"}, {"a", "c"}),
        "$in": ({"$in": ["alpha", "gamma"]}, {"a", "c"}),
        "$gt": ({"$gt": "alpha"}, {"b", "c"}),
        "$gte": ({"$gte": "beta"}, {"b", "c"}),
        "$lt": ({"$lt": "beta"}, {"a"}),
        "$lte": ({"$lte": "beta"}, {"a", "b"}),
        "$contains": ({"$contains": "lph"}, {"a"}),
        "$begins_with": ({"$begins_with": "ga"}, {"c"}),
    },
}

CASES = [(b, op) for b in BACKENDS for op in EXPECTED[b]]


@pytest.mark.parametrize("backend", BACKENDS)
def test_expected_cases_cover_the_supported_set(backend) -> None:
    assert set(EXPECTED[backend]) == set(_supported(backend))


@pytest.mark.parametrize(("backend", "operator"), CASES)
def test_every_supported_operator_is_interpreted(
    backend, operator, json_strategy, sql_strategy, dynamodb_strategy
) -> None:
    strategy = {"json": json_strategy, "sql": sql_strategy, "dynamodb": dynamodb_strategy}[backend]
    _seed(strategy)
    operand, expected = EXPECTED[backend][operator]

    assert _ids(strategy.find_by_criteria({"name": operand})) == expected


UNSUPPORTED = [
    ("json", {"$like": "x"}),
    ("json", {"$bogus": 1}),
    ("sql", {"$regex": "x"}),
    ("sql", {"$bogus": 1}),
    ("dynamodb", {"$regex": "x"}),
    ("dynamodb", {"$bogus": 1}),
]


@pytest.mark.parametrize(("backend", "operand"), UNSUPPORTED)
def test_unsupported_operator_raises(
    backend, operand, json_strategy, sql_strategy, dynamodb_strategy
) -> None:
    strategy = {"json": json_strategy, "sql": sql_strategy, "dynamodb": dynamodb_strategy}[backend]
    _seed(strategy)
    operator = next(iter(operand))

    with pytest.raises(StorageError, match="Unsupported criteria operator") as exc_info:
        strategy.find_by_criteria({"name": operand})

    assert operator in str(exc_info.value)


MULTIPLE = [
    ("json", {"$in": ["alpha"], "$regex": "a"}),
    ("sql", {"$in": ["alpha"], "$like": "a"}),
    ("dynamodb", {"$gt": "a", "$lt": "z"}),
]


@pytest.mark.parametrize(("backend", "operand"), MULTIPLE)
def test_multiple_operators_raise(
    backend, operand, json_strategy, sql_strategy, dynamodb_strategy
) -> None:
    strategy = {"json": json_strategy, "sql": sql_strategy, "dynamodb": dynamodb_strategy}[backend]
    _seed(strategy)

    with pytest.raises(StorageError, match="combine several operators"):
        strategy.find_by_criteria({"name": operand})


MIXED = [
    ("json", {"$in": ["alpha"], "x": 2}),
    ("sql", {"$in": ["alpha"], "x": 2}),
    ("dynamodb", {"$in": ["alpha"], "x": 2}),
]


@pytest.mark.parametrize(("backend", "operand"), MIXED)
def test_mixed_operator_and_literal_keys_raise(
    backend, operand, json_strategy, sql_strategy, dynamodb_strategy
) -> None:
    strategy = {"json": json_strategy, "sql": sql_strategy, "dynamodb": dynamodb_strategy}[backend]
    _seed(strategy)

    with pytest.raises(StorageError, match="mix operator and literal keys"):
        strategy.find_by_criteria({"name": operand})


def test_plain_dict_is_matched_by_equality_on_json(json_strategy) -> None:
    json_strategy.save("a", {"id": "a", "meta": {"x": "1"}})
    json_strategy.save("b", {"id": "b", "meta": {"x": "2"}})

    assert _ids(json_strategy.find_by_criteria({"meta": {"x": "1"}})) == {"a"}


def test_plain_dict_is_matched_by_equality_on_dynamodb(dynamodb_strategy) -> None:
    dynamodb_strategy.save("a", {"id": "a", "meta": {"x": "1"}})
    dynamodb_strategy.save("b", {"id": "b", "meta": {"x": "2"}})

    assert _ids(dynamodb_strategy.find_by_criteria({"meta": {"x": "1"}})) == {"a"}


def test_plain_dict_is_not_rejected_by_the_guard_on_sql(sql_strategy) -> None:
    """SQL cannot bind a dict value; that failure is not the guard's."""
    sql_strategy.save("a", {"id": "a", "name": "alpha"})

    with pytest.raises(StorageError) as exc_info:
        sql_strategy.find_by_criteria({"name": {"x": "1"}})

    message = str(exc_info.value)
    assert "Unsupported criteria operator" not in message
    assert "operator and literal" not in message


def test_sql_tuple_names_every_operator_the_query_builder_interprets() -> None:
    import inspect
    import re

    from orb.infrastructure.storage.components.sql_query_builder import (
        SUPPORTED_OPERATORS,
        SQLQueryBuilder,
    )

    source = inspect.getsource(SQLQueryBuilder.build_select_by_criteria)

    assert set(re.findall(r'"(\$\w+)"', source)) == set(SUPPORTED_OPERATORS)
