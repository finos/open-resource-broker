"""Scheduler loads: untyped rows resolved against an instance gain its provider_type.

Each expected dict is the resolved row as it was before the change. A typed row
is unchanged; an untyped row is that dict plus ``provider_type``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy


def _defaults_service(registry: Any = None) -> TemplateDefaultsService:
    config = MagicMock()
    config.get_template_config.return_value = {}
    config.get_provider_config.return_value = SimpleNamespace(
        provider_defaults={},
        providers=[SimpleNamespace(name="aws-main", type="aws", template_defaults={})],
    )
    return TemplateDefaultsService(
        config_manager=config, logger=MagicMock(), provider_registry=registry
    )


def _load(strategy: Any, rows: list[dict[str, Any]], tmp_path, instance: str = "aws-main"):
    strategy._template_defaults_service = _defaults_service()
    strategy._logger = MagicMock()
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"templates": rows}))
    with (
        patch.object(FieldMappingRegistry, "get_or_none", return_value=None),
        patch.object(strategy, "_get_provider_name", return_value=instance),
        patch.object(strategy, "_get_active_provider_type", return_value="aws"),
    ):
        return strategy.load_templates_from_path(str(path))


_DEFAULT_BASE = {"machine_image": "ami-12345678", "provider_api": "EC2Fleet"}


def test_default_scheduler(tmp_path):
    rows = [
        {**_DEFAULT_BASE, "template_id": "u", "max_machines": 2},
        {**_DEFAULT_BASE, "template_id": "t", "max_machines": 2, "provider_type": "aws"},
    ]
    out = _load(DefaultSchedulerStrategy(), rows, tmp_path)
    assert out == [
        {**_DEFAULT_BASE, "template_id": "u", "max_machines": 2, "provider_type": "aws"},
        {**_DEFAULT_BASE, "template_id": "t", "max_machines": 2, "provider_type": "aws"},
    ]


def test_default_scheduler_camel_case_rows(tmp_path):
    row = {"templateId": "c", "providerApi": "EC2Fleet", "machine_image": "ami-12345678"}
    out = _load(DefaultSchedulerStrategy(), [row, {**row, "providerType": "aws"}], tmp_path)
    assert out == [
        {**row, "provider_type": "aws"},
        {**row, "providerType": "aws", "provider_type": "aws"},
    ]


def test_hostfactory_scheduler(tmp_path):
    typed = {
        "templateId": "t",
        "providerApi": "EC2Fleet",
        "imageId": "ami-12345678",
        "maxNumber": 2,
        "vmType": "t3.micro",
        "providerType": "aws",
    }
    untyped = {k: v for k, v in typed.items() if k != "providerType"} | {"templateId": "u"}
    out = _load(HostFactorySchedulerStrategy(), [untyped, typed], tmp_path)

    def golden(template_id: str) -> dict[str, Any]:
        return {
            "created_at": None,
            "image_id": "ami-12345678",
            "machine_image": "ami-12345678",
            "machine_types": {"t3.micro": 1},
            "max_instances": 2,
            "max_machines": 2,
            "name": template_id,
            "provider_api": "EC2Fleet",
            "template_id": template_id,
            "updated_at": None,
            "version": None,
        }

    assert out == [
        {**golden("u"), "provider_type": "aws"},
        {**golden("t"), "provider_type": "aws"},
    ]


def test_slurm_scheduler(tmp_path):
    base = {"provider_api": "EC2Fleet", "machine_image": "ami-12345678"}
    rows = [{**base, "template_id": "u"}, {**base, "template_id": "t", "provider_type": "aws"}]
    out = _load(SlurmSchedulerStrategy(), rows, tmp_path)
    assert out == [
        {**base, "template_id": tid, "templateId": tid, "provider_type": "aws"}
        for tid in ("u", "t")
    ]


@pytest.mark.parametrize("name", ["default", "My_Cluster", "Prod-East"])
def test_fallback_instance_names_leave_rows_untyped(tmp_path, name):
    row = {**_DEFAULT_BASE, "template_id": "u", "max_machines": 2}
    registry = MagicMock()
    registry.is_provider_registered.return_value = False
    registry.get_default_api.return_value = None
    registry.get_template_row_migration.return_value = None
    strategy = DefaultSchedulerStrategy()
    strategy._template_defaults_service = _defaults_service(registry)
    strategy._logger = MagicMock()
    path = tmp_path / "templates.json"
    path.write_text(json.dumps({"templates": [row]}))
    with patch.object(strategy, "_get_provider_name", return_value=name):
        out = strategy.load_templates_from_path(str(path))
    assert out == [row]
