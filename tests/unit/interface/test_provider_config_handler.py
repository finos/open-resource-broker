"""Unit tests for provider_config_handler.

NOTE: All handlers in this module bypass CQRS — they read/write config.json directly
via get_config_location() instead of dispatching through a command/query bus. Tests
reflect current behaviour and include TODO markers where the violation exists.
"""

import argparse
import json
from typing import Any
from unittest.mock import patch

import pytest

from orb.application.dto.interface_response import InterfaceResponse


@pytest.fixture(autouse=True)
def register_aws_cli_spec():
    """Register AWS CLI spec for all tests in this module."""
    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.providers.aws.cli.aws_cli_spec import AWSCLISpec

    CLISpecRegistry.register("aws", AWSCLISpec())
    yield
    # Clean up after test
    CLISpecRegistry.clear()


def exit_code(result: dict[str, Any] | InterfaceResponse) -> int:
    """Extract exit_code from either a dict or InterfaceResponse."""
    if isinstance(result, InterfaceResponse):
        return result.exit_code
    return result.get("exit_code", 0)  # type: ignore[return-value]


def _ns(**kwargs) -> argparse.Namespace:
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def _write_config(config_dir, data: dict) -> None:
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps(data))


def _base_config(providers=None) -> dict:
    return {
        "provider": {
            "providers": providers
            if providers is not None
            else [
                {
                    "name": "aws-default",
                    "type": "aws",
                    "enabled": True,
                    "config": {"profile": "default", "region": "us-east-1"},
                }
            ]
        }
    }


# ---------------------------------------------------------------------------
# handle_provider_add
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderAdd:
    @pytest.mark.asyncio
    async def test_missing_config_file_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_add

        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(
                provider_type="aws",
                aws_profile="default",
                aws_region="us-east-1",
                name=None,
                discover=False,
            )
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_missing_aws_profile_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(
                provider_type="aws",
                aws_profile=None,
                aws_region="us-east-1",
                name=None,
                discover=False,
            )
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_missing_aws_region_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(
                provider_type="aws",
                aws_profile="default",
                aws_region=None,
                name=None,
                discover=False,
            )
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_duplicate_provider_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with (
            patch(
                "orb.interface.provider_config_handler.get_config_location",
                return_value=tmp_path,
            ),
            patch(
                "orb.interface.provider_config_handler._test_provider_credentials",
                return_value=(True, ""),
            ),
        ):
            args = _ns(
                provider_type="aws",
                aws_profile="default",
                aws_region="us-east-1",
                name="aws-default",
                discover=False,
            )
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_success_returns_0_and_writes_config(self, tmp_path):
        # TODO: CQRS violation — handler writes config.json directly
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with (
            patch(
                "orb.interface.provider_config_handler.get_config_location",
                return_value=tmp_path,
            ),
            patch(
                "orb.interface.provider_config_handler._test_provider_credentials",
                return_value=(True, ""),
            ),
        ):
            args = _ns(
                provider_type="aws",
                aws_profile="prod",
                aws_region="eu-west-1",
                name="aws-prod",
                discover=False,
            )
            from unittest.mock import MagicMock as _MC_add

            args._container = _MC_add()
            result = await handle_provider_add(args)

        assert result.get("error") is not True
        saved = json.loads((tmp_path / "config.json").read_text())
        names = [p["name"] for p in saved["provider"]["providers"]]
        assert "aws-prod" in names

    @pytest.mark.asyncio
    async def test_credential_failure_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler calls AWSSessionFactory directly
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with (
            patch(
                "orb.interface.provider_config_handler.get_config_location",
                return_value=tmp_path,
            ),
            patch(
                "orb.interface.provider_config_handler._test_provider_credentials",
                return_value=(False, "No credentials"),
            ),
        ):
            args = _ns(
                provider_type="aws",
                aws_profile="bad",
                aws_region="us-east-1",
                name="aws-bad",
                discover=False,
            )
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_missing_provider_type_returns_1(self, tmp_path):
        """No provider_type attribute on args must return an error, not default to 'aws'."""
        from orb.interface.provider_config_handler import handle_provider_add

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            # args has no provider_type attribute at all
            args = _ns(aws_profile="default", aws_region="us-east-1", name=None, discover=False)
            from unittest.mock import MagicMock as _MC

            args._container = _MC()
            result = await handle_provider_add(args)

        assert result.get("error") is True and result.get("exit_code") == 1


# ---------------------------------------------------------------------------
# handle_provider_remove
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderRemove:
    @pytest.mark.asyncio
    async def test_missing_config_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_remove

        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_remove(_ns(provider_name="aws-default"))

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_provider_not_found_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_remove

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_remove(_ns(provider_name="nonexistent"))

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_last_provider_guard_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_remove

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_remove(_ns(provider_name="aws-default"))

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_success_returns_0_and_removes_provider(self, tmp_path):
        # TODO: CQRS violation — handler writes config.json directly
        from orb.interface.provider_config_handler import handle_provider_remove

        config = _base_config(
            providers=[
                {
                    "name": "aws-a",
                    "type": "aws",
                    "enabled": True,
                    "config": {"profile": "a", "region": "us-east-1"},
                },
                {
                    "name": "aws-b",
                    "type": "aws",
                    "enabled": True,
                    "config": {"profile": "b", "region": "eu-west-1"},
                },
            ]
        )
        _write_config(tmp_path, config)
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_remove(_ns(provider_name="aws-a"))

        assert result.get("error") is not True
        saved = json.loads((tmp_path / "config.json").read_text())
        names = [p["name"] for p in saved["provider"]["providers"]]
        assert "aws-a" not in names
        assert "aws-b" in names


