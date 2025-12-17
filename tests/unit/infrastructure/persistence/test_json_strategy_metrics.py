"""Tests for JSON storage strategy metrics instrumentation."""

from unittest.mock import Mock, patch

import pytest

from infrastructure.persistence.json.strategy import JSONStorageStrategy


def test_save_with_metrics_success(tmp_path):
    """Verify metrics are recorded on successful save."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    strategy.save("test-id", {"key": "value"})

    metrics.increment_counter.assert_called_with("storage.json.save_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.save_duration" in metrics.record_time.call_args[0][0]


def test_save_with_metrics_error(tmp_path):
    """Verify error metrics are recorded on failure."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    with patch.object(strategy, "_save_data", side_effect=Exception("Test error")):
        with pytest.raises(Exception):
            strategy.save("test-id", {"key": "value"})

    metrics.increment_counter.assert_called_with("storage.json.save_errors_total")
    metrics.record_time.assert_called_once()


def test_save_without_metrics(tmp_path):
    """Verify operations work without metrics collector."""
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=None)

    # Should not raise, operates normally
    strategy.save("test-id", {"key": "value"})


def test_batch_operations_metrics(tmp_path):
    """Verify batch operations are instrumented."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    strategy.save_batch({"id1": {"data": 1}, "id2": {"data": 2}})

    metrics.increment_counter.assert_called_with("storage.json.save_batch_total")
    metrics.record_time.assert_called_once()


def test_find_by_id_with_metrics_success(tmp_path):
    """Verify metrics are recorded on successful find_by_id."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save first
    strategy.save("test-id", {"key": "value"})
    metrics.reset_mock()

    # Find
    result = strategy.find_by_id("test-id")

    assert result == {"key": "value"}
    metrics.increment_counter.assert_called_with("storage.json.find_by_id_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.find_by_id_duration" in metrics.record_time.call_args[0][0]


def test_find_by_id_with_metrics_error(tmp_path):
    """Verify error metrics are recorded on find_by_id failure."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    with patch.object(strategy, "_load_data", side_effect=Exception("Test error")):
        with pytest.raises(Exception):
            strategy.find_by_id("test-id")

    metrics.increment_counter.assert_called_with("storage.json.find_by_id_errors_total")
    metrics.record_time.assert_called_once()


def test_find_all_with_metrics(tmp_path):
    """Verify metrics are recorded on find_all."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    result = strategy.find_all()

    assert result == {}
    metrics.increment_counter.assert_called_with("storage.json.find_all_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.find_all_duration" in metrics.record_time.call_args[0][0]


def test_delete_with_metrics_success(tmp_path):
    """Verify metrics are recorded on successful delete."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save first
    strategy.save("test-id", {"key": "value"})
    metrics.reset_mock()

    # Delete
    strategy.delete("test-id")

    metrics.increment_counter.assert_called_with("storage.json.delete_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.delete_duration" in metrics.record_time.call_args[0][0]


def test_delete_with_metrics_error(tmp_path):
    """Verify error metrics are recorded on delete failure."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    with patch.object(strategy, "_load_data", side_effect=Exception("Test error")):
        with pytest.raises(Exception):
            strategy.delete("test-id")

    metrics.increment_counter.assert_called_with("storage.json.delete_errors_total")
    metrics.record_time.assert_called_once()


def test_delete_batch_with_metrics(tmp_path):
    """Verify metrics are recorded on delete_batch."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save first
    strategy.save_batch({"id1": {"data": 1}, "id2": {"data": 2}})
    metrics.reset_mock()

    # Delete batch
    strategy.delete_batch(["id1", "id2"])

    metrics.increment_counter.assert_called_with("storage.json.delete_batch_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.delete_batch_duration" in metrics.record_time.call_args[0][0]


def test_metrics_duration_is_positive(tmp_path):
    """Verify that duration metrics are always positive."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    strategy.save("test-id", {"key": "value"})

    # Check that duration was recorded with a positive value
    call_args = metrics.record_time.call_args
    assert call_args[0][0] == "storage.json.save_duration"
    assert call_args[0][1] >= 0


def test_exists_with_metrics(tmp_path):
    """Verify metrics are recorded on exists check."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save first
    strategy.save("test-id", {"key": "value"})
    metrics.reset_mock()

    # Check exists
    result = strategy.exists("test-id")

    assert result is True
    metrics.increment_counter.assert_called_with("storage.json.exists_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.exists_duration" in metrics.record_time.call_args[0][0]


def test_find_by_criteria_with_metrics(tmp_path):
    """Verify metrics are recorded on find_by_criteria."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save some data
    strategy.save("test-1", {"type": "A", "value": 1})
    strategy.save("test-2", {"type": "B", "value": 2})
    metrics.reset_mock()

    # Search by criteria
    result = strategy.find_by_criteria({"type": "A"})

    assert len(result) == 1
    metrics.increment_counter.assert_called_with("storage.json.find_by_criteria_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.find_by_criteria_duration" in metrics.record_time.call_args[0][0]


def test_count_with_metrics(tmp_path):
    """Verify metrics are recorded on count."""
    metrics = Mock()
    test_file = tmp_path / "test.json"

    strategy = JSONStorageStrategy(file_path=str(test_file), metrics=metrics)

    # Save some data
    strategy.save("test-1", {"key": "value1"})
    strategy.save("test-2", {"key": "value2"})
    metrics.reset_mock()

    # Count
    result = strategy.count()

    assert result == 2
    metrics.increment_counter.assert_called_with("storage.json.count_total")
    metrics.record_time.assert_called_once()
    assert "storage.json.count_duration" in metrics.record_time.call_args[0][0]
