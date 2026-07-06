"""Unit tests for init_command_handler — flow order and cache bypass."""

from unittest.mock import MagicMock, patch

import orb.interface.init_command_handler as _mod


def _make_strategy(regions=None, default_region="us-east-1"):
    strategy = MagicMock()
    strategy.get_available_regions.return_value = regions or []
    strategy.get_default_region.return_value = default_region
    strategy.get_available_credential_sources.return_value = [
        {"name": "default", "description": "Default profile"}
    ]
    strategy.test_credentials.return_value = {"success": True}
    strategy.get_credential_requirements.return_value = {}
    strategy.get_operational_requirements.return_value = {
        "region": {"required": True, "description": "AWS region"}
    }
    return strategy


def _mock_container():
    console = MagicMock()
    container = MagicMock()
    container.get.return_value = console
    return container


def test_discover_infrastructure_uses_create_strategy_by_type():
    """_discover_infrastructure must use create_strategy_by_type with the operator's config."""
    mock_registry = MagicMock()
    mock_strategy = MagicMock()
    mock_strategy.discover_infrastructure_interactive.return_value = {"vpc_id": "vpc-123"}
    mock_registry.create_strategy_by_type.return_value = mock_strategy

    with (
        patch(
            "orb.providers.registry.provider_registry.ProviderRegistry",
        ),
        patch(
            "orb.providers.registry.get_provider_registry",
            return_value=mock_registry,
        ),
        patch(
            "orb.interface.init_command_handler.get_container",
            return_value=_mock_container(),
        ),
    ):
        result = _mod._discover_infrastructure("aws", "us-east-1", "my-profile", mock_registry)

    mock_registry.create_strategy_by_type.assert_called_once_with(
        "aws", {"region": "us-east-1", "profile": "my-profile"}
    )
    mock_registry.get_or_create_strategy.assert_not_called()
    assert result == {"vpc_id": "vpc-123"}


def test_get_operational_requirements_returns_region_for_aws():
    """_get_operational_requirements returns region dict when strategy provides it."""
    strategy = MagicMock()
    strategy.get_operational_requirements.return_value = {
        "region": {"required": True, "description": "AWS region"}
    }

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        result = _mod._get_operational_requirements("aws")

    assert "region" in result
    assert result["region"]["required"] is True


def test_get_credential_requirements_returns_empty_for_aws():
    """_get_credential_requirements returns {} for AWS (no pre-auth params needed)."""
    strategy = MagicMock()
    strategy.get_credential_requirements.return_value = {}

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        result = _mod._get_credential_requirements("aws")

    assert result == {}


def test_operational_requirements_separate_from_credential_requirements():
    """get_operational_requirements and get_credential_requirements return different things."""
    strategy = _make_strategy()

    with patch.object(_mod, "_get_provider_strategy", return_value=strategy):
        cred_reqs = _mod._get_credential_requirements("aws")
        op_reqs = _mod._get_operational_requirements("aws")

    assert cred_reqs == {}
    assert "region" in op_reqs
    assert cred_reqs != op_reqs


def test_discover_infrastructure_forwards_region_and_profile_to_strategy():
    """_discover_infrastructure forwards region + profile via the strategy's discover call."""
    mock_registry = MagicMock()
    mock_strategy = MagicMock()
    mock_strategy.discover_infrastructure_interactive.return_value = {}
    mock_registry.create_strategy_by_type.return_value = mock_strategy

    with (
        patch("orb.providers.registry.get_provider_registry", return_value=mock_registry),
        patch("orb.interface.init_command_handler.get_container", return_value=_mock_container()),
    ):
        _mod._discover_infrastructure("aws", "eu-west-2", "my-prod-profile", mock_registry)

    mock_registry.create_strategy_by_type.assert_called_once_with(
        "aws", {"region": "eu-west-2", "profile": "my-prod-profile"}
    )
    full_config = mock_strategy.discover_infrastructure_interactive.call_args[0][0]
    assert full_config["type"] == "aws"
    assert full_config["config"]["region"] == "eu-west-2"
    assert full_config["config"]["profile"] == "my-prod-profile"
    mock_registry.get_or_create_strategy.assert_not_called()


