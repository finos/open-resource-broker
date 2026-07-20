"""Unit tests for FileManager covering additional branches."""

import hashlib
import time
from pathlib import Path

import pytest

from orb.infrastructure.storage.components.file_manager import FileManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fm(tmp_path: Path, backup_enabled: bool = False) -> FileManager:
    return FileManager(str(tmp_path / "data.json"), backup_enabled=backup_enabled)


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadFile:
    def test_nonexistent_file_returns_empty_string(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert fm.read_file() == ""

    def test_existing_file_returns_content(self, tmp_path: Path) -> None:
        data = tmp_path / "data.json"
        data.write_text('{"key": "val"}', encoding="utf-8")
        fm = FileManager(str(data), create_dirs=False, backup_enabled=False)
        assert fm.read_file() == '{"key": "val"}'


# ---------------------------------------------------------------------------
# write_file (atomic write)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWriteFile:
    def test_write_creates_file(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file('{"x": 1}')
        assert fm.file_path.exists()

    def test_write_content_correct(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("hello world")
        assert fm.file_path.read_text(encoding="utf-8") == "hello world"

    def test_write_overwrites_existing(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("first")
        fm.write_file("second")
        assert fm.file_path.read_text(encoding="utf-8") == "second"

    def test_no_temp_file_left_after_write(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("content")
        # No .tmp files should remain
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert tmp_files == []


# ---------------------------------------------------------------------------
# file_exists / get_file_size / get_modification_time
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFileMetadata:
    def test_file_exists_false_before_write(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert fm.file_exists() is False

    def test_file_exists_true_after_write(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("data")
        assert fm.file_exists() is True

    def test_get_file_size_zero_for_nonexistent(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert fm.get_file_size() == 0

    def test_get_file_size_matches_written_content(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        content = "hello"
        fm.write_file(content)
        assert fm.get_file_size() == fm.file_path.stat().st_size

    def test_get_modification_time_none_when_missing(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert fm.get_modification_time() is None

    def test_get_modification_time_returns_datetime_after_write(self, tmp_path: Path) -> None:
        from datetime import datetime

        fm = _fm(tmp_path)
        fm.write_file("x")
        mtime = fm.get_modification_time()
        assert isinstance(mtime, datetime)


# ---------------------------------------------------------------------------
# calculate_checksum
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCalculateChecksum:
    def test_returns_sha256_hex(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        content = "hello"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        assert fm.calculate_checksum(content) == expected

    def test_different_content_different_checksum(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        c1 = fm.calculate_checksum("abc")
        c2 = fm.calculate_checksum("xyz")
        assert c1 != c2

    def test_empty_string_has_checksum(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert len(fm.calculate_checksum("")) == 64


# ---------------------------------------------------------------------------
# verify_file_integrity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyFileIntegrity:
    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        assert fm.verify_file_integrity() is False

    def test_empty_file_returns_false_without_expected_checksum(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.file_path.write_text("", encoding="utf-8")
        assert fm.verify_file_integrity() is False

    def test_nonempty_file_returns_true_without_expected_checksum(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("some content")
        assert fm.verify_file_integrity() is True

    def test_matching_checksum_returns_true(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        content = "hello"
        fm.write_file(content)
        checksum = fm.calculate_checksum(content)
        assert fm.verify_file_integrity(checksum) is True

    def test_wrong_checksum_returns_false(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        fm.write_file("hello")
        assert fm.verify_file_integrity("deadbeef" * 8) is False


# ---------------------------------------------------------------------------
# create_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateBackup:
    def test_no_backup_when_file_does_not_exist(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=True)
        result = fm.create_backup()
        assert result is None

    def test_no_backup_when_disabled(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path, backup_enabled=False)
        fm.write_file("content")
        result = fm.create_backup()
        assert result is None

    def test_backup_created_when_enabled(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=True)
        fm.write_file('{"e": 1}')
        result = fm.create_backup()
        assert result is not None
        assert Path(result).exists()

    def test_backup_skipped_when_content_unchanged(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=True)
        fm.write_file("same content")
        first = fm.create_backup()
        assert first is not None  # first backup always created
        # Second backup with same content should be skipped (returns None)
        second = fm.create_backup()
        assert second is None

    def test_old_backups_cleaned_up(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_count=2, backup_enabled=True)
        # Create 4 backups with different content
        for i in range(4):
            fm.write_file(f"content-{i}")
            fm.create_backup()
            time.sleep(0.01)  # ensure distinct timestamps
        backup_files = list(fm.backup_dir.glob("data.backup_*.json"))
        assert len(backup_files) <= 2


# ---------------------------------------------------------------------------
# recover_from_backup
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecoverFromBackup:
    def test_returns_false_when_no_backup_files(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=True)
        result = fm.recover_from_backup()
        assert result is False

    def test_recovers_from_backup(self, tmp_path: Path) -> None:
        fm = FileManager(str(tmp_path / "data.json"), backup_enabled=True)
        fm.write_file("original content")
        fm.create_backup()
        # Now clobber the main file
        fm.file_path.write_text("corrupted", encoding="utf-8")
        ok = fm.recover_from_backup()
        assert ok is True
        assert "original content" in fm.file_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# exclusive_write_lock
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExclusiveWriteLock:
    def test_lock_context_yields(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        entered = False
        with fm.exclusive_write_lock():
            entered = True
        assert entered

    def test_lock_released_on_exception(self, tmp_path: Path) -> None:
        fm = _fm(tmp_path)
        with pytest.raises(RuntimeError):
            with fm.exclusive_write_lock():
                raise RuntimeError("inside lock")
        # If we reach here, the lock was released (no deadlock)
        with fm.exclusive_write_lock():
            pass  # should not hang


# ---------------------------------------------------------------------------
# create_dirs behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateDirs:
    def test_directories_created_when_create_dirs_true(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "data.json"
        FileManager(str(nested), create_dirs=True, backup_enabled=False)
        assert nested.parent.exists()

    def test_no_dir_creation_when_create_dirs_false(self, tmp_path: Path) -> None:
        nested = tmp_path / "missing_dir" / "data.json"
        # Should not raise even if the dir doesn't exist
        FileManager(str(nested), create_dirs=False, backup_enabled=False)
        assert not nested.parent.exists()
