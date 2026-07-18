"""Tests for file/directory_utils.py utilities."""

import os

import pytest

from orb.infrastructure.utilities.file.directory_utils import (
    change_directory,
    create_temp_directory,
    delete_directory,
    directory_exists,
    ensure_directory_exists,
    ensure_parent_directory_exists,
    find_files,
    get_current_directory,
    get_home_directory,
    list_directories,
    list_files,
)


@pytest.mark.unit
class TestEnsureDirectoryExists:
    """Tests for ensure_directory_exists."""

    def test_creates_new_directory(self, tmp_path):
        target = tmp_path / "new_dir"
        ensure_directory_exists(str(target))
        assert target.is_dir()

    def test_creates_nested_directories(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        ensure_directory_exists(str(target))
        assert target.is_dir()

    def test_no_error_if_directory_already_exists(self, tmp_path):
        ensure_directory_exists(str(tmp_path))  # should not raise


@pytest.mark.unit
class TestEnsureParentDirectoryExists:
    """Tests for ensure_parent_directory_exists."""

    def test_creates_parent_directory(self, tmp_path):
        file_path = tmp_path / "sub" / "file.txt"
        ensure_parent_directory_exists(str(file_path))
        assert (tmp_path / "sub").is_dir()

    def test_no_error_when_parent_exists(self, tmp_path):
        file_path = tmp_path / "file.txt"
        ensure_parent_directory_exists(str(file_path))  # should not raise

    def test_handles_empty_parent(self, tmp_path):
        # just a filename with no dir component
        ensure_parent_directory_exists("file.txt")  # should not raise


@pytest.mark.unit
class TestDirectoryExists:
    """Tests for directory_exists."""

    def test_returns_true_for_real_directory(self, tmp_path):
        assert directory_exists(str(tmp_path)) is True

    def test_returns_false_for_missing_path(self, tmp_path):
        assert directory_exists(str(tmp_path / "nope")) is False

    def test_returns_false_for_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        assert directory_exists(str(f)) is False


@pytest.mark.unit
class TestDeleteDirectory:
    """Tests for delete_directory."""

    def test_delete_empty_directory(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        delete_directory(str(d))
        assert not d.exists()

    def test_delete_non_empty_directory_recursive(self, tmp_path):
        d = tmp_path / "full"
        d.mkdir()
        (d / "file.txt").write_text("data")
        delete_directory(str(d), recursive=True)
        assert not d.exists()

    def test_delete_non_empty_directory_non_recursive_raises(self, tmp_path):
        d = tmp_path / "full"
        d.mkdir()
        (d / "file.txt").write_text("data")
        with pytest.raises(OSError):
            delete_directory(str(d), recursive=False)

    def test_delete_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            delete_directory(str(tmp_path / "nope"))


@pytest.mark.unit
class TestListFiles:
    """Tests for list_files."""

    def test_list_files_returns_files_only(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        sub = tmp_path / "sub"
        sub.mkdir()
        files = list_files(str(tmp_path))
        assert len(files) == 2
        assert all(os.path.isfile(f) for f in files)

    def test_list_files_with_pattern(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        files = list_files(str(tmp_path), pattern="*.py")
        assert len(files) == 1
        assert files[0].endswith("a.py")

    def test_list_files_recursive(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("c")
        (tmp_path / "a.txt").write_text("a")
        files = list_files(str(tmp_path), recursive=True)
        assert len(files) == 2

    def test_list_files_recursive_with_pattern(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("c")
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        files = list_files(str(tmp_path), pattern="*.py", recursive=True)
        assert len(files) == 2

    def test_list_files_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            list_files(str(tmp_path / "nope"))


@pytest.mark.unit
class TestListDirectories:
    """Tests for list_directories."""

    def test_list_directories_returns_dirs_only(self, tmp_path):
        (tmp_path / "sub1").mkdir()
        (tmp_path / "sub2").mkdir()
        (tmp_path / "file.txt").write_text("x")
        dirs = list_directories(str(tmp_path))
        assert len(dirs) == 2
        assert all(os.path.isdir(d) for d in dirs)

    def test_list_directories_recursive(self, tmp_path):
        sub = tmp_path / "a"
        sub.mkdir()
        (sub / "b").mkdir()
        dirs = list_directories(str(tmp_path), recursive=True)
        assert len(dirs) == 2

    def test_list_directories_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            list_directories(str(tmp_path / "nope"))


@pytest.mark.unit
class TestFindFiles:
    """Tests for find_files."""

    def test_find_files_by_name_pattern(self, tmp_path):
        (tmp_path / "alpha.py").write_text("a")
        (tmp_path / "beta.txt").write_text("b")
        found = find_files(str(tmp_path), name_pattern="*.py")
        assert len(found) == 1

    def test_find_files_by_content_pattern(self, tmp_path):
        (tmp_path / "match.txt").write_text("hello world")
        (tmp_path / "no.txt").write_text("goodbye world")
        found = find_files(str(tmp_path), content_pattern="hello")
        assert len(found) == 1
        assert "match.txt" in found[0]

    def test_find_files_name_and_content(self, tmp_path):
        (tmp_path / "a.py").write_text("needle")
        (tmp_path / "b.py").write_text("hay")
        (tmp_path / "c.txt").write_text("needle")
        found = find_files(str(tmp_path), name_pattern="*.py", content_pattern="needle")
        assert len(found) == 1
        assert "a.py" in found[0]

    def test_find_files_missing_directory_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            find_files(str(tmp_path / "nope"))

    def test_find_files_no_match_returns_empty(self, tmp_path):
        (tmp_path / "file.txt").write_text("data")
        found = find_files(str(tmp_path), name_pattern="*.py")
        assert found == []


@pytest.mark.unit
class TestDirectoryNavigation:
    """Tests for get_current_directory, change_directory, get_home_directory."""

    def test_get_current_directory_returns_string(self):
        cwd = get_current_directory()
        assert isinstance(cwd, str)
        assert os.path.isdir(cwd)

    def test_get_home_directory_returns_existing_path(self):
        home = get_home_directory()
        assert os.path.isdir(home)

    def test_change_directory_changes_cwd(self, tmp_path):
        original = get_current_directory()
        try:
            change_directory(str(tmp_path))
            assert os.path.realpath(get_current_directory()) == os.path.realpath(str(tmp_path))
        finally:
            os.chdir(original)

    def test_change_directory_raises_for_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Directory not found"):
            change_directory(str(tmp_path / "nope"))


@pytest.mark.unit
class TestCreateTempDirectory:
    """Tests for create_temp_directory."""

    def test_create_temp_directory_returns_existing_dir(self):
        import shutil

        path = create_temp_directory()
        try:
            assert os.path.isdir(path)
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_create_temp_directory_with_suffix(self):
        import shutil

        path = create_temp_directory(suffix="_orb")
        try:
            assert path.endswith("_orb")
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_create_temp_directory_in_custom_dir(self, tmp_path):
        import shutil

        path = create_temp_directory(dir=str(tmp_path))
        try:
            assert str(tmp_path) in path
        finally:
            shutil.rmtree(path, ignore_errors=True)