def test_interactive_setup_tests_credentials_before_asking_for_region():
    """Credentials must be tested before region is selected."""
    call_order = []

    def mock_test_creds(provider_type, source, **kwargs):
        call_order.append("test_credentials")
        return (True, "")

    def mock_pick_region(regions, default):
        call_order.append("pick_region")
        return "us-east-1"

    # inputs: scheduler choice, provider choice, credential choice, discover infra, add another
    inputs = iter(["1", "1", "1", "N", "N"])
    strategy = _make_strategy()

    with (
        patch("builtins.input", side_effect=inputs),
        patch.object(_mod, "_test_provider_credentials", side_effect=mock_test_creds),
        patch.object(_mod, "_pick_region", side_effect=mock_pick_region),
        patch.object(
            _mod,
            "_get_available_schedulers",
            return_value=[{"type": "default", "display_name": "Default", "description": ""}],
        ),
        patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ),
        patch.object(_mod, "_get_provider_strategy", return_value=strategy),
        patch.object(_mod, "_get_credential_requirements", return_value={}),
        patch.object(
            _mod,
            "_get_available_credential_sources",
            return_value=[{"name": "default", "description": "Default"}],
        ),
        patch.object(
            _mod,
            "_get_operational_requirements",
            return_value={"region": {"required": True, "description": "AWS region"}},
        ),
        patch("orb.interface.init_command_handler.get_container", return_value=_mock_container()),
    ):
        _mod._interactive_setup()

    assert "test_credentials" in call_order
    assert "pick_region" in call_order
    assert call_order.index("test_credentials") < call_order.index("pick_region")


def test_interactive_setup_returns_empty_on_credential_failure():
    """When credentials fail, _interactive_setup returns {}."""
    inputs = iter(["1", "1", "1", "N"])
    strategy = _make_strategy()

    with (
        patch("builtins.input", side_effect=inputs),
        patch.object(
            _mod, "_test_provider_credentials", return_value=(False, "Invalid credentials")
        ),
        patch.object(
            _mod,
            "_get_available_schedulers",
            return_value=[{"type": "default", "display_name": "Default", "description": ""}],
        ),
        patch.object(
            _mod,
            "_get_available_providers",
            return_value=[{"type": "aws", "display_name": "AWS", "description": ""}],
        ),
        patch.object(_mod, "_get_provider_strategy", return_value=strategy),
        patch.object(_mod, "_get_credential_requirements", return_value={}),
        patch.object(
            _mod,
            "_get_available_credential_sources",
            return_value=[{"name": "default", "description": "Default"}],
        ),
        patch("orb.interface.init_command_handler.get_container", return_value=_mock_container()),
    ):
        result = _mod._interactive_setup()

    assert result == {}


def test_interactive_setup_raises_on_no_providers():
    """When no providers are registered, _interactive_setup raises ValueError."""
    import pytest

    with (
        patch.object(
            _mod,
            "_get_available_schedulers",
            return_value=[{"type": "default", "display_name": "Default", "description": ""}],
        ),
        patch.object(_mod, "_get_available_providers", return_value=[]),
        patch("builtins.input", return_value="1"),
        patch("orb.interface.init_command_handler.get_container", return_value=_mock_container()),
    ):
        with pytest.raises(ValueError, match="No providers registered"):
            _mod._interactive_setup()


