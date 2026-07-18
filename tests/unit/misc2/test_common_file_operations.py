"""Unit tests for orb.infrastructure.utilities.common.file_operations."""

from __future__ import annotations

import json
import os

import pytest

from orb.infrastructure.utilities.common.file_operations import (
    create_temp_directory,
    create_temp_file,
    directory_exists,
    ensure_directory_exists,
    ensure_parent_directory_exists,
    file_exists,
    get_file_size,
    read_json_file,
    read_text_file,
    write_json_file,
    write_text_file,
)


@pytest.mark.unit
class TestEnsureDirectoryExists:
    def test_creates_directory_if_missing(self, tmp_path) -> None:
        target = str(tmp_path / "new_dir")
        ensure_directory_exists(target)
        assert os.path.isdir(target)

    def test_idempotent_for_existing_directory(self, tmp_path) -> None:
        ensure_directory_exists(str(tmp_path))  # already exists — no error
        assert os.path.isdir(str(tmp_path))

    def test_creates_nested_directories(self, tmp_path) -> None:
        target = str(tmp_path / "a" / "b" / "c")
        ensure_directory_exists(target)
        assert os.path.isdir(target)


@pytest.mark.unit
class TestEnsureParentDirectoryExists:
    def test_creates_parent_directory(self, tmp_path) -> None:
        file_path = str(tmp_path / "sub" / "file.txt")
        ensure_parent_directory_exists(file_path)
        assert os.path.isdir(str(tmp_path / "sub"))

    def test_empty_directory_component_is_noop(self, tmp_path) -> None:
        # file path with no directory component (just a name)
        ensure_parent_directory_exists("just_a_name.txt")  # should not raise


@pytest.mark.unit
class TestReadTextFile:
    def test_reads_content(self, tmp_path) -> None:
        f = tmp_path / "hello.txt"
        f.write_text("hello world", encoding="utf-8")
        assert read_text_file(str(f)) == "hello world"

    def test_raises_for_missing_file(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            read_text_file(str(tmp_path / "nope.txt"))


@pytest.mark.unit
class TestWriteTextFile:
    def test_writes_content(self, tmp_path) -> None:
        f = tmp_path / "out.txt"
        write_text_file(str(f), "content")
        assert f.read_text(encoding="utf-8") == "content"

    def test_creates_parent_directories(self, tmp_path) -> None:
        f = tmp_path / "deep" / "nested" / "file.txt"
        write_text_file(str(f), "data")
        assert f.exists()


@pytest.mark.unit
class TestReadJsonFile:
    def test_reads_valid_json(self, tmp_path) -> None:
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = read_json_file(str(f))
        assert result == {"key": "value"}

    def test_raises_for_invalid_json(self, tmp_path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json_file(str(f))

    def test_raises_for_missing_file(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            read_json_file(str(tmp_path / "nope.json"))


@pytest.mark.unit
class TestWriteJsonFile:
    def test_writes_valid_json(self, tmp_path) -> None:
        f = tmp_path / "out.json"
        write_json_file(str(f), {"a": 1})
        loaded = json.loads(f.read_text(encoding="utf-8"))
        assert loaded == {"a": 1}

    def test_creates_parent_directories(self, tmp_path) -> None:
        f = tmp_path / "deep" / "out.json"
        write_json_file(str(f), {"x": 2})
        assert f.exists()

    def test_uses_specified_indent(self, tmp_path) -> None:
        f = tmp_path / "indented.json"
        write_json_file(str(f), {"k": "v"}, indent=4)
        content = f.read_text(encoding="utf-8")
        assert "    " in content  # indent=4


@pytest.mark.unit
class TestFileExists:
    def test_true_for_existing_file(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert file_exists(str(f)) is True

    def test_false_for_missing(self, tmp_path) -> None:
        assert file_exists(str(tmp_path / "nope.txt")) is False

    def test_false_for_directory(self, tmp_path) -> None:
        assert file_exists(str(tmp_path)) is False


@pytest.mark.unit
class TestDirectoryExists:
    def test_true_for_existing_dir(self, tmp_path) -> None:
        assert directory_exists(str(tmp_path)) is True

    def test_false_for_missing(self, tmp_path) -> None:
        assert directory_exists(str(tmp_path / "nope")) is False

    def test_false_for_file(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_text("x")
        assert directory_exists(str(f)) is False


@pytest.mark.unit
class TestGetFileSize:
    def test_returns_byte_count(self, tmp_path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"12345")
        assert get_file_size(str(f)) == 5


@pytest.mark.unit
class TestCreateTempFile:
    def test_creates_file(self) -> None:
        path = create_temp_file()
        try:
            assert os.path.isfile(path)
        finally:
            os.unlink(path)

    def test_respects_suffix(self) -> None:
        path = create_temp_file(suffix=".log")
        try:
            assert path.endswith(".log")
        finally:
            os.unlink(path)

    def test_respects_prefix(self) -> None:
        path = create_temp_file(prefix="orb_")
        try:
            assert os.path.basename(path).startswith("orb_")
        finally:
            os.unlink(path)


@pytest.mark.unit
class TestCreateTempDirectory:
    def test_creates_directory(self) -> None:
        path = create_temp_directory()
        try:
            assert os.path.isdir(path)
        finally:
            os.rmdir(path)

    def test_respects_suffix(self) -> None:
        path = create_temp_directory(suffix="_test")
        try:
            assert path.endswith("_test")
        finally:
            os.rmdir(path)
