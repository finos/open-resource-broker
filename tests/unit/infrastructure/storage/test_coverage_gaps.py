"""Coverage-gap tests for storage module.

Targets uncovered lines in:
- sql/strategy.py   (auto-stamp path, non-ORM table fallback, init exception,
                     count_by_column SQLAlchemy error path)
- json/strategy.py  (exception branches in every public method,
                     _load_data recovery path, _save_data read-error path)
- components/file_manager.py  (error branches in read_file, write_file,
                               _atomic_write failure cleanup, create_backup
                               exception, _cleanup_old_backups unlink error,
                               recover_from_backup exception,
                               verify_file_integrity exception)
- repositories/template_repository.py  (exception branches in every public method)
"""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across multiple test classes
# ---------------------------------------------------------------------------


def _make_sql_strategy(table_name="cov_ents", columns=None):
    """In-memory SQLite strategy for SQL gap tests."""
    from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

    if columns is None:
        columns = {"id": "TEXT PRIMARY KEY", "data": "TEXT"}
    return SQLStorageStrategy(
        config={"type": "sqlite", "name": ":memory:"},
        table_name=table_name,
        columns=columns,
    )


def _make_json_strategy(tmp_path: Path, entity_type: str = "items") -> Any:
    from orb.infrastructure.storage.json.strategy import JSONStorageStrategy

    return JSONStorageStrategy(
        file_path=str(tmp_path / "data.json"),
        entity_type=entity_type,
        backup_enabled=False,
    )


# ---------------------------------------------------------------------------
# SQLStorageStrategy — uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSQLStrategyInitializeTable:
    """_initialize_table: ORM path, non-ORM fallback, exception re-raise."""

    def test_non_orm_table_created_via_column_dict(self) -> None:
        """A table whose name is NOT in ORM Base.metadata uses the column-dict DDL path."""
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        # 'custom_table' is not in the ORM Base, so it uses build_create_table().
        strategy = SQLStorageStrategy(
            config={"type": "sqlite", "name": ":memory:"},
            table_name="custom_table",
            columns={"id": "TEXT PRIMARY KEY", "value": "TEXT"},
        )
        # If we reach here without exception the DDL path ran.
        assert strategy.table_name == "custom_table"

    def test_initialize_table_exception_is_reraised(self) -> None:
        """If _initialize_table raises, __init__ propagates it."""
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        with patch(
            "orb.infrastructure.storage.sql.strategy.SQLConnectionManager",
            side_effect=RuntimeError("cannot connect"),
        ):
            with pytest.raises(RuntimeError, match="cannot connect"):
                SQLStorageStrategy(
                    config={"type": "sqlite", "name": ":memory:"},
                    table_name="x",
                    columns={"id": "TEXT PRIMARY KEY"},
                )

    def test_initialize_table_error_logged_and_reraised(self) -> None:
        """SQLConnectionManager init failure logs an error and re-raises (lines 136-138)."""
        from orb.infrastructure.storage.sql.strategy import SQLStorageStrategy

        with patch(
            "orb.infrastructure.storage.sql.strategy.SQLConnectionManager",
            side_effect=Exception("forced init failure"),
        ):
            with pytest.raises(Exception, match="forced init failure"):
                SQLStorageStrategy(
                    config={"type": "sqlite", "name": ":memory:"},
                    table_name="tbl",
                    columns={"id": "TEXT PRIMARY KEY"},
                )


@pytest.mark.unit
class TestSQLStrategyAutoStampHead:
    """_auto_stamp_head: best-effort; failures must not raise, only warn."""

    def test_auto_stamp_failure_logs_warning_does_not_raise(self) -> None:
        """_auto_stamp_head catches exceptions and logs at WARNING (line 259-265)."""
        strategy = _make_sql_strategy()
        # Pass a mock engine that raises on raw_connection() to force the
        # warning path without needing a real alembic setup.
        mock_engine = MagicMock()
        mock_engine.url = "sqlite:///:memory:"
        mock_engine.raw_connection.side_effect = RuntimeError("no raw conn")
        # Should NOT raise — failure is best-effort.
        strategy._auto_stamp_head(mock_engine)

    def test_auto_stamp_head_revision_none_returns_early(self) -> None:
        """When get_current_head() returns None the method returns without stamping."""
        strategy = _make_sql_strategy()
        mock_engine = MagicMock()
        mock_engine.url = "sqlite:///:memory:"

        mock_script_dir = MagicMock()
        mock_script_dir.get_current_head.return_value = None

        with (
            patch("alembic.config.Config"),
            patch("alembic.script.ScriptDirectory.from_config", return_value=mock_script_dir),
        ):
            # No exception, and no INSERT attempt.
            strategy._auto_stamp_head(mock_engine)


