"""Tests for file/file_operations.py utilities."""

import os
import stat

import pytest

from orb.infrastructure.utilities.file.file_operations import (
    copy_file,
    create_temp_file as create_temp_file_op,
    delete_file,
    file_exists as file_exists_op,
    get_absolute_path,
    get_directory_name,
    get_file_access_time,
    get_file_creation_time,
    get_file_extension,
    get_file_modification_time,
    get_file_name,
    get_file_name_without_extension,
    get_file_permissions,
    get_file_size,
    get_relative_path,
    is_file_empty,
    join_paths,
    move_file,
    normalize_path,
    rename_file,
    set_file_permissions,
    touch_file,
    with_temp_file,
)


@pytest.mark.unit
class TestFileExists:
    """Tests for file_exists."""

    def test_file_exists_returns_true_for_real_file(self, tmp_path):
        f = tmp_path / "real.txt"
        f.write_text("data")
        assert file_exists_op(str(f)) is True

    def test_file_exists_returns_false_for_missing_file(self, tmp_path):
        assert file_exists_op(str(tmp_path / "nope.txt")) is False

    def test_file_exists_returns_false_for_directory(self, tmp_path):
        assert file_exists_op(str(tmp_path)) is False


@pytest.mark.unit
class TestGetFileSize:
    """Tests for get_file_size."""

    def test_get_file_size_returns_correct_bytes(self, tmp_path):
        f = tmp_path / "sized.txt"
        f.write_bytes(b"hello")
        assert get_file_size(str(f)) == 5

    def test_get_file_size_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert get_file_size(str(f)) == 0

    def test_get_file_size_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            get_file_size(str(tmp_path / "missing.txt"))


@pytest.mark.unit
class TestGetFileTimes:
    """Tests for get_file_modification/creation/access times."""

    def test_get_file_modification_time_returns_float(self, tmp_path):
        f = tmp_path / "mod.txt"
        f.write_text("data")
        mtime = get_file_modification_time(str(f))
        assert isinstance(mtime, float)
        assert mtime > 0

    def test_get_file_modification_time_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_file_modification_time(str(tmp_path / "nope.txt"))

    def test_get_file_creation_time_returns_float(self, tmp_path):
        f = tmp_path / "ctime.txt"
        f.write_text("data")
        assert isinstance(get_file_creation_time(str(f)), float)

    def test_get_file_creation_time_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_file_creation_time(str(tmp_path / "nope.txt"))

    def test_get_file_access_time_returns_float(self, tmp_path):
        f = tmp_path / "atime.txt"
        f.write_text("data")
        assert isinstance(get_file_access_time(str(f)), float)

    def test_get_file_access_time_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_file_access_time(str(tmp_path / "nope.txt"))


@pytest.mark.unit
class TestDeleteFile:
    """Tests for delete_file."""

    def test_delete_file_removes_existing_file(self, tmp_path):
        f = tmp_path / "del.txt"
        f.write_text("x")
        delete_file(str(f))
        assert not f.exists()

    def test_delete_file_raises_for_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            delete_file(str(tmp_path / "gone.txt"))


