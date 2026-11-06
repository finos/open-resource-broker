"""The AWS template is the single place an allocation-strategy default is decided.

Spot and heterogeneous templates without an explicit strategy get
``priceCapacityOptimized``; on-demand templates get ``lowestPrice``. Explicit
values keep their meaning but are stored in canonical camelCase, and unknown
values still load so the request path can reject them.
"""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.template.factory import TemplateFactory
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.aws.configuration.template_extension import AWSTemplateExtensionConfig
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.aws_template_dto_config import AWSTemplateDTOConfig
from orb.providers.aws.infrastructure.handlers.asg.config_builder import ASGConfigBuilder
from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import (
    EC2FleetConfigBuilder,
)
from orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder import (
    SpotFleetConfigBuilder,
)
from orb.providers.aws.scheduler.hostfactory_field_mapping import AWSFieldMapping

pytestmark = pytest.mark.unit

_FLEET_FIELDS: dict[str, Any] = {
    "machine_types": {"t3.micro": 1, "t3.small": 1},
    "subnet_ids": ["subnet-1"],
    "security_group_ids": ["sg-1"],
}


def _request(requested_count: int = 2) -> Request:
    request = MagicMock()
    request.request_id = "req-00000000-0000-0000-0000-000000000001"
    request.requested_count = requested_count
    return cast(Request, request)


def _aws_factory() -> TemplateFactory:
    factory = TemplateFactory()
    factory.register_provider_template_class("aws", AWSTemplate)
    return factory


@pytest.fixture
def aws_dto_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TemplateExtensionRegistry._extensions, "aws", AWSTemplateDTOConfig)


# ---------------------------------------------------------------------------
# AWSTemplate built directly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("price_type", "expected"),
    [
        ("spot", "priceCapacityOptimized"),
        ("heterogeneous", "priceCapacityOptimized"),
        ("ondemand", "lowestPrice"),
    ],
)
def test_aws_template_default_follows_price_type(price_type: str, expected: str) -> None:
    template = AWSTemplate(template_id="t", price_type=price_type)
    assert template.allocation_strategy == expected


def test_heterogeneous_ec2_fleet_uses_price_capacity_optimized_for_spot() -> None:
    template = AWSTemplate(
        template_id="t",
        provider_api="EC2Fleet",
        price_type="heterogeneous",
        percent_on_demand=50,
        **_FLEET_FIELDS,
    )

    config = EC2FleetConfigBuilder(None, MagicMock(), MagicMock()).build(
        template, _request(), "lt-1", "1"
    )

    assert config["SpotOptions"]["AllocationStrategy"] == "price-capacity-optimized"


@pytest.mark.parametrize(
    ("explicit", "stored"),
    [
        ("lowest_price", "lowestPrice"),
        ("capacity-optimized", "capacityOptimized"),
        ("diversified", "diversified"),
    ],
)
def test_explicit_strategy_is_kept_in_canonical_spelling(explicit: str, stored: str) -> None:
    template = AWSTemplate(template_id="t", price_type="spot", allocation_strategy=explicit)
    assert template.allocation_strategy == stored


def test_value_object_input_is_unwrapped() -> None:
    from orb.providers.aws.value_objects import AWSAllocationStrategy

    template = AWSTemplate(
        template_id="t",
        price_type="spot",
        allocation_strategy=cast(Any, AWSAllocationStrategy("capacity_optimized")),
    )
    assert template.allocation_strategy == "capacityOptimized"


def test_unknown_strategy_still_loads_and_fails_at_request_time() -> None:
    template = _aws_factory().create_template(
        {
            "template_id": "t",
            "provider_type": "aws",
            "provider_api": "EC2Fleet",
            "price_type": "spot",
            "allocation_strategy": "bogus",
            **_FLEET_FIELDS,
        }
    )

    assert isinstance(template, AWSTemplate)
    assert template.allocation_strategy == "bogus"
    with pytest.raises(ValueError, match="Unknown allocation strategy 'bogus'"):
        EC2FleetConfigBuilder(None, MagicMock(), MagicMock()).build(
            template, _request(), "lt-1", "1"
        )


# ---------------------------------------------------------------------------
# HostFactory load path with the AWS field mapping registered
# ---------------------------------------------------------------------------


def _load_hf_template(tmp_path, hf_template: dict[str, Any]) -> AWSTemplate:
    path = tmp_path / "awsprov_templates.json"
    path.write_text(json.dumps({"templates": [hf_template]}))

    strategy = HostFactorySchedulerStrategy()
    strategy._logger = MagicMock()
    with (
        patch.object(
            FieldMappingRegistry,
            "get_or_none",
            side_effect=lambda provider_type: AWSFieldMapping() if provider_type == "aws" else None,
        ),
        patch.object(strategy, "_get_active_provider_type", return_value="aws"),
        patch.object(strategy, "_get_provider_name", return_value="aws-default"),
        patch.object(strategy, "_apply_template_defaults", side_effect=lambda t, _p: t),
    ):
        loaded = strategy.load_templates_from_path(str(path))

    assert len(loaded) == 1
    template = _aws_factory().create_template(loaded[0])
    assert isinstance(template, AWSTemplate)
    return template


