"""Runtime tests for the GCP provider infrastructure and strategy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.infrastructure.gcp_handler_factory import GCPHandlerFactory
from orb.providers.gcp.infrastructure.handlers.mig_handler import (
    GCPManagedInstanceGroupHandler,
)
from orb.providers.gcp.infrastructure.handlers.single_vm_handler import GCPSingleVMHandler
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy
class _ComputeClientStub:
    def __init__(self) -> None:
        self.created_instances: list[tuple[str, dict]] = []
        self.created_templates: list[tuple[str, dict]] = []
        self.created_migs: list[tuple[str, str, dict]] = []

    def create_instance(self, *, zone: str, body: object) -> object:
        self.created_instances.append((zone, body))
        return SimpleNamespace(name=f"op-{body.name}", status="PENDING", target_link=None)

    def create_instance_template(self, *, template_name: str, body: object) -> object:
        self.created_templates.append((template_name, body))
        return SimpleNamespace(name=f"template-op-{template_name}", status="PENDING", target_link=None)

    def create_regional_mig(
        self,
        *,
        region: str,
        mig_name: str,
        body: object,
    ) -> object:
        self.created_migs.append((region, mig_name, body))
        return SimpleNamespace(name=f"mig-op-{mig_name}", status="PENDING", target_link=None)

    def get_image_from_family(self, *, image_project: str, family: str) -> object:
        return SimpleNamespace(
            self_link=f"projects/{image_project}/global/images/family/{family}",
            name=family,
        )


def _config(**overrides: object) -> GCPProviderConfig:
    payload: dict[str, object] = {
        "project_id": "orb-example-12345",
        "region": "us-central1",
        "zones": ["us-central1-a", "us-central1-b"],
    }
    payload.update(overrides)
    return GCPProviderConfig(**payload)


def test_handler_factory_creates_singlevm_and_mig_handlers() -> None:
    factory = GCPHandlerFactory(
        compute_client=_ComputeClientStub(),
        config=_config(),
        logger=MagicMock(),
    )

    single_vm_handler = factory.create_handler("SingleVM")
    mig_handler = factory.create_handler("MIG")

    assert isinstance(single_vm_handler, GCPSingleVMHandler)
    assert isinstance(mig_handler, GCPManagedInstanceGroupHandler)


def test_single_vm_handler_acquire_hosts_submits_instance_creation() -> None:
    compute_client = _ComputeClientStub()
    handler = GCPSingleVMHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-single",
        machine_count=1,
        provider_type="gcp",
    )
    template = GCPTemplate.model_validate(
        {
            "template_id": "gcp-single",
            "provider_type": "gcp",
            "provider_api": "SingleVM",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a"],
            "instance_type": "e2-standard-4",
            "max_instances": 1,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    result = handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 1
    assert result["provider_data"]["zone"] == "us-central1-a"
    assert compute_client.created_instances[0][0] == "us-central1-a"


def test_mig_handler_acquire_hosts_submits_template_and_group() -> None:
    compute_client = _ComputeClientStub()
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-mig",
        machine_count=3,
        provider_type="gcp",
    )
    template = GCPTemplate.model_validate(
        {
            "template_id": "gcp-mig",
            "provider_type": "gcp",
            "provider_api": "MIG",
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": ["us-central1-a", "us-central1-b"],
            "mig_scope": "regional",
            "instance_type": "e2-standard-4",
            "max_instances": 3,
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
    )

    result = handler.acquire_hosts(request, template)

    assert len(result["resource_ids"]) == 1
    assert result["provider_data"]["target_size"] == 3
    assert len(compute_client.created_templates) == 1
    assert len(compute_client.created_migs) == 1


@pytest.mark.asyncio
async def test_strategy_create_instances_delegates_to_handler() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.acquire_hosts.return_value = {
        "resource_ids": ["mig-demo"],
        "instances": [],
        "provider_data": {"scope": "regional"},
    }
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 2,
                "template_config": {
                    "template_id": "gcp-mig",
                    "provider_type": "gcp",
                    "provider_api": "MIG",
                    "project_id": "orb-example-12345",
                    "region": "us-central1",
                    "zones": ["us-central1-a", "us-central1-b"],
                    "mig_scope": "regional",
                    "instance_type": "e2-standard-4",
                    "source_image_family": "debian-12",
                    "source_image_project": "debian-cloud",
                },
            },
        )
    )

    assert result.success is True
    assert result.data["resource_ids"] == ["mig-demo"]
    assert result.metadata["provider_data"] == {"scope": "regional"}


@pytest.mark.asyncio
async def test_strategy_create_singlevm_rejects_missing_zone() -> None:
    strategy = GCPProviderStrategy(config=_config(zones=[]), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 1,
                "template_config": {
                    "template_id": "gcp-single",
                    "provider_type": "gcp",
                    "provider_api": "SingleVM",
                    "project_id": "orb-example-12345",
                    "region": "us-central1",
                    "instance_type": "e2-standard-4",
                    "source_image_family": "debian-12",
                    "source_image_project": "debian-cloud",
                },
            },
        )
    )

    assert result.success is False
    assert "SingleVM templates require exactly one explicit zone" in result.error_message


@pytest.mark.asyncio
async def test_strategy_resolve_image_uses_compute_client() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True
    strategy._compute_client = _ComputeClientStub()

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.RESOLVE_IMAGE,
            parameters={
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
            },
        )
    )

    assert result.success is True
    assert result.data["resolved_images"]["image_id"].endswith("/debian-12")