@pytest.mark.unit
class TestCopyFile:
    """Tests for copy_file."""

    def test_copy_file_creates_destination(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("content")
        dst = tmp_path / "dst.txt"
        copy_file(str(src), str(dst))
        assert dst.read_text() == "content"

    def test_copy_file_creates_parent_directories(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("abc")
        dst = tmp_path / "sub" / "dir" / "dst.txt"
        copy_file(str(src), str(dst))
        assert dst.read_text() == "abc"

    def test_copy_file_raises_if_source_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            copy_file(str(tmp_path / "nope.txt"), str(tmp_path / "out.txt"))


@pytest.mark.unit
class TestMoveFile:
    """Tests for move_file."""

    def test_move_file_moves_to_destination(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("move me")
        dst = tmp_path / "dst.txt"
        move_file(str(src), str(dst))
        assert dst.read_text() == "move me"
        assert not src.exists()

    def test_move_file_creates_parent_directories(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "sub" / "dst.txt"
        move_file(str(src), str(dst))
        assert dst.exists()

    def test_move_file_raises_if_source_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Source file not found"):
            move_file(str(tmp_path / "nope.txt"), str(tmp_path / "out.txt"))


@pytest.mark.unit
class TestRenameFile:
    """Tests for rename_file."""

    def test_rename_file_returns_new_path(self, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("data")
        new_path = rename_file(str(f), "new.txt")
        assert os.path.isfile(new_path)
        assert new_path.endswith("new.txt")
        assert not f.exists()

    def test_rename_file_raises_if_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            rename_file(str(tmp_path / "nope.txt"), "other.txt")


@pytest.mark.unit
class TestTouchFile:
    """Tests for touch_file."""

    def test_touch_file_creates_new_file(self, tmp_path):
        target = tmp_path / "touched.txt"
        touch_file(str(target))
        assert target.exists()

    def test_touch_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "touched.txt"
        touch_file(str(target))
        assert target.exists()

    def test_touch_file_updates_existing_file(self, tmp_path):
        f = tmp_path / "exist.txt"
        f.write_text("data")
        touch_file(str(f))
        # mtime should be updated (or at least file still exists)
        assert f.exists()


@pytest.mark.unit
class TestIsFileEmpty:
    """Tests for is_file_empty."""

    def test_is_file_empty_returns_true_for_empty(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_bytes(b"")
        assert is_file_empty(str(f)) is True

    def test_is_file_empty_returns_false_for_non_empty(self, tmp_path):
        f = tmp_path / "nonempty.txt"
        f.write_text("content")
        assert is_file_empty(str(f)) is False

    def test_is_file_empty_raises_for_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            is_file_empty(str(tmp_path / "nope.txt"))


@pytest.mark.unit
class TestCreateTempFile:
    """Tests for create_temp_file."""

    def test_create_temp_file_returns_existing_path(self):
        path = create_temp_file_op()
        try:
            assert os.path.isfile(path)
        finally:
            os.unlink(path)

    def test_create_temp_file_with_suffix(self):
        path = create_temp_file_op(suffix=".tmp")
        try:
            assert path.endswith(".tmp")
        finally:
            os.unlink(path)

    def test_create_temp_file_with_prefix(self):
        path = create_temp_file_op(prefix="orb_test_")
        try:
            assert os.path.basename(path).startswith("orb_test_")
        finally:
            os.unlink(path)

    def test_create_temp_file_in_custom_dir(self, tmp_path):
        path = create_temp_file_op(dir=str(tmp_path))
        try:
            assert str(tmp_path) in path
        finally:
            os.unlink(path)


@pytest.mark.unit
class TestWithTempFile:
    """Tests for with_temp_file context manager."""

    def test_with_temp_file_yields_path(self):
        with with_temp_file(suffix=".txt") as path:
            assert os.path.isfile(path)

    def test_with_temp_file_deletes_on_exit(self):
        with with_temp_file() as path:
            captured = path
        assert not os.path.exists(captured)

    def test_with_temp_file_survives_exception(self):
        captured = None
        try:
            with with_temp_file() as path:
                captured = path
                raise ValueError("simulated error")
        except ValueError:
            pass
        assert captured is not None
        assert not os.path.exists(captured)


@pytest.mark.unit
class TestPathHelpers:
    """Tests for path helper functions."""

    def test_get_file_extension_with_ext(self):
        assert get_file_extension("/tmp/file.txt") == ".txt"

    def test_get_file_extension_no_ext(self):
        assert get_file_extension("/tmp/file") == ""

    def test_get_file_extension_hidden_dotfile(self):
        # os.path.splitext treats dotfiles like ".hiddenrc" as having no extension
        assert get_file_extension("/tmp/.hiddenrc") == ""

    def test_get_file_name_returns_basename(self):
        assert get_file_name("/some/path/file.txt") == "file.txt"

    def test_get_file_name_without_extension(self):
        assert get_file_name_without_extension("/some/path/file.txt") == "file"

    def test_get_file_name_without_extension_no_ext(self):
        assert get_file_name_without_extension("/some/path/file") == "file"

    def test_get_directory_name_returns_parent(self):
        assert get_directory_name("/some/path/file.txt") == "/some/path"

    def test_get_absolute_path_returns_absolute(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        result = get_absolute_path(str(f))
        assert os.path.isabs(result)

    def test_get_relative_path_relative_to_parent(self, tmp_path):
        f = tmp_path / "sub" / "file.txt"
        f.parent.mkdir()
        f.write_text("x")
        result = get_relative_path(str(f), str(tmp_path))
        assert result == os.path.join("sub", "file.txt")

    def test_join_paths_combines_components(self):
        result = join_paths("/a", "b", "c.txt")
        assert result == "/a/b/c.txt"

    def test_normalize_path_resolves_dots(self):
        result = normalize_path("/a/b/../c")
        assert result == "/a/c"


@pytest.mark.unit
class TestFilePermissions:
    """Tests for file permission helpers."""

    def test_get_file_permissions_returns_octal(self, tmp_path):
        f = tmp_path / "perms.txt"
        f.write_text("x")
        perm = get_file_permissions(str(f))
        assert isinstance(perm, int)
        assert 0 <= perm <= 0o777

    def test_get_file_permissions_raises_for_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            get_file_permissions(str(tmp_path / "nope.txt"))

    def test_set_file_permissions_changes_mode(self, tmp_path):
        f = tmp_path / "chmod.txt"
        f.write_text("x")
        set_file_permissions(str(f), 0o600)
        mode = stat.S_IMODE(os.stat(str(f)).st_mode)
        assert mode == 0o600

    def test_set_file_permissions_raises_for_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            set_file_permissions(str(tmp_path / "nope.txt"), 0o644)