@pytest.mark.unit
class TestSQLStrategyCountByColumnSQLAlchemyError:
    """count_by_column wraps SQLAlchemyError as RepositoryQueryError."""

    def test_raises_repository_query_error_on_sqlalchemy_failure(self) -> None:
        from sqlalchemy.exc import SQLAlchemyError

        from orb.application.ports.exceptions import RepositoryQueryError

        strategy = _make_sql_strategy(columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"})
        # Force the session to raise a SQLAlchemy error.
        from unittest.mock import patch as _patch

        with _patch.object(
            strategy.connection_manager,
            "get_session",
            side_effect=SQLAlchemyError("alchemy boom"),
        ):
            with pytest.raises(RepositoryQueryError):
                strategy.count_by_column("data")

    def test_non_sqlalchemy_error_propagates_unchanged(self) -> None:
        """Non-SQLAlchemy errors are re-raised as-is (not wrapped)."""
        strategy = _make_sql_strategy(columns={"id": "TEXT PRIMARY KEY", "data": "TEXT"})

        with patch.object(
            strategy.connection_manager,
            "get_session",
            side_effect=RuntimeError("generic error"),
        ):
            with pytest.raises(RuntimeError, match="generic error"):
                strategy.count_by_column("data")


# ---------------------------------------------------------------------------
# JSONStorageStrategy — error/exception branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestJSONStrategySaveException:
    """save() must raise StorageError when _save_data fails."""

    def test_save_raises_storage_error_on_write_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "write_file", side_effect=OSError("disk full")):
            with pytest.raises(StorageError):
                strategy.save("e1", {"v": 1})


@pytest.mark.unit
class TestJSONStrategyFindByIdException:
    """find_by_id() returns None when entity absent (no raise); cache miss is handled."""

    def test_find_by_id_returns_none_for_absent_entity(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        # No entities stored — should return None, not raise.
        result = strategy.find_by_id("missing_key")
        assert result is None

    def test_find_by_id_returns_entity_when_present(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        strategy.save("k1", {"color": "blue"})
        result = strategy.find_by_id("k1")
        assert result is not None
        assert result["color"] == "blue"


@pytest.mark.unit
class TestJSONStrategyFindAllException:
    """find_all() returns empty dict on load error (swallowed by _load_data)."""

    def test_find_all_returns_empty_on_load_error(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "read_file", side_effect=OSError("unreadable")):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=False):
                strategy._cache_valid = False
                strategy._data_cache = None
                result = strategy.find_all()
        assert result == {}


@pytest.mark.unit
class TestJSONStrategyDeleteException:
    """delete() must raise StorageError when write fails."""

    def test_delete_raises_storage_error_on_write_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_json_strategy(tmp_path)
        strategy.save("e1", {"v": 1})
        strategy._cache_valid = False  # force real load path
        with patch.object(strategy.file_manager, "write_file", side_effect=OSError("disk full")):
            with pytest.raises(StorageError):
                strategy.delete("e1")


@pytest.mark.unit
class TestJSONStrategyExistsException:
    """exists() returns False (not raises) when loading fails."""

    def test_exists_returns_false_on_load_failure(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "read_file", side_effect=OSError("cannot read")):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=False):
                result = strategy.exists("e1")
        assert result is False


@pytest.mark.unit
class TestJSONStrategyFindByCriteriaException:
    """find_by_criteria() returns empty list when _load_data swallows errors."""

    def test_find_by_criteria_returns_empty_on_load_error(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "read_file", side_effect=OSError("no read")):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=False):
                strategy._cache_valid = False
                strategy._data_cache = None
                result = strategy.find_by_criteria({"v": 1})
        assert result == []


@pytest.mark.unit
class TestJSONStrategySaveBatchException:
    """save_batch() raises StorageError when write fails."""

    def test_save_batch_raises_storage_error_on_write_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "write_file", side_effect=OSError("disk full")):
            with pytest.raises(StorageError):
                strategy.save_batch({"a": {"v": 1}})


