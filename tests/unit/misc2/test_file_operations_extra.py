"""Unit tests for uncovered branches in orb.infrastructure.utilities.file.file_operations."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from orb.infrastructure.utilities.file.file_operations import (
    copy_file,
    delete_file,
    get_file_access_time,
    get_file_creation_time,
    get_file_group,
    get_file_modification_time,
    get_file_owner,
    get_file_permissions,
    get_file_size,
    move_file,
    rename_file,
    set_file_owner_and_group,
    set_file_permissions,
    touch_file,
    with_temp_file,
)

# ---------------------------------------------------------------------------
# get_file_size — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFileSizeErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_size(str(tmp_path / "nope.txt"))

    def test_raises_os_error_on_stat_failure(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.path.getsize", side_effect=OSError("stat error")):
            with pytest.raises(OSError, match="Failed to get file size"):
                get_file_size(str(f))


# ---------------------------------------------------------------------------
# get_file_modification_time — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFileModificationTimeErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_modification_time(str(tmp_path / "nope.txt"))

    def test_raises_os_error(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.path.getmtime", side_effect=OSError("mtime error")):
            with pytest.raises(OSError, match="Failed to get modification time"):
                get_file_modification_time(str(f))


# ---------------------------------------------------------------------------
# get_file_creation_time — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFileCreationTimeErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_creation_time(str(tmp_path / "nope.txt"))

    def test_raises_os_error(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.path.getctime", side_effect=OSError("ctime error")):
            with pytest.raises(OSError, match="Failed to get creation time"):
                get_file_creation_time(str(f))


# ---------------------------------------------------------------------------
# get_file_access_time — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFileAccessTimeErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_access_time(str(tmp_path / "nope.txt"))

    def test_raises_os_error(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.path.getatime", side_effect=OSError("atime error")):
            with pytest.raises(OSError, match="Failed to get access time"):
                get_file_access_time(str(f))


# ---------------------------------------------------------------------------
# delete_file — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteFileErrors:
    def test_raises_file_not_found_when_missing(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            delete_file(str(tmp_path / "nope.txt"))

    def test_raises_os_error_on_remove_failure(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.remove", side_effect=OSError("remove error")):
            with pytest.raises(OSError, match="Failed to delete file"):
                delete_file(str(f))


# ---------------------------------------------------------------------------
# copy_file — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCopyFileErrors:
    def test_raises_file_not_found_when_source_missing(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            copy_file(str(tmp_path / "nope.txt"), str(tmp_path / "dst.txt"))

    def test_raises_os_error_on_copy_failure(self, tmp_path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        with patch("shutil.copy2", side_effect=OSError("copy error")):
            with pytest.raises(OSError, match="Failed to copy file"):
                copy_file(str(src), str(tmp_path / "dst.txt"))


# ---------------------------------------------------------------------------
# move_file — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMoveFileErrors:
    def test_raises_file_not_found_when_source_missing(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            move_file(str(tmp_path / "nope.txt"), str(tmp_path / "dst.txt"))

    def test_raises_os_error_on_move_failure(self, tmp_path) -> None:
        src = tmp_path / "src.txt"
        src.write_text("data")
        with patch("shutil.move", side_effect=OSError("move error")):
            with pytest.raises(OSError, match="Failed to move file"):
                move_file(str(src), str(tmp_path / "dst.txt"))


# ---------------------------------------------------------------------------
# rename_file — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRenameFileErrors:
    def test_raises_file_not_found_when_missing(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            rename_file(str(tmp_path / "nope.txt"), "new.txt")

    def test_raises_os_error_on_rename_failure(self, tmp_path) -> None:
        f = tmp_path / "old.txt"
        f.write_text("data")
        with patch("os.rename", side_effect=OSError("rename error")):
            with pytest.raises(OSError, match="Failed to rename"):
                rename_file(str(f), "new.txt")


# ---------------------------------------------------------------------------
# touch_file — error path
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTouchFileErrors:
    def test_raises_os_error_on_open_failure(self, tmp_path) -> None:
        f = tmp_path / "cannot_touch.txt"
        with patch("builtins.open", side_effect=OSError("touch error")):
            with pytest.raises(OSError, match="Failed to touch file"):
                touch_file(str(f))


# ---------------------------------------------------------------------------
# get_file_permissions — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFilePermissionsErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_permissions(str(tmp_path / "nope.txt"))

    def test_raises_os_error_on_stat_failure(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.stat", side_effect=OSError("stat error")):
            with pytest.raises(OSError, match="Failed to get permissions"):
                get_file_permissions(str(f))


# ---------------------------------------------------------------------------
# set_file_permissions — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetFilePermissionsErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            set_file_permissions(str(tmp_path / "nope.txt"), 0o644)

    def test_raises_os_error_on_chmod_failure(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.chmod", side_effect=OSError("chmod error")):
            with pytest.raises(OSError, match="Failed to set permissions"):
                set_file_permissions(str(f), 0o644)


# ---------------------------------------------------------------------------
# get_file_owner / get_file_group — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetFileOwnerGroupErrors:
    def test_get_owner_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_owner(str(tmp_path / "nope.txt"))

    def test_get_owner_raises_os_error(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.stat", side_effect=OSError("stat error")):
            with pytest.raises(OSError, match="Failed to get owner"):
                get_file_owner(str(f))

    def test_get_group_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            get_file_group(str(tmp_path / "nope.txt"))

    def test_get_group_raises_os_error(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.stat", side_effect=OSError("stat error")):
            with pytest.raises(OSError, match="Failed to get group"):
                get_file_group(str(f))


# ---------------------------------------------------------------------------
# set_file_owner_and_group — error paths
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSetFileOwnerAndGroupErrors:
    def test_raises_file_not_found(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            set_file_owner_and_group(str(tmp_path / "nope.txt"), 0, 0)

    def test_raises_os_error_on_chown_failure(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        with patch("os.chown", side_effect=OSError("chown error")):
            with pytest.raises(OSError, match="Failed to set owner/group"):
                set_file_owner_and_group(str(f), 0, 0)


# ---------------------------------------------------------------------------
# with_temp_file — context manager
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWithTempFile:
    def test_context_manager_yields_path_and_cleans_up(self) -> None:
        with with_temp_file(suffix=".tmp") as path:
            assert os.path.isfile(path)
        assert not os.path.isfile(path)

    def test_cleanup_even_after_manual_delete(self) -> None:
        """Context manager does not raise if file was already deleted."""
        with with_temp_file() as path:
            os.unlink(path)
        # no exception raised