# ---------------------------------------------------------------------------
# handle_provider_update
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderUpdate:
    @pytest.mark.asyncio
    async def test_no_updates_specified_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_update

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider_name="aws-default", aws_region=None, aws_profile=None)
            from unittest.mock import MagicMock as _MC

            args._container = _MC()
            result = await handle_provider_update(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_provider_not_found_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_update

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            args = _ns(provider_name="nonexistent", aws_region="eu-west-1", aws_profile=None)
            from unittest.mock import MagicMock as _MC

            args._container = _MC()
            result = await handle_provider_update(args)

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_success_returns_0_and_writes_config(self, tmp_path):
        # TODO: CQRS violation — handler writes config.json directly
        from orb.interface.provider_config_handler import handle_provider_update

        _write_config(tmp_path, _base_config())
        with (
            patch(
                "orb.interface.provider_config_handler.get_config_location",
                return_value=tmp_path,
            ),
            patch(
                "orb.interface.provider_config_handler._test_provider_credentials",
                return_value=(True, ""),
            ),
        ):
            args = _ns(provider_name="aws-default", aws_region="ap-southeast-1", aws_profile=None)
            from unittest.mock import MagicMock as _MC

            args._container = _MC()
            result = await handle_provider_update(args)

        assert result.get("error") is not True
        saved = json.loads((tmp_path / "config.json").read_text())
        provider = next(p for p in saved["provider"]["providers"] if p["name"] == "aws-default")
        assert provider["config"]["region"] == "ap-southeast-1"


# ---------------------------------------------------------------------------
# handle_provider_set_default
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderSetDefault:
    @pytest.mark.asyncio
    async def test_provider_not_found_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_set_default

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_set_default(_ns(provider_name="nonexistent"))

        assert result.get("error") is True and result.get("exit_code") == 1

    @pytest.mark.asyncio
    async def test_success_returns_0_and_writes_default(self, tmp_path):
        # TODO: CQRS violation — handler writes config.json directly
        from orb.interface.provider_config_handler import handle_provider_set_default

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_set_default(_ns(provider_name="aws-default"))

        assert result.get("error") is not True
        saved = json.loads((tmp_path / "config.json").read_text())
        assert saved["provider"]["default_provider"] == "aws-default"


# ---------------------------------------------------------------------------
# handle_provider_get_default
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHandleProviderGetDefault:
    @pytest.mark.asyncio
    async def test_explicit_default_returns_0(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_get_default

        config = _base_config()
        config["provider"]["default_provider"] = "aws-default"
        _write_config(tmp_path, config)
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_get_default(_ns())

        assert result.get("error") is not True

    @pytest.mark.asyncio
    async def test_fallback_to_first_provider_returns_0(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_get_default

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_get_default(_ns())

        assert result.get("error") is not True

    @pytest.mark.asyncio
    async def test_no_providers_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_get_default

        _write_config(tmp_path, _base_config(providers=[]))
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            result = await handle_provider_get_default(_ns())

        assert result.get("error") is True and result.get("exit_code") == 1


# ---------------------------------------------------------------------------
# handle_provider_show
# ---------------------------------------------------------------------------


def _make_show_container(exit_code_ok: int = 0):
    """Create a mock container for handle_provider_show tests."""
    from unittest.mock import MagicMock as _MG

    from orb.application.dto.interface_response import InterfaceResponse
    from orb.interface.response_formatting_service import ResponseFormattingService

    mock_formatter = _MG(spec=ResponseFormattingService)
    mock_formatter.format_provider_detail.return_value = InterfaceResponse(data={}, exit_code=0)
    mock_formatter.format_error.return_value = InterfaceResponse(data={}, exit_code=1)
    mock_container = _MG()
    mock_container.get.side_effect = lambda t: (
        mock_formatter if t is ResponseFormattingService else _MG()
    )
    return mock_container


@pytest.mark.unit
class TestHandleProviderShow:
    @pytest.mark.asyncio
    async def test_specific_provider_returns_0(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_show

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            _show_args = _ns(provider_name="aws-default")
            _show_args._container = _make_show_container()
            result = await handle_provider_show(_show_args)

        assert exit_code(result) == 0

    @pytest.mark.asyncio
    async def test_specific_provider_not_found_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_show

        _write_config(tmp_path, _base_config())
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            _show_args2 = _ns(provider_name="nonexistent")
            _show_args2._container = _make_show_container()
            result = await handle_provider_show(_show_args2)

        assert exit_code(result) == 1

    @pytest.mark.asyncio
    async def test_default_provider_returns_0(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_show

        config = _base_config()
        config["provider"]["default_provider"] = "aws-default"
        _write_config(tmp_path, config)
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            _show_args = _ns(provider_name=None)
            _show_args._container = _make_show_container()
            result = await handle_provider_show(_show_args)

        assert exit_code(result) == 0

    @pytest.mark.asyncio
    async def test_no_providers_returns_1(self, tmp_path):
        # TODO: CQRS violation — handler reads config.json directly
        from orb.interface.provider_config_handler import handle_provider_show

        _write_config(tmp_path, _base_config(providers=[]))
        with patch(
            "orb.interface.provider_config_handler.get_config_location",
            return_value=tmp_path,
        ):
            _show_args3 = _ns(provider_name=None)
            _show_args3._container = _make_show_container()
            result = await handle_provider_show(_show_args3)

        assert exit_code(result) == 1