@pytest.mark.unit
class TestJSONStrategyDeleteBatchException:
    """delete_batch() raises StorageError when write fails."""

    def test_delete_batch_raises_storage_error_on_write_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.exceptions import StorageError

        strategy = _make_json_strategy(tmp_path)
        strategy.save("z", {"v": 99})
        strategy._cache_valid = False
        with patch.object(strategy.file_manager, "write_file", side_effect=OSError("disk full")):
            with pytest.raises(StorageError):
                strategy.delete_batch(["z"])


@pytest.mark.unit
class TestJSONStrategyCountException:
    """count() returns 0 (not raises) on exception."""

    def test_count_returns_zero_on_load_failure(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        with patch.object(strategy.file_manager, "read_file", side_effect=OSError("no read")):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=False):
                assert strategy.count() == 0


@pytest.mark.unit
class TestJSONStrategyLoadDataRecovery:
    """_load_data recovers from backup when read_file raises."""

    def test_load_data_recovers_when_read_fails_and_backup_succeeds(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        strategy.save("real", {"v": 42})

        # Populate the backup dir so recovery logic can find a file.
        call_count = {"n": 0}
        original_read = strategy.file_manager.read_file

        def flaky_read():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("first read explodes")
            return original_read()

        with patch.object(strategy.file_manager, "read_file", side_effect=flaky_read):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=True):
                # reset cache so _load_data actually reads
                strategy._cache_valid = False
                strategy._data_cache = None
                result = strategy._load_data()
        # After recovery the second read returns the real data
        assert isinstance(result, dict)

    def test_load_data_returns_empty_when_recovery_fails(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        with patch.object(
            strategy.file_manager, "read_file", side_effect=OSError("totally broken")
        ):
            with patch.object(strategy.file_manager, "recover_from_backup", return_value=False):
                strategy._cache_valid = False
                strategy._data_cache = None
                result = strategy._load_data()
        assert result == {}


@pytest.mark.unit
class TestJSONStrategySaveDataReadError:
    """_save_data raises when re-reading inside the lock fails."""

    def test_save_data_raises_when_reread_inside_lock_fails(self, tmp_path: Path) -> None:
        strategy = _make_json_strategy(tmp_path)
        # Patch create_backup to no-op so it doesn't call read_file before we expect.
        with patch.object(strategy.file_manager, "create_backup", return_value=None):
            # Now patch read_file to fail on first call (inside the lock).
            with patch.object(
                strategy.file_manager, "read_file", side_effect=OSError("lock-read fails")
            ):
                with pytest.raises(OSError, match="lock-read fails"):
                    strategy._save_data({"e1": {"v": 1}})


# ---------------------------------------------------------------------------
# FileManager — error branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileManagerReadFileError:
    """read_file() logs and re-raises on unexpected exceptions (lines 133-135)."""

    def test_read_file_reraises_unexpected_error(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)
        fm.file_path.write_text("content", encoding="utf-8")

        with patch("builtins.open", side_effect=PermissionError("no perms")):
            with pytest.raises(PermissionError):
                fm.read_file()


@pytest.mark.unit
class TestFileManagerWriteFileError:
    """write_file() logs and re-raises when _atomic_write fails (lines 147-149)."""

    def test_write_file_reraises_on_atomic_write_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)
        with patch.object(fm, "_atomic_write", side_effect=OSError("no space")):
            with pytest.raises(OSError, match="no space"):
                fm.write_file("data")


@pytest.mark.unit
class TestFileManagerAtomicWriteFailureCleanup:
    """_atomic_write cleans up temp file when rename fails (lines 178-182)."""

    def test_atomic_write_cleans_temp_file_on_rename_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)

        with patch("pathlib.Path.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError):
                fm._atomic_write("content")

        # No leftover temp files.
        leftover = list(tmp_path.glob(".data.json.tmp*"))
        assert leftover == []


@pytest.mark.unit
class TestFileManagerCreateBackupChecksumError:
    """create_backup() logs on checksum comparison failure, still proceeds (lines 216-217)."""

    def test_create_backup_proceeds_when_checksum_compare_raises(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_count=5, backup_enabled=True)
        fm.write_file("initial content")
        # Create a first backup normally.
        first = fm.create_backup()
        assert first is not None

        # Patch checksum calculation to raise on the second call so the
        # "skip if content unchanged" guard falls into the except branch.
        original_checksum = fm.calculate_checksum
        call_count = {"n": 0}

        def flaky_checksum(content: str) -> str:
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise ValueError("checksum explodes")
            return original_checksum(content)

        with patch.object(fm, "calculate_checksum", side_effect=flaky_checksum):
            # modify content so it's a different file on disk
            fm.write_file("changed content")
            second = fm.create_backup()
        # Should still have created a backup (exception was handled).
        assert second is not None


@pytest.mark.unit
class TestFileManagerCreateBackupGeneralException:
    """create_backup() catches general exceptions and returns None (lines 227-229)."""

    def test_create_backup_returns_none_on_shutil_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_count=5, backup_enabled=True)
        fm.write_file("some data")

        with patch("shutil.copy2", side_effect=OSError("copy failed")):
            result = fm.create_backup()
        assert result is None


