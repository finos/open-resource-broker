"""TemplateSerializer.from_dict applies provider and global template defaults."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.domain.template.ports.template_defaults_port import TemplateDefaultsPort
from orb.infrastructure.storage.repositories.template_repository import TemplateSerializer

_REPO_ROOT = Path(__file__).resolve().parents[5]

_AWS_DEFAULTS = {
    "machine_types": {"m5.large": 1, "m5.xlarge": 2},
    "machine_disk_size_gb": 77,
    "machine_disk_type": "gp3",
    "iops": 4000,
    "machine_ssh_key": "default-key",
    "machine_bootstrap": "#!/bin/sh\necho default",
    "machine_role": "default-profile",
    "monitoring_enabled": True,
}


def _defaults_service(template_defaults: dict) -> TemplateDefaultsService:
    config_manager = MagicMock()
    config_manager.get_template_config.return_value = {}
    config_manager.get_provider_config.return_value = SimpleNamespace(
        provider_defaults={"aws": SimpleNamespace(template_defaults=template_defaults)},
        providers=[],
    )
    return TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())


def _row(**extra) -> dict:
    return {
        "template_id": "tpl-1",
        "name": "tpl",
        "image_id": "ami-1",
        "provider_type": "aws",
        "provider_api": "RunInstances",
        **extra,
    }


def test_serializer_call_matches_defaults_port_signature() -> None:
    params = inspect.signature(TemplateDefaultsPort.resolve_template_defaults).parameters
    assert "provider_name" not in params
    assert "provider_instance_name" in params


def test_omitted_fields_receive_provider_defaults() -> None:
    serializer = TemplateSerializer(defaults_service=_defaults_service(_AWS_DEFAULTS))

    template = serializer.from_dict(_row())

    assert template.machine_types == {"m5.large": 1, "m5.xlarge": 2}
    assert template.machine_disk_size_gb == 77
    assert template.machine_disk_type == "gp3"
    assert template.iops == 4000
    assert template.machine_ssh_key == "default-key"
    assert template.machine_bootstrap == "#!/bin/sh\necho default"
    assert template.machine_role == "default-profile"
    assert template.monitoring_enabled is True


def test_row_values_win_over_defaults() -> None:
    serializer = TemplateSerializer(defaults_service=_defaults_service(_AWS_DEFAULTS))

    template = serializer.from_dict(
        _row(
            machine_types={"c5.large": 1},
            machine_disk_size_gb=10,
            machine_ssh_key="own-key",
            monitoring_enabled=False,
        )
    )

    assert template.machine_types == {"c5.large": 1}
    assert template.machine_disk_size_gb == 10
    assert template.machine_ssh_key == "own-key"
    assert template.monitoring_enabled is False


def test_no_failure_warning_for_a_normal_row() -> None:
    serializer = TemplateSerializer(defaults_service=_defaults_service(_AWS_DEFAULTS))
    serializer.logger = MagicMock()

    serializer.from_dict(_row())

    messages = [str(c.args[0]) for c in serializer.logger.warning.call_args_list]
    assert not [m for m in messages if "Failed to apply defaults" in m]


def _fixture_templates() -> list[dict]:
    rows: list[dict] = []
    for rel in ("config/aws_templates.json", "tests/fixtures/config/templates.json"):
        rows.extend(json.loads((_REPO_ROOT / rel).read_text())["templates"])
    return [r for r in rows if r.get("templateId") or r.get("template_id")]


_OWN_VALUE_FIELDS = (
    "machine_types",
    "machine_disk_size_gb",
    "machine_disk_type",
    "iops",
    "machine_ssh_key",
    "machine_bootstrap",
    "machine_role",
    "monitoring_enabled",
)


@pytest.mark.parametrize("row", _fixture_templates(), ids=lambda r: r.get("templateId", "row"))
def test_existing_templates_keep_the_values_they_set(row: dict) -> None:
    plain = TemplateSerializer().from_dict(dict(row))
    with_defaults = TemplateSerializer(defaults_service=_defaults_service(_AWS_DEFAULTS)).from_dict(
        dict(row)
    )

    for field in _OWN_VALUE_FIELDS:
        own = getattr(plain, field)
        if own not in (None, {}, [], ""):
            assert getattr(with_defaults, field) == own, field


def test_native_machine_types_survive_a_store_round_trip() -> None:
    serializer = TemplateSerializer()
    stored = serializer.to_dict(serializer.from_dict(_row(machine_types={"c5.large": 2})))

    assert stored["machine_types"] == {"c5.large": 2}
    assert serializer.from_dict(stored).machine_types == {"c5.large": 2}


@pytest.mark.parametrize("row", _fixture_templates(), ids=lambda r: r.get("templateId", "row"))
def test_fixture_templates_load_their_declared_machine_types(row: dict) -> None:
    declared = row.get("vmTypes") or row.get("instance_types") or row.get("machine_types")
    template = TemplateSerializer(defaults_service=_defaults_service(_AWS_DEFAULTS)).from_dict(
        dict(row)
    )

    if declared:
        assert template.machine_types == declared
    else:
        assert template.machine_types == _AWS_DEFAULTS["machine_types"]


def test_unexpected_defaults_failure_does_not_abort_the_load() -> None:
    defaults = MagicMock()
    defaults.resolve_template_defaults.side_effect = RuntimeError("malformed defaults")
    serializer = TemplateSerializer(defaults_service=defaults)
    serializer.logger = MagicMock()

    template = serializer.from_dict(_row())

    assert template.template_id == "tpl-1"
    assert serializer.logger.warning.call_args.kwargs.get("exc_info") is True
