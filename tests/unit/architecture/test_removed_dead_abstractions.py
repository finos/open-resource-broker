"""Removed dead abstractions stay removed; the live unit-of-work classes stay importable."""

from __future__ import annotations

import importlib

import pytest


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    "module",
    [
        "orb.domain.base.decorators",
        "orb.application.machine.queries",
        "orb.application.value_objects",
    ],
)
def test_removed_module_is_not_importable(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


@pytest.mark.unit
@pytest.mark.architecture
def test_strategy_unit_of_work_is_gone() -> None:
    from orb.infrastructure import storage
    from orb.infrastructure.storage.base import unit_of_work

    assert not hasattr(unit_of_work, "StrategyUnitOfWork")
    assert not hasattr(storage, "StrategyUnitOfWork")


@pytest.mark.unit
@pytest.mark.architecture
@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("orb.infrastructure.storage.base.unit_of_work", "BaseUnitOfWork"),
        ("orb.infrastructure.storage", "BaseUnitOfWork"),
        ("orb.infrastructure.storage.json.unit_of_work", "JSONUnitOfWork"),
        ("orb.infrastructure.storage.sql.unit_of_work", "SQLUnitOfWork"),
        ("orb.providers.aws.storage.unit_of_work", "DynamoDBUnitOfWork"),
    ],
)
def test_live_unit_of_work_classes_still_import(module: str, name: str) -> None:
    assert hasattr(importlib.import_module(module), name)