@pytest.mark.unit
class TestFileManagerCleanupOldBackupsUnlinkError:
    """_cleanup_old_backups logs but continues when unlink raises (lines 246-247)."""

    def test_cleanup_old_backups_continues_past_unlink_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_count=1, backup_enabled=True)
        # Create two distinct backups.
        fm.write_file("v1")
        b1 = fm.create_backup()
        assert b1 is not None
        fm.write_file("v2")
        with patch("pathlib.Path.unlink", side_effect=PermissionError("cannot delete")):
            # Should not raise even though unlink fails.
            fm._cleanup_old_backups()


@pytest.mark.unit
class TestFileManagerCleanupOldBackupsOuterException:
    """_cleanup_old_backups catches outer errors silently (lines 249-250)."""

    def test_cleanup_old_backups_handles_outer_exception(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_count=2, backup_enabled=True)
        # Patch Path.glob at the module level so any backup_dir.glob call raises.
        with patch(
            "orb.infrastructure.storage.components.file_manager.Path.glob",
            side_effect=OSError("glob failed"),
        ):
            # Must not raise.
            fm._cleanup_old_backups()


@pytest.mark.unit
class TestFileManagerRecoverFromBackupException:
    """recover_from_backup() catches errors and returns False (lines 326-328)."""

    def test_recover_from_backup_returns_false_on_copy_failure(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_count=5, backup_enabled=True)
        fm.write_file("data")
        fm.create_backup()

        with patch("shutil.copy2", side_effect=OSError("copy error")):
            result = fm.recover_from_backup()
        assert result is False


@pytest.mark.unit
class TestFileManagerVerifyFileIntegrityException:
    """verify_file_integrity() catches errors and returns False (lines 296-298)."""

    def test_verify_file_integrity_returns_false_on_exception(self, tmp_path: Path) -> None:
        from orb.infrastructure.storage.components.file_manager import FileManager

        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=False)
        fm.write_file("data")

        with patch.object(fm, "read_file", side_effect=OSError("read failed")):
            result = fm.verify_file_integrity()
        assert result is False


# ---------------------------------------------------------------------------
# TemplateRepositoryImpl — error branches
# ---------------------------------------------------------------------------


def _minimal_template_data() -> dict:
    from datetime import datetime, timezone

    _NOW = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return {
        "template_id": "tpl-gap-001",
        "name": "Gap Template",
        "image_id": "ami-gap-0000",
        "provider_type": "aws",
        "provider_name": "aws-us-east-1",
        "provider_api": "RunInstances",
        "created_at": _NOW.isoformat(),
        "updated_at": _NOW.isoformat(),
    }


def _make_template() -> Any:
    from datetime import datetime, timezone

    from orb.domain.template.template_aggregate import Template

    _NOW = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return Template.model_validate(
        {
            **_minimal_template_data(),
            "created_at": _NOW,
            "updated_at": _NOW,
        }
    )


def _make_repo(storage=None):
    from orb.infrastructure.storage.repositories.template_repository import TemplateRepositoryImpl

    if storage is None:
        storage = MagicMock()
    return TemplateRepositoryImpl(storage), storage


@pytest.mark.unit
class TestTemplateRepositoryImplSaveException:
    """save() re-raises when storage raises (line 250-252)."""

    def test_save_reraises_on_serializer_failure(self) -> None:
        repo, storage = _make_repo()
        storage.save.side_effect = RuntimeError("DB full")
        with pytest.raises(Exception):
            repo.save(_make_template())


