"""Tests for file/binary_utils.py utilities."""

import hashlib

import pytest

from orb.infrastructure.utilities.file.binary_utils import (
    append_binary_file,
    get_file_hash,
    get_file_mime_type,
    is_binary_file,
    is_text_file,
    read_binary_file,
    write_binary_file,
)


@pytest.mark.unit
class TestReadBinaryFile:
    """Tests for read_binary_file."""

    def test_read_binary_file_returns_bytes(self, tmp_path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        result = read_binary_file(str(f))
        assert result == b"\x00\x01\x02\x03"

    def test_read_binary_file_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert read_binary_file(str(f)) == b""

    def test_read_binary_file_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Binary file not found"):
            read_binary_file(str(tmp_path / "nope.bin"))


@pytest.mark.unit
class TestWriteBinaryFile:
    """Tests for write_binary_file."""

    def test_write_binary_file_creates_file(self, tmp_path):
        target = tmp_path / "out.bin"
        write_binary_file(str(target), b"\xde\xad")
        assert target.read_bytes() == b"\xde\xad"

    def test_write_binary_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "out.bin"
        write_binary_file(str(target), b"\xff")
        assert target.exists()

    def test_write_binary_file_raises_for_non_bytes(self, tmp_path):
        with pytest.raises(TypeError, match="Content must be bytes"):
            write_binary_file(str(tmp_path / "out.bin"), "not bytes")  # type: ignore[arg-type]

    def test_write_binary_file_overwrites_existing(self, tmp_path):
        f = tmp_path / "overwrite.bin"
        f.write_bytes(b"\x00\x00")
        write_binary_file(str(f), b"\xff")
        assert f.read_bytes() == b"\xff"


@pytest.mark.unit
class TestAppendBinaryFile:
    """Tests for append_binary_file."""

    def test_append_binary_file_appends_content(self, tmp_path):
        f = tmp_path / "append.bin"
        f.write_bytes(b"\x01")
        append_binary_file(str(f), b"\x02")
        assert f.read_bytes() == b"\x01\x02"

    def test_append_binary_file_creates_file_if_missing(self, tmp_path):
        target = tmp_path / "new.bin"
        append_binary_file(str(target), b"\xaa")
        assert target.read_bytes() == b"\xaa"

    def test_append_binary_file_raises_for_non_bytes(self, tmp_path):
        with pytest.raises(TypeError, match="Content must be bytes"):
            append_binary_file(str(tmp_path / "f.bin"), "text")  # type: ignore[arg-type]


@pytest.mark.unit
class TestGetFileHash:
    """Tests for get_file_hash."""

    def test_get_file_hash_sha256_matches_known(self, tmp_path):
        f = tmp_path / "hash.txt"
        f.write_bytes(b"hello")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert get_file_hash(str(f)) == expected

    def test_get_file_hash_md5(self, tmp_path):
        f = tmp_path / "hash.txt"
        f.write_bytes(b"test")
        expected = hashlib.md5(b"test").hexdigest()  # nosec B324
        assert get_file_hash(str(f), algorithm="md5") == expected

    def test_get_file_hash_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert get_file_hash(str(f)) == expected

    def test_get_file_hash_unsupported_algorithm_raises(self, tmp_path):
        f = tmp_path / "x.bin"
        f.write_bytes(b"x")
        with pytest.raises(ValueError, match="Unsupported hash algorithm"):
            get_file_hash(str(f), algorithm="notreal")

    def test_get_file_hash_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            get_file_hash(str(tmp_path / "nope.bin"))


@pytest.mark.unit
class TestGetFileMimeType:
    """Tests for get_file_mime_type."""

    def test_get_file_mime_type_text_plain(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("data")
        mime = get_file_mime_type(str(f))
        assert mime == "text/plain"

    def test_get_file_mime_type_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("{}")
        mime = get_file_mime_type(str(f))
        assert "json" in mime

    def test_get_file_mime_type_unknown_returns_octet_stream(self, tmp_path):
        f = tmp_path / "file.xyz123unknownext"
        f.write_bytes(b"\x00")
        mime = get_file_mime_type(str(f))
        assert mime == "application/octet-stream"

    def test_get_file_mime_type_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            get_file_mime_type(str(tmp_path / "nope.txt"))


@pytest.mark.unit
class TestIsBinaryFile:
    """Tests for is_binary_file and is_text_file."""

    def test_is_binary_file_true_for_null_bytes(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02")
        assert is_binary_file(str(f)) is True

    def test_is_binary_file_false_for_text(self, tmp_path):
        f = tmp_path / "text.txt"
        f.write_text("Hello, world!")
        assert is_binary_file(str(f)) is False

    def test_is_text_file_true_for_text(self, tmp_path):
        f = tmp_path / "text.txt"
        f.write_text("Normal text content")
        assert is_text_file(str(f)) is True

    def test_is_text_file_false_for_binary(self, tmp_path):
        f = tmp_path / "bin.bin"
        f.write_bytes(b"\x00\xff\x00")
        assert is_text_file(str(f)) is False

    def test_is_binary_file_raises_for_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="File not found"):
            is_binary_file(str(tmp_path / "nope.bin"))
