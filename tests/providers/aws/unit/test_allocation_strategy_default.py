"""Allocation-strategy defaults come from the AWS provider-type template defaults.

``allocation_strategy`` is the spot-pool strategy and
``allocation_strategy_on_demand`` the on-demand-pool strategy. Neither has a
default on the AWS template itself: the bundled
``provider_defaults.aws.template_defaults`` sets them (``priceCapacityOptimized``
and ``lowestPrice``), provider instance defaults and template rows override
them, and template default resolution applies them. Values are stored in
canonical camelCase and invalid values are rejected.
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
from tests.providers.aws.template_defaults_support import (
    bundled_aws_template_defaults,
    make_defaults_service,
)

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


def _resolved(row: dict[str, Any], **service_kwargs: Any) -> AWSTemplate:
    resolved = make_defaults_service(**service_kwargs).resolve_template_defaults(
        {"template_id": "t", "provider_type": "aws", **row}, "aws-default"
    )
    template = _aws_factory().create_template(resolved)
    assert isinstance(template, AWSTemplate)
    return template


@pytest.fixture
def aws_dto_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(TemplateExtensionRegistry._extensions, "aws", AWSTemplateDTOConfig)


# ---------------------------------------------------------------------------
# The bundled defaults
# ---------------------------------------------------------------------------


def test_bundled_type_defaults_carry_both_strategies_and_no_price_type() -> None:
    defaults = bundled_aws_template_defaults()
    assert defaults["allocation_strategy"] == "priceCapacityOptimized"
    assert defaults["allocation_strategy_on_demand"] == "lowestPrice"
    # price_type comes from the global template.default_price_type only.
    assert "price_type" not in defaults


@pytest.mark.parametrize("price_type", ["spot", "heterogeneous", "ondemand"])
def test_resolved_strategies_do_not_depend_on_price_type(price_type: str) -> None:
    template = _resolved({"price_type": price_type})
    assert template.allocation_strategy == "priceCapacityOptimized"
    assert template.allocation_strategy_on_demand is not None
    assert template.allocation_strategy_on_demand.value == "lowestPrice"


def test_global_price_type_applies_without_a_type_layer_value() -> None:
    template = _resolved({}, global_config={"default_price_type": "spot"})
    assert template.price_type == "spot"


def test_instance_and_template_layers_override_the_type_defaults() -> None:
    template = _resolved(
        {"price_type": "spot"},
        instance_defaults={
            "allocation_strategy": "capacityOptimized",
            "allocation_strategy_on_demand": "prioritized",
        },
    )
    assert template.allocation_strategy == "capacityOptimized"
    assert template.allocation_strategy_on_demand is not None
    assert template.allocation_strategy_on_demand.value == "prioritized"

    template = _resolved(
        {"price_type": "spot", "allocation_strategy": "diversified"},
        instance_defaults={"allocation_strategy": "capacityOptimized"},
    )
    assert template.allocation_strategy == "diversified"


# ---------------------------------------------------------------------------
# AWSTemplate built directly: no default, one spelling boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("price_type", ["spot", "heterogeneous", "ondemand"])
def test_aws_template_has_no_strategy_default(price_type: str) -> None:
    template = AWSTemplate(template_id="t", price_type=price_type)
    assert template.allocation_strategy is None
    assert template.allocation_strategy_on_demand is None
    assert template.get_ec2_fleet_allocation_strategy() is None
    assert template.get_ec2_fleet_on_demand_allocation_strategy() is None


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


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ({"allocation_strategy": "bogus"}, "Unknown allocation strategy 'bogus'"),
        ({"allocation_strategy": "prioritized"}, "does not apply to spot pools"),
        (
            {"allocation_strategy_on_demand": "capacityOptimized"},
            "does not apply to on-demand pools",
        ),
    ],
)
def test_invalid_strategy_is_rejected_when_the_template_is_built(
    row: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _aws_factory().create_template(
            {
                "template_id": "t",
                "provider_type": "aws",
                "provider_api": "EC2Fleet",
                "price_type": "spot",
                **row,
                **_FLEET_FIELDS,
            }
        )


def test_unset_strategy_omits_the_options() -> None:
    template = AWSTemplate(
        template_id="t", provider_api="EC2Fleet", price_type="heterogeneous", **_FLEET_FIELDS
    )
    config = EC2FleetConfigBuilder(None, MagicMock(), MagicMock()).build(
        template, _request(), "lt-1", "1"
    )
    assert "SpotOptions" not in config
    assert "OnDemandOptions" not in config


# ---------------------------------------------------------------------------
# HostFactory load path, with real default resolution
# ---------------------------------------------------------------------------


def _load_hf_template(tmp_path, hf_template: dict[str, Any]) -> AWSTemplate:
    path = tmp_path / "awsprov_templates.json"
    path.write_text(json.dumps({"templates": [hf_template]}))

    strategy = HostFactorySchedulerStrategy()
    strategy._logger = MagicMock()
    defaults_service = make_defaults_service()
    with (
        patch.object(
            FieldMappingRegistry,
            "get_or_none",
            side_effect=lambda provider_type: AWSFieldMapping() if provider_type == "aws" else None,
        ),
        patch.object(strategy, "_get_active_provider_type", return_value="aws"),
        patch.object(strategy, "_get_provider_name", return_value="aws-default"),
        patch.object(
            strategy,
            "_apply_template_defaults",
            side_effect=lambda t, p: defaults_service.resolve_template_defaults(t, p),
        ),
    ):
        loaded = strategy.load_templates_from_path(str(path))

    assert len(loaded) == 1
    template = _aws_factory().create_template(loaded[0])
    assert isinstance(template, AWSTemplate)
    return template


@pytest.mark.parametrize("provider_api", ["EC2Fleet", "SpotFleet", "ASG"])
def test_hostfactory_spot_template_gets_the_type_default(tmp_path, provider_api: str) -> None:
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
        assert "OnDemandOptions" not in config
    elif provider_api == "SpotFleet":
        with patch(
            "orb.providers.aws.infrastructure.handlers.spot_fleet.config_builder.build_resource_tags",
            return_value=[],
        ):
            config = SpotFleetConfigBuilder(None, MagicMock(), MagicMock()).build(
                template, request, "lt-1", "1"
            )
        assert config["AllocationStrategy"] == "priceCapacityOptimized"
        assert "OnDemandAllocationStrategy" not in config
    else:
        config = ASGConfigBuilder(None, MagicMock(), MagicMock()).build(
            "asg-1", template, request, "lt-1", "1"
        )
        distribution = config["MixedInstancesPolicy"]["InstancesDistribution"]
        assert distribution["SpotAllocationStrategy"] == "price-capacity-optimized"
        assert distribution["OnDemandAllocationStrategy"] == "lowest-price"


def test_hostfactory_ondemand_template_gets_both_type_defaults(tmp_path) -> None:
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
    assert template.price_type == "ondemand"
    assert template.allocation_strategy == "priceCapacityOptimized"
    assert template.allocation_strategy_on_demand is not None
    assert template.allocation_strategy_on_demand.value == "lowestPrice"

    config = EC2FleetConfigBuilder(None, MagicMock(), MagicMock()).build(
        template, _request(), "lt-1", "1"
    )
    assert "SpotOptions" not in config
    assert config["OnDemandOptions"] == {"AllocationStrategy": "lowest-price"}


def test_hostfactory_on_demand_strategy_is_kept(tmp_path) -> None:
    template = _load_hf_template(
        tmp_path,
        {
            "templateId": "hf-explicit",
            "providerType": "aws",
            "providerApi": "EC2Fleet",
            "priceType": "heterogeneous",
            "percentOnDemand": 50,
            "allocationStrategy": "capacityOptimized",
            "allocationStrategyOnDemand": "prioritized",
            "maxNumber": 2,
            "vmTypes": {"t3.micro": 1},
            "subnetIds": ["subnet-1"],
        },
    )
    assert template.allocation_strategy == "capacityOptimized"
    assert template.allocation_strategy_on_demand is not None
    assert template.allocation_strategy_on_demand.value == "prioritized"


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
    ("raw", "spot", "on_demand"),
    [
        ({"allocation_strategy": "lowest_price"}, "lowestPrice", None),
        ({"allocation_strategy_on_demand": "prioritized"}, None, "prioritized"),
        (
            {
                "allocation_strategy": "capacity-optimized",
                "allocation_strategy_on_demand": "lowest-price",
            },
            "capacityOptimized",
            "lowestPrice",
        ),
    ],
)
def test_template_dto_carries_both_canonical_strategies(
    raw: dict[str, Any], spot: Any, on_demand: Any
) -> None:
    dto = _manager()._convert_dict_to_template_dto(
        {
            "template_id": "t",
            "provider_type": "aws",
            "provider_api": "EC2Fleet",
            "price_type": "heterogeneous",
            **raw,
        }
    )

    assert dto.allocation_strategy == spot
    assert dto.allocation_strategy_on_demand == on_demand
    provider_config = dto.provider_config.model_dump() if dto.provider_config else {}
    assert "allocation_strategy" not in provider_config
    assert "allocation_strategy_on_demand" not in provider_config


@pytest.mark.usefixtures("aws_dto_extension")
def test_base_aws_template_without_strategy_converts_to_dto() -> None:
    template = Template(template_id="t", provider_type="aws", price_type="spot")

    dto = TemplateDTOFactory().from_domain(template)

    assert dto.allocation_strategy is None
    assert dto.allocation_strategy_on_demand is None


def test_aws_extension_config_has_no_strategy_fields() -> None:
    fields = AWSTemplateExtensionConfig.model_fields
    assert "allocation_strategy" not in fields
    assert "allocation_strategy_on_demand" not in fields
