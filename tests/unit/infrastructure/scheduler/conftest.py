"""Shared fixtures and SCHEDULER_CONFIGS for parametric scheduler tests."""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "src"))

from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from tests.onaws.plugin_io_schemas import (
    expected_get_available_templates_schema_default,
    expected_get_available_templates_schema_hostfactory,
    expected_request_machines_schema_default,
    expected_request_machines_schema_hostfactory,
    expected_request_status_schema_default,
    expected_request_status_schema_hostfactory,
)

# ---------------------------------------------------------------------------
# Minimal fixture templates
# ---------------------------------------------------------------------------

_MINIMAL_HF_TEMPLATE_ON_DISK: dict[str, Any] = {
    "templateId": "hf-tpl-001",
    "maxNumber": 5,
    "vmType": "t3.medium",
    "subnetIds": ["subnet-aaa"],
    "securityGroupIds": ["sg-111"],
    "priceType": "ondemand",
    "allocationStrategy": "lowest_price",
    "providerApi": "EC2Fleet",
    "providerType": "aws",
}

_MINIMAL_SNAKE_TEMPLATE: dict[str, Any] = {
    "template_id": "default-tpl-001",
    "max_instances": 5,
    "machine_types": {"t3.medium": 1},
    "subnet_ids": ["subnet-aaa"],
    "security_group_ids": ["sg-111"],
    "price_type": "ondemand",
    "allocation_strategy": "lowest_price",
    "provider_api": "EC2Fleet",
    "provider_type": "aws",
}


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------


def write_hf_file(path: Path, templates: list[dict]) -> None:
    path.write_text(json.dumps({"scheduler_type": "hostfactory", "templates": templates}))


def write_default_file(path: Path, templates: list[dict]) -> None:
    path.write_text(json.dumps({"scheduler_type": "default", "templates": templates}))


# ---------------------------------------------------------------------------
# Strategy factories
# ---------------------------------------------------------------------------


def make_hf_strategy(defaults_service=None) -> HostFactorySchedulerStrategy:
    return HostFactorySchedulerStrategy(template_defaults_service=defaults_service)


def make_default_strategy(defaults_service=None) -> DefaultSchedulerStrategy:
    return DefaultSchedulerStrategy(template_defaults_service=defaults_service)


def make_defaults_service(subnet_ids: list[str], sg_ids: list[str]) -> MagicMock:
    """Build a minimal mock TemplateDefaultsPort that injects subnet/sg defaults."""
    svc = MagicMock()

    def resolve(template_dict, provider_instance_name=None):
        result = dict(template_dict)
        if not result.get("subnet_ids"):
            result["subnet_ids"] = subnet_ids
        if not result.get("security_group_ids"):
            result["security_group_ids"] = sg_ids
        return result

    svc.resolve_template_defaults.side_effect = resolve
    svc.resolve_provider_api_default.return_value = "EC2Fleet"
    return svc


# ---------------------------------------------------------------------------
# SCHEDULER_CONFIGS — the single extension point for new schedulers
# ---------------------------------------------------------------------------

SCHEDULER_CONFIGS: dict[str, dict[str, Any]] = {
    "hostfactory": {
        "strategy_factory": make_hf_strategy,
        "minimal_template_on_disk": _MINIMAL_HF_TEMPLATE_ON_DISK,
        "write_file_fn": write_hf_file,
        "minimal_domain_dict": _MINIMAL_SNAKE_TEMPLATE,
        "expected_on_disk_key": "templateId",
        "expected_domain_key": "template_id",
        "schemas": {
            "get_available_templates": expected_get_available_templates_schema_hostfactory,
            "request_machines": expected_request_machines_schema_hostfactory,
            "request_status": expected_request_status_schema_hostfactory,
        },
        "response_field_names": {
            "request_id": "requestId",
            "machine_id": "machineId",
            "instance_type": "vmType",
            "private_ip": "privateIpAddress",
        },
    },
    "default": {
        "strategy_factory": make_default_strategy,
        "minimal_template_on_disk": _MINIMAL_SNAKE_TEMPLATE,
        "write_file_fn": write_default_file,
        "minimal_domain_dict": _MINIMAL_SNAKE_TEMPLATE,
        "expected_on_disk_key": "template_id",
        "expected_domain_key": "template_id",
        "schemas": {
            "get_available_templates": expected_get_available_templates_schema_default,
            "request_machines": expected_request_machines_schema_default,
            "request_status": expected_request_status_schema_default,
        },
        "response_field_names": {
            "request_id": "request_id",
            "machine_id": "machine_id",
            "instance_type": "instance_type",
            "private_ip": "private_ip_address",
        },
    },
}


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=list(SCHEDULER_CONFIGS.keys()))
def scheduler_config(request):
    """Parametric fixture — yields one SCHEDULER_CONFIGS entry per scheduler type."""
    return SCHEDULER_CONFIGS[request.param]


@pytest.fixture
def hf_strategy():
    return make_hf_strategy()


@pytest.fixture
def default_strategy():
    return make_default_strategy()
