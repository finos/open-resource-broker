"""Tests for file/yaml_utils.py and file/json_utils.py."""

import json

import pytest

from orb.infrastructure.utilities.file.json_utils import read_json_file, write_json_file
from orb.infrastructure.utilities.file.yaml_utils import read_yaml_file, write_yaml_file


@pytest.mark.unit
class TestReadYamlFile:
    """Tests for read_yaml_file."""

    def test_read_yaml_file_returns_dict(self, tmp_path):
        f = tmp_path / "cfg.yaml"
        f.write_text("key: value\nnested:\n  x: 1\n", encoding="utf-8")
        result = read_yaml_file(str(f))
        assert result == {"key": "value", "nested": {"x": 1}}

    def test_read_yaml_file_empty_file_returns_empty_dict(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("", encoding="utf-8")
        assert read_yaml_file(str(f)) == {}

    def test_read_yaml_file_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="YAML file not found"):
            read_yaml_file(str(tmp_path / "nope.yaml"))

    def test_read_yaml_file_invalid_yaml_raises(self, tmp_path):
        import yaml

        f = tmp_path / "bad.yaml"
        f.write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            read_yaml_file(str(f))

    def test_read_yaml_file_list_value(self, tmp_path):
        f = tmp_path / "list.yaml"
        f.write_text("items:\n  - a\n  - b\n", encoding="utf-8")
        result = read_yaml_file(str(f))
        assert result["items"] == ["a", "b"]


@pytest.mark.unit
class TestWriteYamlFile:
    """Tests for write_yaml_file."""

    def test_write_yaml_file_creates_valid_yaml(self, tmp_path):
        import yaml

        target = tmp_path / "out.yaml"
        write_yaml_file(str(target), {"hello": "world", "num": 42})
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert loaded == {"hello": "world", "num": 42}

    def test_write_yaml_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "cfg.yaml"
        write_yaml_file(str(target), {"a": 1})
        assert target.exists()

    def test_write_yaml_file_roundtrip(self, tmp_path):
        target = tmp_path / "roundtrip.yaml"
        original = {"key": "value", "nested": {"x": [1, 2, 3]}}
        write_yaml_file(str(target), original)
        result = read_yaml_file(str(target))
        assert result == original

    def test_write_yaml_file_empty_dict(self, tmp_path):
        target = tmp_path / "empty.yaml"
        write_yaml_file(str(target), {})
        result = read_yaml_file(str(target))
        assert result == {}


@pytest.mark.unit
class TestReadJsonFile:
    """Tests for read_json_file (file/json_utils)."""

    def test_read_json_file_returns_dict(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value", "num": 99}', encoding="utf-8")
        result = read_json_file(str(f))
        assert result == {"key": "value", "num": 99}

    def test_read_json_file_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="JSON file not found"):
            read_json_file(str(tmp_path / "nope.json"))

    def test_read_json_file_invalid_json_raises(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("{bad json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json_file(str(f))

    def test_read_json_file_nested(self, tmp_path):
        f = tmp_path / "nested.json"
        data = {"outer": {"inner": [1, 2, 3]}}
        f.write_text(json.dumps(data), encoding="utf-8")
        assert read_json_file(str(f)) == data


@pytest.mark.unit
class TestWriteJsonFile:
    """Tests for write_json_file (file/json_utils)."""

    def test_write_json_file_creates_valid_json(self, tmp_path):
        target = tmp_path / "out.json"
        write_json_file(str(target), {"hello": "world"})
        loaded = json.loads(target.read_text(encoding="utf-8"))
        assert loaded == {"hello": "world"}

    def test_write_json_file_with_custom_indent(self, tmp_path):
        target = tmp_path / "indented.json"
        write_json_file(str(target), {"a": 1}, indent=4)
        content = target.read_text(encoding="utf-8")
        assert "    " in content

    def test_write_json_file_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "sub" / "out.json"
        write_json_file(str(target), {"x": "y"})
        assert target.exists()

    def test_write_json_file_roundtrip(self, tmp_path):
        target = tmp_path / "roundtrip.json"
        original = {"numbers": [1, 2, 3], "flag": True, "nested": {"a": None}}
        write_json_file(str(target), original)
        result = read_json_file(str(target))
        assert result == original

    def test_write_json_file_non_serializable_raises(self, tmp_path):
        target = tmp_path / "bad.json"
        with pytest.raises(TypeError, match="Failed to serialize"):
            write_json_file(str(target), {"key": object()})  # type: ignore[arg-type]
