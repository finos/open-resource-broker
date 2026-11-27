"""Every shipped and example AWS template validates cleanly on the validate path.

Rows are judged the way ``orb templates validate`` judges them: template defaults
are applied first, then the active provider's validator runs.  The real registry
and the real AWS registration are used.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.infrastructure.adapters.template_configuration_adapter import (
    TemplateConfigurationAdapter,
)
from tests.providers.contract.fresh_registry import freshly_registered_providers

ROOT = Path(__file__).resolve().parents[4]
SHIPPED = json.loads((ROOT / "config" / "aws_templates.json").read_text())["templates"]

AWS_DEFAULTS = {
    "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "machine_types": {"t3.micro": 1},
    "subnet_ids": ["subnet-0123456789abcdef0"],
    "security_group_ids": ["sg-0123456789abcdef0"],
}


def _example_rows() -> list[dict]:
    from orb.providers.aws.infrastructure.handlers.asg.handler import ASGHandler
    from orb.providers.aws.infrastructure.handlers.ec2_fleet.example_templates import (
        build_ec2_fleet_example_templates,
    )
    from orb.providers.aws.infrastructure.handlers.microvm.example_templates import (
        build_microvm_example_templates,
    )
    from orb.providers.aws.infrastructure.handlers.run_instances.handler import (
        RunInstancesHandler,
    )
    from orb.providers.aws.infrastructure.handlers.spot_fleet.handler import SpotFleetHandler

    templates = [
        *build_ec2_fleet_example_templates(),
        *build_microvm_example_templates(),
        *SpotFleetHandler.get_example_templates(),
        *ASGHandler.get_example_templates(),
        *RunInstancesHandler.get_example_templates(),
    ]
    return [t.model_dump(mode="json", exclude_none=True) for t in templates]


EXAMPLES = _example_rows()


def _instance(name: str, type_: str):
    return SimpleNamespace(name=name, type=type_, enabled=True, config={"region": "us-east-1"})


def _provider_config(instances):
    return SimpleNamespace(
        providers=instances,
        provider_defaults={"aws": SimpleNamespace(template_defaults=AWS_DEFAULTS)},
        get_active_providers=lambda: instances,
    )


def _build(registry, instances):
    config_manager = MagicMock()
    config_manager.get_provider_config.return_value = _provider_config(instances)
    manager = MagicMock()
    manager._registry = registry
    manager.config_manager = config_manager
    adapter = TemplateConfigurationAdapter(template_manager=manager, logger=MagicMock())
    defaults = TemplateDefaultsService(config_manager, MagicMock())
    return adapter, defaults


@pytest.fixture(scope="module")
def registry():
    with freshly_registered_providers() as populated:
        yield populated


@pytest.fixture(params=["aws-only", "aws-and-azure"])
def parts(request, registry):
    instances = [_instance("aws-1", "aws")]
    if request.param == "aws-and-azure":
        instances.append(_instance("azure-1", "azure"))
    return _build(registry, instances)


def _ids(rows):
    return [r.get("templateId") or r["template_id"] for r in rows]


@pytest.mark.parametrize("row", SHIPPED, ids=_ids(SHIPPED))
def test_shipped_row_has_no_errors_once_defaults_apply(parts, row) -> None:
    adapter, defaults = parts
    assert adapter.validate_template_config(defaults.resolve_template_defaults(dict(row))) == []


@pytest.mark.parametrize("row", EXAMPLES, ids=_ids(EXAMPLES))
def test_example_row_has_no_errors_once_defaults_apply(parts, row) -> None:
    adapter, defaults = parts
    assert adapter.validate_template_config(defaults.resolve_template_defaults(dict(row))) == []


def test_shipped_row_without_defaults_lacks_only_the_image(parts) -> None:
    # Shipped rows carry no image: the generic check needs the defaults applied first.
    adapter, _ = parts
    errors = adapter.validate_template_config(dict(SHIPPED[0]))
    assert errors == ["Image ID is required", "image_id is required for AWS templates"]


def test_stored_template_keeps_its_image_through_defaults(parts) -> None:
    # What create or update hands to validation: a stored, snake_case row.
    adapter, defaults = parts
    stored = next(r for r in EXAMPLES if r["template_id"] == "EC2Fleet-Instant-OnDemand")
    stored = {**stored, "machine_image": "ami-0abc1234"}
    resolved = defaults.resolve_template_defaults(dict(stored))
    assert resolved["machine_image"] == "ami-0abc1234"
    assert adapter.validate_template_config(resolved) == []


def test_bad_values_in_a_shipped_row_are_reported_despite_defaults(parts) -> None:
    adapter, defaults = parts
    row = {
        **SHIPPED[0],
        "subnetIds": ["bad"],
        "securityGroupIds": "sg-1,nope",
        "percentOnDemand": 150,
    }
    errors = adapter.validate_template_config(defaults.resolve_template_defaults(row))
    assert errors == [
        "Invalid AWS subnet ID format: bad",
        "Invalid AWS security group ID format: nope",
        "percent_on_demand must be between 0 and 100",
    ]