@pytest.mark.parametrize("provider_api", ["EC2Fleet", "SpotFleet", "ASG"])
def test_hostfactory_spot_template_gets_spot_default(tmp_path, provider_api: str) -> None:
    template = _load_hf_template(
        tmp_path,
        {
            "templateId": "hf-spot",
            "providerType": "aws",
            "providerApi": provider_api,
            "priceType": "spot",
            "maxNumber": 4,
            "vmTypes": {"t3.micro": 1, "t3.small": 1},
            "subnetIds": ["subnet-1"],
            "securityGroupIds": ["sg-1"],
            "fleetRole": "arn:aws:iam::123456789012:role/aws-ec2-spot-fleet-tagging-role",
        },
    )

    assert template.allocation_strategy == "priceCapacityOptimized"

    request = _request()
    if provider_api == "EC2Fleet":
        config = EC2FleetConfigBuilder(None, MagicMock(), MagicMock()).build(
            template, request, "lt-1", "1"
        )
        assert config["SpotOptions"]["AllocationStrategy"] == "price-capacity-optimized"
    elif provider_api == "SpotFleet":
        with patch(
            "orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder.build_resource_tags",
            return_value=[],
        ):
            config = SpotFleetConfigBuilder(None, MagicMock(), MagicMock()).build(
                template, request, "lt-1", "1"
            )
        assert config["AllocationStrategy"] == "priceCapacityOptimized"
    else:
        config = ASGConfigBuilder(None, MagicMock(), MagicMock()).build(
            "asg-1", template, request, "lt-1", "1"
        )
        distribution = config["MixedInstancesPolicy"]["InstancesDistribution"]
        assert distribution["SpotAllocationStrategy"] == "price-capacity-optimized"


def test_hostfactory_ondemand_template_gets_lowest_price(tmp_path) -> None:
    template = _load_hf_template(
        tmp_path,
        {
            "templateId": "hf-ondemand",
            "providerType": "aws",
            "providerApi": "EC2Fleet",
            "maxNumber": 2,
            "vmType": "t3.micro",
            "subnetIds": ["subnet-1"],
        },
    )
    assert template.allocation_strategy == "lowestPrice"


def test_hostfactory_explicit_strategy_is_kept(tmp_path) -> None:
    template = _load_hf_template(
        tmp_path,
        {
            "templateId": "hf-explicit",
            "providerType": "aws",
            "providerApi": "EC2Fleet",
            "priceType": "spot",
            "allocationStrategy": "capacityOptimized",
            "maxNumber": 2,
            "vmType": "t3.micro",
            "subnetIds": ["subnet-1"],
        },
    )
    assert template.allocation_strategy == "capacityOptimized"


# ---------------------------------------------------------------------------
# configuration-manager path used by the template API
# ---------------------------------------------------------------------------


def _manager() -> TemplateConfigurationManager:
    return TemplateConfigurationManager(
        config_manager=MagicMock(),
        scheduler_strategy=MagicMock(),
        logger=MagicMock(),
        cache_service=MagicMock(),
        storage_service=MagicMock(),
        template_factory=_aws_factory(),
    )


@pytest.mark.usefixtures("aws_dto_extension")
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"price_type": "spot"}, "priceCapacityOptimized"),
        ({"price_type": "heterogeneous"}, "priceCapacityOptimized"),
        ({"price_type": "ondemand"}, "lowestPrice"),
        ({"price_type": "spot", "allocation_strategy": "lowest_price"}, "lowestPrice"),
    ],
)
def test_template_dto_carries_canonical_strategy(raw: dict[str, Any], expected: str) -> None:
    dto = _manager()._convert_dict_to_template_dto(
        {"template_id": "t", "provider_type": "aws", "provider_api": "EC2Fleet", **raw}
    )

    assert dto.allocation_strategy == expected
    provider_config = dto.provider_config.model_dump() if dto.provider_config else {}
    assert "allocation_strategy" not in provider_config


@pytest.mark.usefixtures("aws_dto_extension")
def test_base_aws_template_without_strategy_converts_to_dto() -> None:
    template = Template(template_id="t", provider_type="aws", price_type="spot")

    dto = TemplateDTOFactory().from_domain(template)

    assert dto.allocation_strategy is None
    provider_config = dto.provider_config.model_dump() if dto.provider_config else {}
    assert "allocation_strategy" not in provider_config


def test_aws_extension_config_has_no_competing_default() -> None:
    assert AWSTemplateExtensionConfig.model_validate({}).allocation_strategy is None
    explicit_none = AWSTemplateExtensionConfig.model_validate({"allocation_strategy": None})
    assert explicit_none.allocation_strategy is None