def test_write_config_file_fleet_role_in_config_subnet_ids_in_template_defaults(tmp_path):
    """fleet_role goes to provider config; subnet_ids goes to template_defaults."""
    import json

    config_file = tmp_path / "config.json"

    user_config = {
        "scheduler_type": "default",
        "providers": [
            {
                "type": "aws",
                "profile": None,
                "region": "us-east-1",
                "is_default": True,
                "infrastructure_defaults": {
                    "subnet_ids": ["subnet-aaa", "subnet-bbb"],
                    "security_group_ids": ["sg-111"],
                    "fleet_role": "arn:aws:iam::123456789012:role/AWSServiceRoleForEC2SpotFleet",
                },
            }
        ],
    }

    mock_strategy_class = MagicMock()
    mock_strategy_class.get_cli_extra_config_keys.return_value = {"fleet_role"}
    mock_strategy_class.generate_provider_name.return_value = "aws_instance-profile_us-east-1"

    mock_reg = MagicMock()
    mock_reg.strategy_class = mock_strategy_class

    mock_factory = MagicMock()
    mock_factory.ensure_provider_type_registered.return_value = True
    mock_factory._get_type_registration.return_value = mock_reg

    mock_container = MagicMock()
    mock_container.get.return_value = mock_factory

    mock_scheduler_registry = MagicMock()
    mock_scheduler_registry.get_extra_config_for_type.return_value = {}

    fake_default = json.dumps({"scheduler": {}, "provider": {}})

    class _FakeResource:
        def read_text(self):
            return fake_default

    class _FakeFiles:
        def joinpath(self, name):
            return _FakeResource()

    with (
        patch("orb.interface.init_command_handler.get_container", return_value=mock_container),
        patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry",
            return_value=mock_scheduler_registry,
        ),
        patch("importlib.resources.files", return_value=_FakeFiles()),
    ):
        _mod._write_config_file(config_file, user_config)

    with open(config_file) as f:
        written = json.load(f)

    providers = written["provider"]["providers"]
    assert len(providers) == 1
    p = providers[0]

    assert "fleet_role" in p["config"], "fleet_role should be in provider config"
    assert (
        p["config"]["fleet_role"] == "arn:aws:iam::123456789012:role/AWSServiceRoleForEC2SpotFleet"
    )

    assert "template_defaults" in p, "template_defaults key should exist"
    assert "subnet_ids" in p["template_defaults"], "subnet_ids should be in template_defaults"
    assert p["template_defaults"]["subnet_ids"] == ["subnet-aaa", "subnet-bbb"]

    assert "fleet_role" not in p.get("template_defaults", {}), (
        "fleet_role must not appear in template_defaults"
    )


# ---------------------------------------------------------------------------
# _get_provider_strategy — init-path tests
# ---------------------------------------------------------------------------


def test_get_provider_strategy_returns_strategy_class() -> None:
    """_get_provider_strategy must return the strategy CLASS (not an instance).

    The init flow has no provider config yet; classmethods can be called
    directly on the class without constructing an instance.
    """
    mock_cls = MagicMock()
    mock_reg = MagicMock()
    mock_reg.strategy_class = mock_cls
    mock_registry = MagicMock()
    mock_registry.ensure_provider_type_registered.return_value = True
    mock_registry._get_type_registration.return_value = mock_reg

    result = _mod._get_provider_strategy("k8s", registry=mock_registry)

    mock_registry.ensure_provider_type_registered.assert_called_once_with("k8s")
    mock_registry.get_or_create_strategy.assert_not_called()
    assert result is mock_cls


def test_get_provider_strategy_k8s_does_not_touch_get_or_create_strategy() -> None:
    """_get_provider_strategy must not call registry.get_or_create_strategy."""
    mock_cls = MagicMock()
    mock_reg = MagicMock()
    mock_reg.strategy_class = mock_cls
    mock_registry = MagicMock()
    mock_registry.ensure_provider_type_registered.return_value = True
    mock_registry._get_type_registration.return_value = mock_reg

    _mod._get_provider_strategy("k8s", registry=mock_registry)

    mock_registry.get_or_create_strategy.assert_not_called()


def test_get_provider_strategy_aws_returns_strategy_class() -> None:
    """All provider types go through the classmethod path (no special-casing)."""
    mock_cls = MagicMock()
    mock_reg = MagicMock()
    mock_reg.strategy_class = mock_cls
    mock_registry = MagicMock()
    mock_registry.ensure_provider_type_registered.return_value = True
    mock_registry._get_type_registration.return_value = mock_reg

    result = _mod._get_provider_strategy("aws", registry=mock_registry)

    mock_registry.ensure_provider_type_registered.assert_called_once_with("aws")
    mock_registry.get_or_create_strategy.assert_not_called()
    assert result is mock_cls


def test_get_provider_strategy_returns_none_on_error() -> None:
    """_get_provider_strategy returns None when the type lookup raises."""
    mock_registry = MagicMock()
    mock_registry.ensure_provider_type_registered.return_value = True
    mock_registry._get_type_registration.side_effect = ValueError("not registered")

    result = _mod._get_provider_strategy("k8s", registry=mock_registry)

    assert result is None


# ---------------------------------------------------------------------------
# test_init_handler_uses_strategy_generate_provider_name
# ---------------------------------------------------------------------------