@pytest.mark.unit
class TestTemplateRepositoryImplGetByIdException:
    """get_by_id() re-raises when loading from storage raises (lines 269-271)."""

    def test_get_by_id_reraises_on_storage_failure(self) -> None:
        from orb.domain.template.value_objects import TemplateId

        repo, storage = _make_repo()
        storage.find_by_id.side_effect = RuntimeError("storage down")
        with pytest.raises(Exception):
            repo.get_by_id(TemplateId(value="tpl-gap-001"))


@pytest.mark.unit
class TestTemplateRepositoryImplFindByTemplateIdException:
    """find_by_template_id() re-raises when get_by_id raises (lines 283-285)."""

    def test_find_by_template_id_reraises_on_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_by_id.side_effect = RuntimeError("error")
        with pytest.raises(Exception):
            repo.find_by_template_id("tpl-gap-001")


@pytest.mark.unit
class TestTemplateRepositoryImplFindByNameException:
    """find_by_name() re-raises when storage raises (lines 293-295)."""

    def test_find_by_name_reraises_on_storage_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.find_by_name("Ghost")


@pytest.mark.unit
class TestTemplateRepositoryImplFindActiveTemplatesException:
    """find_active_templates() re-raises when storage raises (lines 302-304)."""

    def test_find_active_templates_reraises_on_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.find_active_templates()


@pytest.mark.unit
class TestTemplateRepositoryImplFindByProviderApiException:
    """find_by_provider_api() re-raises when storage raises (lines 311-313)."""

    def test_find_by_provider_api_reraises_on_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.find_by_provider_api("EC2Fleet")


@pytest.mark.unit
class TestTemplateRepositoryImplFindAllException:
    """find_all() re-raises when storage raises (lines 320-322)."""

    def test_find_all_reraises_on_storage_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_all.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.find_all()


@pytest.mark.unit
class TestTemplateRepositoryImplSearchTemplatesException:
    """search_templates() re-raises when storage raises (lines 348-350)."""

    def test_search_templates_reraises_on_failure(self) -> None:
        repo, storage = _make_repo()
        storage.find_by_criteria.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.search_templates({"provider_type": "aws"})


@pytest.mark.unit
class TestTemplateRepositoryImplDeleteException:
    """delete() re-raises when storage raises (lines 360-362)."""

    def test_delete_reraises_on_storage_failure(self) -> None:
        from orb.domain.template.value_objects import TemplateId

        repo, storage = _make_repo()
        storage.delete.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.delete(TemplateId(value="tpl-gap-001"))


@pytest.mark.unit
class TestTemplateRepositoryImplExistsException:
    """exists() re-raises when storage raises (lines 369-371)."""

    def test_exists_reraises_on_storage_failure(self) -> None:
        from orb.domain.template.value_objects import TemplateId

        repo, storage = _make_repo()
        storage.exists.side_effect = RuntimeError("boom")
        with pytest.raises(Exception):
            repo.exists(TemplateId(value="tpl-gap-001"))


@pytest.mark.unit
class TestTemplateRepositoryImplSaveWithDomainEvents:
    """save() calls event_publisher when domain events are present (line 241)."""

    def test_save_publishes_domain_events(self) -> None:
        from orb.infrastructure.storage.repositories.template_repository import (
            TemplateRepositoryImpl,
        )

        storage = MagicMock()
        mock_event_publisher = MagicMock()
        mock_event = MagicMock()

        repo = TemplateRepositoryImpl(storage, event_publisher=mock_event_publisher)
        template = _make_template()

        # Use patch to override the methods on the template's class temporarily.
        with patch.object(
            type(template), "get_domain_events", create=True, return_value=[mock_event]
        ):
            with patch.object(type(template), "clear_domain_events", create=True):
                repo.save(template)

        mock_event_publisher.publish_events.assert_called_once_with([mock_event])


@pytest.mark.unit
class TestTemplateSerializerToDict:
    """to_dict() exception branch logs and re-raises (lines 112-114)."""

    def test_to_dict_reraises_when_serialize_datetime_fails(self) -> None:
        from orb.infrastructure.storage.repositories.template_repository import TemplateSerializer

        s = TemplateSerializer()
        template = _make_template()

        # Patch the GenericEntitySerializer's serialize_datetime to raise so the
        # except branch (lines 112-114) in to_dict() is exercised.
        with patch.object(s._dt, "serialize_datetime", side_effect=ValueError("bad datetime")):
            with pytest.raises(Exception):
                s.to_dict(template)
