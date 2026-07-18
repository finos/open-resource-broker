"""Tests for file/text_utils.py utilities."""

import pytest

from orb.infrastructure.utilities.file.text_utils import (
    append_text_file,
    read_text_file,
    read_text_lines,
    write_text_file,
    write_text_lines,
)


@pytest.mark.unit
class TestReadTextFile:
    """Tests for read_text_file."""

    def test_read_text_file_returns_content(self, tmp_path):
        f = tmp_path / "read.txt"
        f.write_text("hello world", encoding="utf-8")
        assert read_text_file(str(f)) == "hello world"

    def test_read_text_file_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert read_text_file(str(f)) == ""

    def test_read_text_file_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Text file not found"):
            read_text_file(str(tmp_path / "nope.txt"))

    def test_read_text_file_utf8_unicode(self, tmp_path):
        f = tmp_path / "unicode.txt"
        f.write_text("café", encoding="utf-8")
        assert read_text_file(str(f)) == "café"

    def test_read_text_file_custom_encoding(self, tmp_path):
        f = tmp_path / "latin.txt"
        f.write_bytes("caf\xe9".encode("latin-1"))
        result = read_text_file(str(f), encoding="latin-1")
        assert result == "caf\xe9"


@pytest.mark.unit
class TestWriteTextFile:
    """Tests for write_text_file."""

    def test_write_text_file_creates_file(self, tmp_path):
        target = tmp_path / "out.txt"
        write_text_file(str(target), "content")
        assert target.read_text(encoding="utf-8") == "content"

    def test_write_text_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "dir" / "out.txt"
        write_text_file(str(target), "data")
        assert target.exists()

    def test_write_text_file_overwrites_existing(self, tmp_path):
        f = tmp_path / "over.txt"
        f.write_text("old", encoding="utf-8")
        write_text_file(str(f), "new")
        assert f.read_text(encoding="utf-8") == "new"

    def test_write_text_file_unicode(self, tmp_path):
        target = tmp_path / "unicode.txt"
        write_text_file(str(target), "中文")
        assert target.read_text(encoding="utf-8") == "中文"


@pytest.mark.unit
class TestAppendTextFile:
    """Tests for append_text_file."""

    def test_append_text_file_appends_content(self, tmp_path):
        f = tmp_path / "append.txt"
        f.write_text("line1", encoding="utf-8")
        append_text_file(str(f), "\nline2")
        assert f.read_text(encoding="utf-8") == "line1\nline2"

    def test_append_text_file_creates_file_if_missing(self, tmp_path):
        target = tmp_path / "new.txt"
        append_text_file(str(target), "first")
        assert target.read_text(encoding="utf-8") == "first"

    def test_append_text_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "append.txt"
        append_text_file(str(target), "data")
        assert target.exists()


@pytest.mark.unit
class TestReadTextLines:
    """Tests for read_text_lines."""

    def test_read_text_lines_strips_whitespace_by_default(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("  line1  \nline2\n  line3\n", encoding="utf-8")
        lines = read_text_lines(str(f))
        # strip() removes the trailing blank produced by the trailing newline
        assert "line1" in lines
        assert "line2" in lines
        assert "line3" in lines

    def test_read_text_lines_no_strip(self, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("  line1  \nline2\n", encoding="utf-8")
        lines = read_text_lines(str(f), strip_whitespace=False)
        assert lines[0] == "  line1  \n"
        assert lines[1] == "line2\n"

    def test_read_text_lines_empty_file_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        assert read_text_lines(str(f)) == []

    def test_read_text_lines_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Text file not found"):
            read_text_lines(str(tmp_path / "nope.txt"))

    def test_read_text_lines_single_line(self, tmp_path):
        f = tmp_path / "single.txt"
        f.write_text("only line", encoding="utf-8")
        assert read_text_lines(str(f)) == ["only line"]


@pytest.mark.unit
class TestWriteTextLines:
    """Tests for write_text_lines."""

    def test_write_text_lines_adds_newlines_by_default(self, tmp_path):
        target = tmp_path / "lines.txt"
        write_text_lines(str(target), ["alpha", "beta"])
        content = target.read_text(encoding="utf-8")
        assert "alpha\n" in content
        assert "beta\n" in content

    def test_write_text_lines_no_add_newlines(self, tmp_path):
        target = tmp_path / "lines.txt"
        write_text_lines(str(target), ["alpha", "beta"], add_newlines=False)
        content = target.read_text(encoding="utf-8")
        assert content == "alphabeta"

    def test_write_text_lines_does_not_double_newline(self, tmp_path):
        target = tmp_path / "lines.txt"
        write_text_lines(str(target), ["line1\n", "line2\n"])
        content = target.read_text(encoding="utf-8")
        # Lines that already end with \n should not get an extra \n
        assert content.count("\n") == 2

    def test_write_text_lines_empty_list(self, tmp_path):
        target = tmp_path / "empty.txt"
        write_text_lines(str(target), [])
        assert target.read_text(encoding="utf-8") == ""

    def test_write_text_lines_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "lines.txt"
        write_text_lines(str(target), ["line"])
        assert target.exists()
