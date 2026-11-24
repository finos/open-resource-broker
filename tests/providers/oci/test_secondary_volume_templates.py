"""Owned volume plans survive template conversion and fail before invalid launches."""

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from orb.providers.oci.validation.template_validator import OCITemplateValidator


def template(**options):
    return {
        "template_id": "worker",
        "provider_type": "oci",
        "image_id": "image",
        "machine_types": {"VM.Standard.E5.Flex": 1},
        "subnet_ids": ["subnet"],
        "compartment_id": "compartment",
        **options,
    }


def test_volume_order_and_policies_survive_domain_and_dto():
    volumes = [
        {"size_in_gbs": 50, "display_name": "work", "vpus_per_gb": 0, "delete_on_return": True},
        {
            "size_in_gbs": 32768,
            "display_name": "work",
            "vpus_per_gb": 20,
            "delete_on_return": False,
        },
        {"size_in_gbs": 100, "delete_on_return": False},
    ]
    OCIPlugin.reset_for_testing()
    try:
        plugin = OCIPlugin()
        plugin.initialize_provider()
        plugin.register_services_with_di(MagicMock())
        domain = OCITemplate.model_validate(
            template(provider_config={"secondary_volumes": volumes})
        )
        dto = TemplateDTOFactory().from_domain(domain)
        payload = OCITemplateMapper.build_launch_payload(dto.to_dict(), "worker-1")
        assert payload["secondary_volumes"] == volumes
    finally:
        OCIPlugin.reset_for_testing()


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"secondary_volumes": []},
        {"metadata": {"secondary_volumes": [{"size_in_gbs": 50, "delete_on_return": True}]}},
        {
            "provider_config": {
                "secondary_volumes": [{"size_in_gbs": 50, "delete_on_return": True}]
            },
            "secondary_volumes": [],
        },
    ],
)
def test_no_implicit_volume_launch(options):
    payload = OCITemplateMapper.build_launch_payload(template(**options), "worker-1")
    assert "secondary_volumes" not in payload


@pytest.mark.parametrize(
    "volumes",
    [
        {"size_in_gbs": 50, "delete_on_return": True},
        [{"size_in_gbs": 50}],
        [{"size_in_gbs": 50, "delete_on_return": "false"}],
        [{"size_in_gbs": 50, "delete_on_return": 1}],
        [{"size_in_gbs": 49, "delete_on_return": True}],
        [{"size_in_gbs": 32769, "delete_on_return": True}],
        [{"size_in_gbs": 50.5, "delete_on_return": True}],
        [{"size_in_gbs": "50", "delete_on_return": True}],
        [{"size_in_gbs": 50, "delete_on_return": True, "vpus_per_gb": True}],
        [{"size_in_gbs": 50, "delete_on_return": True, "vpus_per_gb": 15}],
        [{"size_in_gbs": 50, "delete_on_return": True, "vpus_per_gb": 30}],
        [{"size_in_gbs": 50, "delete_on_return": True, "volume_id": "existing"}],
        [{"size_in_gbs": 50, "delete_on_return": True}] * 33,
    ],
)
def test_invalid_volume_plan_is_rejected_by_registration_and_mapping(volumes):
    row = template(provider_config={"secondary_volumes": volumes})
    validation = OCITemplateValidator().validate(row)
    assert not validation.valid
    assert any("secondary_volumes" in message for message in validation.errors)
    with pytest.raises(ValidationError, match="secondary_volumes"):
        OCITemplateMapper.build_launch_payload(row, "worker-1")


@pytest.mark.parametrize(
    "shape", ["VM.Standard.E5.Flex", "VM.Standard.E6.Flex", "VM.DenseIO.E5.Flex"]
)
def test_paravirtualized_attachment_count_checked_before_launch(shape):
    volumes = [{"size_in_gbs": 50, "delete_on_return": True}] * 17
    row = template(machine_types={shape: 1}, secondary_volumes=volumes)
    assert not OCITemplateValidator().validate(row).valid
    with pytest.raises(ValidationError, match="16 paravirtualized"):
        OCITemplateMapper.build_launch_payload(row, "worker-1")
    row["secondary_volumes"] = volumes[:16]
    assert len(OCITemplateMapper.build_launch_payload(row, "worker-1")["secondary_volumes"]) == 16


def test_iscsi_bare_metal_can_use_more_than_sixteen_attachments():
    volumes = [{"size_in_gbs": 50, "delete_on_return": False}] * 32
    row = template(machine_types={"BM.Standard.E5.192": 1}, secondary_volumes=volumes)
    assert OCITemplateValidator().validate(row).valid
    assert len(OCITemplateMapper.build_launch_payload(row, "worker-1")["secondary_volumes"]) == 32


@pytest.mark.asyncio
async def test_invalid_policy_never_reaches_oci():
    runner = MagicMock()
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)
    operation = ProviderOperation(
        operation_type=ProviderOperationType.CREATE_INSTANCES,
        parameters={"template_config": template(secondary_volumes=[{"size_in_gbs": 50}])},
    )
    result = await handler.create_instances(operation)
    assert not result.success
    assert "delete_on_return" in (result.error_message or "")
    runner.assert_not_called()
