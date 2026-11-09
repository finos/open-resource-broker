"""Unknown provider config keys are reported once, on the user-input path only."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import orb
from orb.config.schemas.provider_strategy_schema import ProviderInstanceConfig
from orb.providers.aws.configuration.config import AWSProviderConfig
from orb.providers.aws.registration import create_aws_config
from orb.providers.base import config_input
from orb.providers.oci.registration import create_oci_config

_SRC = Path(orb.__file__).parent
_ROOT = _SRC.parents[1]


@pytest.fixture(autouse=True)
def _fresh_dedupe_state():
    config_input._warned.clear()
    yield
    config_input._warned.clear()


def _unknown_key_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Unknown key" in r.getMessage()]


def _instance(name: str, config: dict, type_: str = "aws") -> ProviderInstanceConfig:
    return ProviderInstanceConfig.model_validate({"name": name, "type": type_, "config": config})


def test_same_bad_key_built_repeatedly_warns_once(caplog) -> None:
    caplog.set_level(logging.WARNING)
    for _ in range(5):
        create_aws_config(_instance("aws-1", {"region": "us-east-1", "regoin": "x"}))

    assert len(_unknown_key_warnings(caplog)) == 1


def test_warning_names_key_and_suggests_close_match(caplog) -> None:
    caplog.set_level(logging.WARNING)

    create_aws_config(_instance("aws-1", {"regoin": "us-east-1"}))

    (message,) = _unknown_key_warnings(caplog)
    assert "regoin" in message
    assert "'region'" in message
    assert "aws-1" in message


def test_values_are_never_logged(caplog) -> None:
    caplog.set_level(logging.WARNING)

    create_aws_config(_instance("aws-1", {"secrett": "super-secret-value"}))

    assert "super-secret-value" not in caplog.text


def test_distinct_instances_warn_separately(caplog) -> None:
    caplog.set_level(logging.WARNING)

    create_aws_config(_instance("aws-1", {"regoin": "x"}))
    create_aws_config(_instance("aws-2", {"regoin": "x"}))

    assert len(_unknown_key_warnings(caplog)) == 2


def test_unnamed_raw_config_is_deduplicated_by_content(caplog) -> None:
    caplog.set_level(logging.WARNING)

    for _ in range(3):
        create_aws_config({"regoin": "x"})

    assert len(_unknown_key_warnings(caplog)) == 1


def test_model_dump_round_trip_is_silent(caplog) -> None:
    config = AWSProviderConfig(region="us-east-1", regoin="x")
    caplog.set_level(logging.WARNING)

    AWSProviderConfig(**config.model_dump())
    AWSProviderConfig(**config.model_dump())

    assert _unknown_key_warnings(caplog) == []


def test_type_discriminator_and_legacy_keys_are_known(caplog) -> None:
    caplog.set_level(logging.WARNING)

    create_aws_config({"type": "aws", "provider_type": "aws", "region": "us-east-1"})
    create_aws_config({"aws_connection_timeout": 5})
    create_aws_config({"connection_timeout_ms": 5000})

    assert _unknown_key_warnings(caplog) == []


def test_environment_sourced_names_do_not_warn(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ORB_AWS_REGION", "eu-west-1")
    monkeypatch.setenv("ORB_AWS_SOMETHING_ELSE", "x")
    caplog.set_level(logging.WARNING)

    create_aws_config({"profile": "default"})

    assert _unknown_key_warnings(caplog) == []


def _shipped_provider_configs() -> list[tuple[str, str, dict]]:
    files = [
        _ROOT / "config" / "default_config.json",
        _ROOT / "config" / "config.example.json",
        _ROOT / "config" / "oci_config.local.example.json",
        _ROOT / "config" / "oci_config.remote.example.json",
        *(_SRC / "providers" / "aws" / "config").glob("*.json"),
        *(_SRC / "providers" / "oci" / "config").glob("*.json"),
    ]
    found = []
    for path in files:
        provider = json.loads(path.read_text()).get("provider") or {}
        for entry in provider.get("providers", []):
            found.append((path.name, entry["type"], entry))
    return found


@pytest.mark.parametrize(
    "source,type_,entry",
    _shipped_provider_configs(),
    ids=lambda v: v if isinstance(v, str) else v.get("name", "entry"),
)
def test_shipped_provider_configs_are_silent(source, type_, entry, caplog) -> None:
    caplog.set_level(logging.WARNING)
    factory = {"aws": create_aws_config, "oci": create_oci_config}.get(type_)
    if factory is None:
        pytest.skip(f"no config factory under test for {type_}")

    factory(_instance(entry["name"], entry.get("config", {}), type_))

    assert _unknown_key_warnings(caplog) == []


def test_oci_unknown_key_warns_once(caplog) -> None:
    caplog.set_level(logging.WARNING)

    for _ in range(3):
        create_oci_config(_instance("oci-1", {"regoin": "us-ashburn-1"}, "oci"))

    (message,) = _unknown_key_warnings(caplog)
    assert "'region'" in message


def test_non_mapping_settings_are_not_scanned_for_unknown_keys(monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from orb.providers.aws import registration

    scan = MagicMock()
    monkeypatch.setattr(registration, "warn_unknown_config_keys", scan)

    with pytest.raises(RuntimeError, match="Failed to create AWS config"):
        create_aws_config(SimpleNamespace(config=object()))

    scan.assert_not_called()
