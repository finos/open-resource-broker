"""Azure resource identifier contract tests."""

import pytest

from orb.application.request.dto import RequestDTO
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.infrastructure.storage.repositories.request_repository import RequestSerializer
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy


def test_azure_does_not_advertise_one_resource_id_pattern():
    assert AzureProviderStrategy.get_resource_id_pattern() is None


@pytest.mark.parametrize(
    ("provider_api", "resource_id"),
    [
        ("VMSS", "vmss-orb-eastus2"),
        ("SingleVM", "vm-orb-0001"),
        ("CycleCloud", "req-12345678-1234-1234-1234-123456789012"),
    ],
)
def test_azure_opaque_resource_ids_round_trip_through_storage_and_api(
    provider_api: str,
    resource_id: str,
):
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="azure-template",
        machine_count=1,
        provider_type="azure",
        provider_name="azure-default",
        provider_api=provider_api,
    )
    request.resource_ids = [resource_id]

    serializer = RequestSerializer()
    restored = serializer.from_dict(serializer.to_dict(request))
    api_payload = RequestDTO.from_domain(restored).to_dict()

    assert restored.resource_ids == [resource_id]
    assert api_payload["resource_id"] == resource_id
    assert api_payload["resource_ids"] == [resource_id]
