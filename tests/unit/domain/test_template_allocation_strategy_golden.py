"""Golden outcomes for a template's allocation strategy per entry path.

When a template carries no explicit ``allocation_strategy`` the value it ends
up with depends on how it was built. These tests pin the current outcome for
each path, plus what the EC2 Fleet request builder emits from it, so any change
to where the default is decided is a visible, deliberate edit here.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.request_identifiers import RequestId
from orb.domain.request.request_types import RequestStatus
from orb.domain.request.value_objects import RequestType
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)
from orb.infrastructure.storage.repositories.template_repository import TemplateSerializer
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import normalise_allocation_strategy
from orb.providers.aws.infrastructure.handlers.ec2_fleet.config_builder import (
    EC2FleetConfigBuilder,
)
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate

_FLEET_FIELDS = {
    "provider_api": "EC2Fleet",
    "machine_types": {"t3.micro": 1},
    "subnet_ids": ["subnet-1"],
}


def _request() -> Request:
    return Request(
        request_id=RequestId(value="req-00000000-0000-0000-0000-000000000001"),
        request_type=RequestType.ACQUIRE,
        provider_type="aws",
        template_id="tmpl-1",
        requested_count=2,
        status=RequestStatus.PENDING,
    )


def _fleet_request_options(template: Template) -> dict:
    """SpotOptions the EC2 Fleet builder emits for ``template``."""
    aws_template = (
        template
        if isinstance(template, AWSTemplate)
        else AWSTemplate.model_validate(template.model_dump())
    )
    builder = EC2FleetConfigBuilder(None, MagicMock(), MagicMock())
    return builder.build(aws_template, _request(), "lt-1", "1").get("SpotOptions", {})


# (i) base aggregate built directly


@pytest.mark.unit
@pytest.mark.parametrize(
    ("price_type", "expected"),
    [
        ("spot", "priceCapacityOptimized"),
        ("ondemand", "lowestPrice"),
        ("heterogeneous", "lowestPrice"),
    ],
)
def test_base_template_default_follows_price_type(price_type: str, expected: str) -> None:
    assert Template(template_id="t", price_type=price_type).allocation_strategy == expected


# (ii) AWS aggregate built directly


@pytest.mark.unit
@pytest.mark.parametrize(
    ("price_type", "expected"),
    [
        ("spot", "priceCapacityOptimized"),
        ("ondemand", "lowestPrice"),
        ("heterogeneous", "lowestPrice"),
    ],
)
def test_aws_template_default_follows_price_type(price_type: str, expected: str) -> None:
    template = AWSTemplate(template_id="t", price_type=price_type, **_FLEET_FIELDS)
    assert template.allocation_strategy == expected


# (iii) stored template loaded without the key: snake_case, whatever the price type


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_stored_template_without_key_defaults_to_snake_case_lowest_price(
    price_type: str,
) -> None:
    template = TemplateSerializer().from_dict({"template_id": "t", "price_type": price_type})
    assert template.allocation_strategy == "lowest_price"


# (iv) HostFactory input without allocationStrategy: camelCase, whatever the price type


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_hostfactory_input_without_key_defaults_to_camel_case_lowest_price(
    price_type: str,
) -> None:
    dto = HostFactorySchedulerStrategy().parse_template_config(
        {"templateId": "t", "priceType": price_type}
    )
    assert dto.allocation_strategy == "lowestPrice"


# (v) the AWS normaliser collapses every spelling to one canonical value


@pytest.mark.unit
@pytest.mark.parametrize(
    "spellings",
    [
        ("lowestPrice", "lowest_price", "lowest-price"),
        ("priceCapacityOptimized", "price_capacity_optimized", "price-capacity-optimized"),
        ("capacityOptimized", "capacity_optimized", "capacity-optimized"),
    ],
)
def test_aws_normaliser_maps_every_spelling_to_one_value(spellings: tuple[str, ...]) -> None:
    assert len({normalise_allocation_strategy(s) for s in spellings}) == 1


# non-AWS provider: Azure does not use the field and holds an inert empty value


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_azure_template_allocation_strategy_is_inert(price_type: str) -> None:
    template = AzureTemplate(
        template_id="t",
        price_type=price_type,
        provider_api="VMSS",
        location="eastus",
        resource_group="rg",
        ssh_key_name="key",
        vm_size="Standard_D2s_v3",
        image_id="/subscriptions/s/resourceGroups/rg/providers/Microsoft.Compute/images/img",
    )
    assert template.allocation_strategy == ""


# (vi) the fleet request carries AllocationStrategy for each entry path; the
# builder only emits it when the template value is truthy, so a path that
# stopped supplying a default would silently drop the parameter.


def _template_via(path: str, price_type: str) -> Template:
    if path == "base":
        return Template(template_id="t", price_type=price_type, **_FLEET_FIELDS)
    if path == "aws":
        return AWSTemplate(template_id="t", price_type=price_type, **_FLEET_FIELDS)
    if path == "stored":
        return TemplateSerializer().from_dict(
            {"template_id": "t", "price_type": price_type, **_FLEET_FIELDS}
        )
    raise AssertionError(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "price_type", "expected"),
    [
        ("base", "spot", "price-capacity-optimized"),
        ("aws", "spot", "price-capacity-optimized"),
        ("stored", "spot", "lowest-price"),
        ("base", "heterogeneous", "lowest-price"),
        ("aws", "heterogeneous", "lowest-price"),
        ("stored", "heterogeneous", "lowest-price"),
    ],
)
def test_fleet_request_carries_allocation_strategy(
    path: str, price_type: str, expected: str
) -> None:
    template = _template_via(path, price_type)
    options = _fleet_request_options(template)
    assert options.get("AllocationStrategy") == expected


@pytest.mark.unit
@pytest.mark.parametrize("path", ["base", "aws", "stored"])
def test_on_demand_fleet_request_has_no_spot_options(path: str) -> None:
    assert _fleet_request_options(_template_via(path, "ondemand")) == {}
