"""Smoke tests for helper functions in tests/providers/aws/live/scenarios.py."""

from tests.providers.aws.live.scenarios import _load_template_vm_types


class TestLoadTemplateVmTypes:
    def test_known_template_returns_nonempty_dict(self) -> None:
        """EC2Fleet-Maintain-OnDemand exists in config/aws_templates.json and has vmTypes."""
        result = _load_template_vm_types("EC2Fleet-Maintain-OnDemand")
        assert isinstance(result, dict), "Expected a dict"
        assert result, "Expected a non-empty dict for EC2Fleet-Maintain-OnDemand"

    def test_unknown_template_returns_empty_dict(self) -> None:
        """An unrecognised template ID should silently return {}."""
        result = _load_template_vm_types("DoesNotExist-Template-ID")
        assert result == {}

    def test_file_not_found_returns_empty_dict(self, monkeypatch) -> None:
        """FileNotFoundError during open() is swallowed and returns {}."""
        import builtins
        from pathlib import Path

        def fake_open(path, *args, **kwargs):
            raise FileNotFoundError(f"injected: {path}")

        monkeypatch.setattr(builtins, "open", fake_open)
        # templates_path.exists() must return True to reach the open() call
        monkeypatch.setattr(Path, "exists", lambda self: True)
        result = _load_template_vm_types("EC2Fleet-Maintain-OnDemand")
        assert result == {}

    def test_json_decode_error_returns_empty_dict(self, monkeypatch) -> None:
        """json.JSONDecodeError during json.load() is swallowed and returns {}."""
        import builtins
        import io
        from pathlib import Path

        def fake_open(path, *args, **kwargs):
            return io.StringIO("<<<not valid json>>>")

        monkeypatch.setattr(builtins, "open", fake_open)
        monkeypatch.setattr(Path, "exists", lambda self: True)
        result = _load_template_vm_types("EC2Fleet-Maintain-OnDemand")
        assert result == {}

    def test_unexpected_exception_is_reraised(self, monkeypatch) -> None:
        """Unexpected exceptions (not FileNotFoundError/JSONDecodeError) are re-raised."""
        import builtins

        def exploding_open(path, *args, **kwargs):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(builtins, "open", exploding_open)
        from pathlib import Path

        monkeypatch.setattr(Path, "exists", lambda self: True)
        import pytest

        with pytest.raises(RuntimeError, match="disk on fire"):
            _load_template_vm_types("EC2Fleet-Maintain-OnDemand")