def _write_config_file_with_strategy(tmp_path, provider_data, strategy_name_return):
    """Helper: run _write_config_file with a mocked strategy class."""
    import json

    config_file = tmp_path / "config.json"
    user_config = {"scheduler_type": "default", "providers": [provider_data]}

    mock_strategy_class = MagicMock()
    mock_strategy_class.get_cli_extra_config_keys.return_value = set()
    mock_strategy_class.generate_provider_name.return_value = strategy_name_return

    mock_reg = MagicMock()
    mock_reg.strategy_class = mock_strategy_class

    mock_factory = MagicMock()
    mock_factory.ensure_provider_type_registered.return_value = True
    mock_factory._get_type_registration.return_value = mock_reg

    mock_container = MagicMock()
    mock_container.get.return_value = mock_factory

    mock_scheduler_registry = MagicMock()
    mock_scheduler_registry.get_extra_config_for_type.return_value = {}

    fake_default = json.dumps({"scheduler": {}, "provider": {}})

    class _FakeResource:
        def read_text(self):
            return fake_default

    class _FakeFiles:
        def joinpath(self, name):
            return _FakeResource()

    with (
        patch("orb.interface.init_command_handler.get_container", return_value=mock_container),
        patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry",
            return_value=mock_scheduler_registry,
        ),
        patch("importlib.resources.files", return_value=_FakeFiles()),
    ):
        _mod._write_config_file(config_file, user_config)

    with open(config_file) as f:
        written = json.load(f)

    return written["provider"]["providers"][0], mock_strategy_class


def test_init_handler_uses_strategy_generate_provider_name(tmp_path) -> None:
    """_write_config_file routes provider name generation through the strategy."""
    provider_data = {
        "type": "k8s",
        "profile": "ms-karpenter",
        "region": "",
        "is_default": True,
        "infrastructure_defaults": {},
    }
    p, mock_strategy = _write_config_file_with_strategy(tmp_path, provider_data, "k8s_ms-karpenter")
    mock_strategy.generate_provider_name.assert_called_once()
    assert p["name"] == "k8s_ms-karpenter"


def test_init_handler_uses_strategy_name_for_aws(tmp_path) -> None:
    """generate_provider_name is called for AWS providers too."""
    provider_data = {
        "type": "aws",
        "profile": "my-profile",
        "region": "eu-west-1",
        "is_default": False,
        "infrastructure_defaults": {},
    }
    p, mock_strategy = _write_config_file_with_strategy(
        tmp_path, provider_data, "aws_my-profile_eu-west-1"
    )
    mock_strategy.generate_provider_name.assert_called_once()
    assert p["name"] == "aws_my-profile_eu-west-1"


def test_init_handler_fallback_name_when_strategy_unavailable(tmp_path) -> None:
    """When the strategy cannot be created, _fallback_provider_name is used."""
    import json

    config_file = tmp_path / "config.json"
    user_config = {
        "scheduler_type": "default",
        "providers": [
            {
                "type": "aws",
                "profile": "default",
                "region": "us-west-2",
                "is_default": False,
                "infrastructure_defaults": {},
            }
        ],
    }

    mock_factory = MagicMock()
    mock_factory.ensure_provider_type_registered.return_value = True
    mock_factory._get_type_registration.side_effect = RuntimeError("no strategy")

    mock_container = MagicMock()
    mock_container.get.return_value = mock_factory

    mock_scheduler_registry = MagicMock()
    mock_scheduler_registry.get_extra_config_for_type.return_value = {}

    fake_default = json.dumps({"scheduler": {}, "provider": {}})

    class _FakeResource:
        def read_text(self):
            return fake_default

    class _FakeFiles:
        def joinpath(self, name):
            return _FakeResource()

    with (
        patch("orb.interface.init_command_handler.get_container", return_value=mock_container),
        patch(
            "orb.infrastructure.scheduler.registry.get_scheduler_registry",
            return_value=mock_scheduler_registry,
        ),
        patch("importlib.resources.files", return_value=_FakeFiles()),
    ):
        _mod._write_config_file(config_file, user_config)

    with open(config_file) as f:
        written = json.load(f)

    p = written["provider"]["providers"][0]
    # Fallback: aws_{sanitized_profile}_{region}
    assert p["name"] == "aws_default_us-west-2"
