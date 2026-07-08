"""Unit tests for StorageRepositoryMixin._safe_deserialize_iter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.infrastructure.storage.base.repository_mixin import StorageRepositoryMixin

# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------


class _ConcreteRepo(StorageRepositoryMixin):
    """Minimal concrete repo that uses _safe_deserialize_iter."""

    def __init__(self, deserializer=None):
        self._skipped_row_count = {}
        self.logger = MagicMock()
        self._deserializer = deserializer  # callable(data) -> entity

    def _deserialize(self, data):
        if self._deserializer is not None:
            return self._deserializer(data)
        raise NotImplementedError("no deserializer")

    def _get_storage(self):
        raise NotImplementedError("storage not needed for these tests")


# ---------------------------------------------------------------------------
# _safe_deserialize_iter tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSafeDeserializeIter:
    def test_all_valid_rows_returned(self):
        rows = [
            {"machine_id": "m1", "status": "running"},
            {"machine_id": "m2", "status": "stopped"},
        ]
        repo = _ConcreteRepo(deserializer=lambda d: d)
        result = list(repo._safe_deserialize_iter(rows))
        assert result == rows

    def test_corrupt_row_skipped_rest_returned(self):
        """A corrupt row must be skipped; the healthy rows must still be returned."""
        call_count = 0

        def flaky(data):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("bad data")
            return data

        rows = [
            {"machine_id": "m1"},
            {"machine_id": "m2-corrupt"},
            {"machine_id": "m3"},
        ]
        repo = _ConcreteRepo(deserializer=flaky)
        result = list(repo._safe_deserialize_iter(rows))
        assert len(result) == 2
        assert result[0]["machine_id"] == "m1"
        assert result[1]["machine_id"] == "m3"

    def test_error_log_emitted_with_entity_id(self):
        """logger.error must be called with the entity id of the bad row."""

        def broken(_data):
            raise ValueError("schema changed")

        rows = [{"request_id": "req-bad-1", "status": "pending"}]
        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter(rows))

        repo.logger.error.assert_called_once()
        call_args = repo.logger.error.call_args[0]
        # The entity id must appear somewhere in the format string or args
        all_args = " ".join(str(a) for a in call_args)
        assert "req-bad-1" in all_args

    def test_skip_counter_incremented_per_entity_type(self):
        def broken(_data):
            raise ValueError("oops")

        rows = [
            {"machine_id": "m-bad-1"},
            {"machine_id": "m-bad-2"},
            {"request_id": "req-bad-1"},
        ]
        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter(rows))

        counters = repo._get_skip_counters()
        assert counters.get("machines", 0) == 2
        assert counters.get("requests", 0) == 1

    def test_get_skip_counters_returns_dict_by_entity_type(self):
        def broken(_data):
            raise ValueError("bad")

        rows = [{"template_id": "t1"}]
        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter(rows))

        counters = repo._get_skip_counters()
        assert isinstance(counters, dict)
        assert counters.get("templates", 0) == 1

    def test_get_skip_counters_returns_copy_not_live_dict(self):
        """Mutations to the returned dict must not affect internal state."""
        repo = _ConcreteRepo(deserializer=lambda d: d)
        counters = repo._get_skip_counters()
        counters["machines"] = 999
        assert repo._get_skip_counters().get("machines", 0) != 999

    def test_empty_input_yields_nothing(self):
        repo = _ConcreteRepo(deserializer=lambda d: d)
        result = list(repo._safe_deserialize_iter([]))
        assert result == []

    def test_all_corrupt_yields_nothing(self):
        def always_fail(_data):
            raise TypeError("always broken")

        rows = [{"machine_id": f"m{i}"} for i in range(5)]
        repo = _ConcreteRepo(deserializer=always_fail)
        result = list(repo._safe_deserialize_iter(rows))
        assert result == []
        assert repo._get_skip_counters().get("machines", 0) == 5

    def test_unknown_entity_type_counted_under_unknown(self):
        """Rows with no known id field go into 'unknown' bucket."""

        def broken(_data):
            raise ValueError("bad")

        rows = [{"some_other_field": "value"}]
        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter(rows))

        counters = repo._get_skip_counters()
        assert counters.get("unknown", 0) == 1

    def test_skip_counter_accumulates_across_calls(self):
        """Multiple calls to _safe_deserialize_iter must accumulate skip counts."""

        def broken(_data):
            raise ValueError("bad")

        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter([{"machine_id": "m1"}]))
        list(repo._safe_deserialize_iter([{"machine_id": "m2"}]))

        assert repo._get_skip_counters().get("machines", 0) == 2

    def test_error_logged_at_error_level(self):
        """Skipped rows must be logged at ERROR level (not WARNING/INFO)."""

        def broken(_data):
            raise ValueError("validation")

        rows = [{"request_id": "req-1"}]
        repo = _ConcreteRepo(deserializer=broken)
        list(repo._safe_deserialize_iter(rows))

        # logger.error must have been called (not warning or info)
        repo.logger.error.assert_called_once()
        repo.logger.warning.assert_not_called()
