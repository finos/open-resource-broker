"""Unit tests for StrategyBasedRepository (base/repository.py)."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.base.exceptions import ConcurrencyError, EntityNotFoundError
from orb.infrastructure.storage.base.repository import StrategyBasedRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _SimpleEntity:
    """Plain entity with .id attribute."""

    def __init__(self, id: str = "unknown", version: int = 0, **kwargs: Any) -> None:
        self.id = id
        self.version = version

    def get_domain_events(self) -> list:
        return []


class _RequestEntity:
    """Entity that uses request_id."""

    def __init__(self, request_id: str = "req-0", **kwargs: Any) -> None:
        self.request_id = request_id


class _MachineEntity:
    """Entity that uses machine_id."""

    def __init__(self, machine_id: str = "m-0", **kwargs: Any) -> None:
        self.machine_id = machine_id


class _EventEntity:
    """Entity with domain events."""

    def __init__(self, id: str = "ev-0") -> None:
        self.id = id
        self._events: list = []
        self.version = 0

    def get_domain_events(self) -> list:
        return list(self._events)

    def clear_domain_events(self) -> None:
        self._events.clear()


class _PydanticLike:
    """Entity with model_dump / model_validate."""

    def __init__(self, id: str = "pl-0", data: str = "hello") -> None:
        self.id = id
        self.data = data
        self.version = 0

    def model_dump(self) -> dict:
        return {"id": self.id, "data": self.data, "version": self.version}

    @classmethod
    def model_validate(cls, d: dict) -> "_PydanticLike":
        return cls(d["id"], d.get("data", ""))


class _ToDictEntity:
    """Entity with to_dict / from_dict."""

    def __init__(self, id: str = "td-0") -> None:
        self.id = id
        self.version = 0

    def to_dict(self) -> dict:
        return {"id": self.id, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "_ToDictEntity":
        return cls(d["id"])


def _make_storage(**kwargs) -> MagicMock:
    s = MagicMock()
    s.find_by_id.return_value = kwargs.get("find_by_id", None)
    s.find_all.return_value = kwargs.get("find_all", {})
    s.find_by_criteria.return_value = kwargs.get("find_by_criteria", [])
    s.exists.return_value = kwargs.get("exists", False)
    return s


def _make_repo(entity_class=None, storage=None, event_bus=None):  # type: ignore[assignment]
    if entity_class is None:
        entity_class = _SimpleEntity
    if storage is None:
        storage = _make_storage()
    return StrategyBasedRepository(entity_class, storage, event_bus), storage


# ---------------------------------------------------------------------------
# _get_entity_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEntityId:
    def test_uses_id_attribute(self) -> None:
        repo, _ = _make_repo()
        entity = _SimpleEntity(id="abc")
        assert repo._get_entity_id(entity) == "abc"

    def test_uses_request_id(self) -> None:
        repo, _ = _make_repo(_RequestEntity)
        entity = _RequestEntity(request_id="req-123")
        assert repo._get_entity_id(entity) == "req-123"

    def test_uses_machine_id(self) -> None:
        repo, _ = _make_repo(_MachineEntity)
        entity = _MachineEntity(machine_id="m-456")
        assert repo._get_entity_id(entity) == "m-456"

    def test_raises_when_no_id(self) -> None:
        repo, _ = _make_repo()

        class _NoId:
            pass

        with pytest.raises(ValueError, match="Cannot determine ID"):
            repo._get_entity_id(_NoId())


# ---------------------------------------------------------------------------
# _to_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToDict:
    def test_uses_model_dump(self) -> None:
        repo, _ = _make_repo(_PydanticLike)
        entity = _PydanticLike(id="e1", data="world")
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            result = repo._to_dict(entity)
        assert result["id"] == "e1"
        assert result["data"] == "world"

    def test_uses_to_dict_fallback(self) -> None:
        repo, _ = _make_repo(_ToDictEntity)
        entity = _ToDictEntity(id="e2")
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            result = repo._to_dict(entity)
        assert result["id"] == "e2"

    def test_uses_vars_fallback(self) -> None:
        repo, _ = _make_repo()
        entity = _SimpleEntity(id="e3", version=7)
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            result = repo._to_dict(entity)
        assert result["id"] == "e3"
        assert result["version"] == 7


# ---------------------------------------------------------------------------
# _from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFromDict:
    def test_uses_model_validate(self) -> None:
        repo, _ = _make_repo(_PydanticLike)
        entity = repo._from_dict({"id": "e1", "data": "foo"})
        assert entity.id == "e1"

    def test_uses_from_dict_fallback(self) -> None:
        repo, _ = _make_repo(_ToDictEntity)
        entity = repo._from_dict({"id": "td1"})
        assert entity.id == "td1"

    def test_uses_constructor_as_last_resort(self) -> None:
        repo, _ = _make_repo(_SimpleEntity)
        entity = repo._from_dict({"id": "cx", "version": 0})
        assert entity.id == "cx"

    def test_pydantic_validation_error_becomes_value_error(self) -> None:
        from pydantic import BaseModel

        class _Strict(BaseModel):
            id: int  # int, not str — will fail on "not-a-number"

        repo, _ = _make_repo(_Strict)
        with pytest.raises(ValueError, match="Validation error"):
            repo._from_dict({"id": "not-a-number"})


# ---------------------------------------------------------------------------
# _get_entity_id_from_dict
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEntityIdFromDict:
    def test_id_key(self) -> None:
        repo, _ = _make_repo()
        assert repo._get_entity_id_from_dict({"id": "x"}) == "x"

    def test_request_id_key(self) -> None:
        repo, _ = _make_repo()
        assert repo._get_entity_id_from_dict({"request_id": "r1"}) == "r1"

    def test_machine_id_key(self) -> None:
        repo, _ = _make_repo()
        assert repo._get_entity_id_from_dict({"machine_id": "m1"}) == "m1"

    def test_unknown_keys_raise(self) -> None:
        repo, _ = _make_repo()
        with pytest.raises(ValueError, match="Cannot determine ID"):
            repo._get_entity_id_from_dict({"foo": "bar"})


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSave:
    def test_save_calls_storage_strategy(self) -> None:
        repo, storage = _make_repo()
        entity = _SimpleEntity(id="e1")
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)
        storage.save.assert_called_once()

    def test_save_populates_cache(self) -> None:
        repo, _ = _make_repo()
        entity = _SimpleEntity(id="e5")
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)
        assert "e5" in repo._cache

    def test_save_version_conflict_raises_concurrency_error(self) -> None:
        repo, _ = _make_repo()
        repo._version_map["e1"] = 3
        entity = _SimpleEntity(id="e1", version=1)  # does not match stored 3
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            with pytest.raises(ConcurrencyError):
                repo.save(entity)

    def test_save_no_version_conflict_when_versions_match(self) -> None:
        repo, _ = _make_repo()
        repo._version_map["e1"] = 2
        entity = _SimpleEntity(id="e1", version=2)
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)  # should not raise
        assert "e1" in repo._cache

    def test_save_with_sync_event_bus_publishes_events(self) -> None:
        bus = MagicMock()
        bus.publish = MagicMock()
        # Make publish a sync function (not a coroutine)
        repo, _ = _make_repo(event_bus=bus)
        entity = _EventEntity(id="ex")
        sentinel = object()
        entity._events.append(sentinel)
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)
        bus.publish.assert_called_once_with(sentinel)

    def test_save_with_events_list_backward_compat(self) -> None:
        """entities_list attribute (legacy) events are collected."""
        bus = MagicMock()

        class _LegacyEntity:
            def __init__(self) -> None:
                self.id = "leg1"
                self.version = 0
                self.events_list = ["ev1"]

        repo, _ = _make_repo(event_bus=bus)
        entity = _LegacyEntity()
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)
        bus.publish.assert_called_once_with("ev1")

    def test_save_with_clear_events_backward_compat(self) -> None:
        """clear_events() return value is cached."""
        bus = MagicMock()
        cleared = object()

        class _ClearEntity:
            def __init__(self) -> None:
                self.id = "cl1"
                self.version = 0

            def get_domain_events(self) -> list:
                return ["ev"]

            def clear_events(self):
                return cleared

        repo, _ = _make_repo(event_bus=bus)
        entity = _ClearEntity()
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)
        assert repo._cache["cl1"] is cleared

    def test_save_pydantic_validation_error_raises_value_error(self) -> None:

        storage = _make_storage()
        storage.save.side_effect = None

        class _Strict:
            def __init__(self):
                self.id = "bad"
                self.version = 0

            def model_dump(self):
                from pydantic import BaseModel

                class _M(BaseModel):
                    x: int

                _M.model_validate({"x": "not-an-int"})  # triggers PydanticValidationError
                return {}

        repo = StrategyBasedRepository(_Strict, storage)
        with pytest.raises(ValueError, match="Validation error"):
            repo.save(_Strict())


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindById:
    def test_returns_none_when_not_found(self) -> None:
        repo, _ = _make_repo()
        assert repo.find_by_id("missing") is None

    def test_returns_from_cache_when_present(self) -> None:
        repo, storage = _make_repo()
        entity = _SimpleEntity(id="e1")
        repo._cache["e1"] = entity
        result = repo.find_by_id("e1")
        assert result is entity
        storage.find_by_id.assert_not_called()

    def test_loads_from_storage_on_miss(self) -> None:
        storage = _make_storage(find_by_id={"id": "e2", "version": 0})
        repo, _ = _make_repo(storage=storage)
        entity = repo.find_by_id("e2")
        assert entity is not None
        assert entity.id == "e2"
        assert "e2" in repo._cache

    def test_populates_version_map_on_load(self) -> None:
        storage = _make_storage(find_by_id={"id": "ev", "version": 5})
        repo, _ = _make_repo(storage=storage)
        repo.find_by_id("ev")
        assert repo._version_map["ev"] == 5


# ---------------------------------------------------------------------------
# find_all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindAll:
    def test_empty_storage_returns_empty_list(self) -> None:
        repo, _ = _make_repo()
        assert repo.find_all() == []

    def test_dict_result_from_storage(self) -> None:
        storage = _make_storage(
            find_all={"e1": {"id": "e1", "version": 0}, "e2": {"id": "e2", "version": 0}}
        )
        repo, _ = _make_repo(storage=storage)
        result = repo.find_all()
        ids = {e.id for e in result}
        assert ids == {"e1", "e2"}

    def test_list_result_from_storage(self) -> None:
        storage = _make_storage(find_all=[{"id": "lx", "version": 0}])
        repo, _ = _make_repo(storage=storage)
        result = repo.find_all()
        assert len(result) == 1
        assert result[0].id == "lx"

    def test_uses_cache_for_dict_result(self) -> None:
        cached_entity = _SimpleEntity(id="e1")
        storage = _make_storage(find_all={"e1": {"id": "e1", "version": 0}})
        repo, _ = _make_repo(storage=storage)
        repo._cache["e1"] = cached_entity
        result = repo.find_all()
        assert result[0] is cached_entity

    def test_uses_cache_for_list_result(self) -> None:
        cached_entity = _SimpleEntity(id="e1")
        storage = _make_storage(find_all=[{"id": "e1", "version": 0}])
        repo, _ = _make_repo(storage=storage)
        repo._cache["e1"] = cached_entity
        result = repo.find_all()
        assert result[0] is cached_entity


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDelete:
    def test_raises_entity_not_found_when_missing(self) -> None:
        repo, _ = _make_repo()
        with pytest.raises(EntityNotFoundError):
            repo.delete("nonexistent")

    def test_deletes_and_clears_cache(self) -> None:
        storage = _make_storage(exists=True)
        repo, _ = _make_repo(storage=storage)
        entity = _SimpleEntity(id="e1")
        repo._cache["e1"] = entity
        repo._version_map["e1"] = 1
        repo.delete("e1")
        storage.delete.assert_called_once_with("e1")
        assert "e1" not in repo._cache
        assert "e1" not in repo._version_map

    def test_calls_storage_delete(self) -> None:
        storage = _make_storage(exists=True)
        repo, _ = _make_repo(storage=storage)
        repo.delete("x")
        storage.delete.assert_called_once_with("x")


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExists:
    def test_true_when_in_cache(self) -> None:
        repo, storage = _make_repo()
        repo._cache["e1"] = _SimpleEntity(id="e1")
        assert repo.exists("e1") is True
        storage.exists.assert_not_called()

    def test_delegates_to_storage_on_cache_miss(self) -> None:
        storage = _make_storage(exists=True)
        repo, _ = _make_repo(storage=storage)
        assert repo.exists("e1") is True

    def test_false_when_missing(self) -> None:
        storage = _make_storage(exists=False)
        repo, _ = _make_repo(storage=storage)
        assert repo.exists("gone") is False


# ---------------------------------------------------------------------------
# find_by_criteria
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindByCriteria:
    def test_empty_result(self) -> None:
        repo, _ = _make_repo()
        assert repo.find_by_criteria({"status": "active"}) == []

    def test_list_result(self) -> None:
        storage = _make_storage(find_by_criteria=[{"id": "c1", "version": 0}])
        repo, _ = _make_repo(storage=storage)
        result = repo.find_by_criteria({"status": "active"})
        assert len(result) == 1

    def test_dict_result_from_criteria(self) -> None:
        storage = MagicMock()
        storage.find_by_criteria.return_value = {"c1": {"id": "c1", "version": 0}}
        repo = StrategyBasedRepository(_SimpleEntity, storage)
        result = repo.find_by_criteria({"foo": "bar"})
        assert len(result) == 1

    def test_uses_cached_entity_in_criteria_result(self) -> None:
        cached = _SimpleEntity(id="c2")
        storage = _make_storage(find_by_criteria=[{"id": "c2", "version": 0}])
        repo, _ = _make_repo(storage=storage)
        repo._cache["c2"] = cached
        result = repo.find_by_criteria({})
        assert result[0] is cached


# ---------------------------------------------------------------------------
# save_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSaveBatch:
    def test_saves_multiple_entities(self) -> None:
        repo, storage = _make_repo()
        entities = [_SimpleEntity(id=f"b{i}") for i in range(3)]
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save_batch(entities)
        storage.save_batch.assert_called_once()
        assert len(repo._cache) == 3

    def test_empty_batch_does_nothing(self) -> None:
        repo, storage = _make_repo()
        repo.save_batch([])
        storage.save_batch.assert_not_called()

    def test_batch_version_conflict_raises(self) -> None:
        repo, _ = _make_repo()
        repo._version_map["b1"] = 5
        entities = [_SimpleEntity(id="b1", version=3)]  # mismatched
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            with pytest.raises(ConcurrencyError):
                repo.save_batch(entities)


# ---------------------------------------------------------------------------
# delete_batch
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteBatch:
    def test_raises_when_entity_missing(self) -> None:
        repo, _ = _make_repo()
        with pytest.raises(EntityNotFoundError):
            repo.delete_batch(["not-here"])

    def test_deletes_multiple_entities(self) -> None:
        storage = _make_storage(exists=True)
        repo, _ = _make_repo(storage=storage)
        repo._cache["a"] = _SimpleEntity(id="a")
        repo._cache["b"] = _SimpleEntity(id="b")
        repo.delete_batch(["a", "b"])
        storage.delete_batch.assert_called_once()
        assert "a" not in repo._cache
        assert "b" not in repo._cache


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClearCache:
    def test_clears_both_dicts(self) -> None:
        repo, _ = _make_repo()
        repo._cache["e1"] = _SimpleEntity(id="e1")
        repo._version_map["e1"] = 1
        repo.clear_cache()
        assert repo._cache == {}
        assert repo._version_map == {}


# ---------------------------------------------------------------------------
# async event bus path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAsyncEventBus:
    def test_publish_skipped_outside_event_loop(self) -> None:
        """No running event loop → warning logged, no crash."""

        async def _async_publish(event):
            pass

        bus = MagicMock()
        bus.publish = _async_publish  # coroutine function

        repo = StrategyBasedRepository(_EventEntity, _make_storage(), event_bus=bus)
        entity = _EventEntity(id="ae1")
        entity._events.append("some_event")

        # asyncio.get_running_loop() raises RuntimeError outside a loop,
        # so the repo should log a warning and not raise.
        with patch(
            "orb.infrastructure.utilities.common.serialization.process_value_objects",
            side_effect=lambda x: x,
        ):
            repo.save(entity)  # must not raise
