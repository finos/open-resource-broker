"""Golden outcomes for a template's allocation strategy per entry path.

The allocation-strategy defaults live only in the provider template defaults
and are applied by template default resolution. No aggregate and no entry path
decides one itself: a template built without default resolution keeps the
strategy unset, and the EC2 Fleet request builder then omits the option. These
tests pin the outcome for each path, so any change to where a default is
decided is a visible, deliberate edit here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# (i) base aggregate built directly: no provider default


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand", "heterogeneous"])
def test_base_template_leaves_strategy_unset(price_type: str) -> None:
    assert Template(template_id="t", price_type=price_type).allocation_strategy is None


# (ii) AWS aggregate built directly: no default either


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand", "heterogeneous"])
def test_aws_template_leaves_strategy_unset(price_type: str) -> None:
    template = AWSTemplate(template_id="t", price_type=price_type, **_FLEET_FIELDS)
    assert template.allocation_strategy is None
    assert template.allocation_strategy_on_demand is None


# (iii) stored template loaded without the key: left unset


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_stored_template_without_key_leaves_strategy_unset(price_type: str) -> None:
    template = TemplateSerializer().from_dict({"template_id": "t", "price_type": price_type})
    assert template.allocation_strategy is None


# (iv) HostFactory input without allocationStrategy: left unset


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_hostfactory_input_without_key_leaves_strategy_unset(price_type: str) -> None:
    dto = HostFactorySchedulerStrategy().parse_template_config(
        {"templateId": "t", "priceType": price_type}
    )
    assert dto.allocation_strategy is None


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


# non-AWS provider: Azure does not use the field and leaves it unset


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "ondemand"])
def test_azure_template_allocation_strategy_is_unset(price_type: str) -> None:
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
    assert template.allocation_strategy is None


# (vi) a template built without default resolution has no strategy, so the
# fleet request omits AllocationStrategy instead of inventing one.


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
@pytest.mark.parametrize("path", ["base", "aws", "stored"])
@pytest.mark.parametrize("price_type", ["spot", "heterogeneous"])
def test_fleet_request_omits_an_unset_allocation_strategy(path: str, price_type: str) -> None:
    template = _template_via(path, price_type)
    assert "AllocationStrategy" not in _fleet_request_options(template)


@pytest.mark.unit
@pytest.mark.parametrize("price_type", ["spot", "heterogeneous"])
def test_fleet_request_carries_the_resolved_default(price_type: str) -> None:
    from tests.providers.aws.template_defaults_support import make_defaults_service

    resolved = make_defaults_service().resolve_template_defaults(
        {"template_id": "t", "provider_type": "aws", "price_type": price_type, **_FLEET_FIELDS}
    )
    options = _fleet_request_options(AWSTemplate.model_validate(resolved))
    assert options.get("AllocationStrategy") == "price-capacity-optimized"


@pytest.mark.unit
@pytest.mark.parametrize("path", ["base", "aws", "stored"])
def test_on_demand_fleet_request_has_no_spot_options(path: str) -> None:
    assert _fleet_request_options(_template_via(path, "ondemand")) == {}


# non-AWS providers in HostFactory output: an unset strategy is omitted, the same
# way as any other absent optional field (maxSpotPrice here)


def _azure_template() -> Template:
    return AzureTemplate(
        template_id="azure-t",
        price_type="spot",
        provider_api="VMSS",
        location="eastus",
        resource_group="rg",
        ssh_key_name="key",
        vm_size="Standard_D2s_v3",
        image_id="/subscriptions/s/resourceGroups/rg/providers/Microsoft.Compute/images/img",
    )


def _gcp_template() -> Template:
    from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate

    return GCPTemplate(
        template_id="gcp-t",
        price_type="spot",
        provider_api="SingleVM",
        project_id="orb-example-12345",
        region="us-central1",
        zones=["us-central1-a"],
        instance_type="e2-standard-4",
        max_instances=1,
        source_image_family="debian-12",
        source_image_project="debian-cloud",
        boot_disk_type="pd-balanced",
        boot_disk_size_gb=64,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("build", "provider_type"),
    [(_azure_template, "azure"), (_gcp_template, "gcp")],
    ids=["azure", "gcp"],
)
def test_hostfactory_output_omits_unset_strategy_for_non_aws(build, provider_type: str) -> None:
    from orb.infrastructure.template.factories import TemplateDTOFactory

    template = build()
    assert template.allocation_strategy is None

    dto = TemplateDTOFactory().from_domain(template)
    strategy = HostFactorySchedulerStrategy()
    strategy._logger = MagicMock()
    with patch.object(strategy, "_get_active_provider_type", return_value=provider_type):
        formatted = strategy.format_templates_response([dto])["templates"][0]

    assert formatted["templateId"] == template.template_id
    assert "allocationStrategy" not in formatted
    assert "allocation_strategy" not in formatted
    assert "maxSpotPrice" not in formatted
    assert formatted["priceType"] == "spot"
