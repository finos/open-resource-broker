"""Runtime tests for the GCP provider infrastructure and strategy."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as google_exceptions

from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.exceptions import (
    GCPEntityNotFoundError,
    GCPRateLimitError,
    GCPValidationError,
)
from orb.providers.gcp.infrastructure.gcp_handler_factory import GCPHandlerFactory
from orb.providers.gcp.infrastructure.handlers.mig_handler import GCPManagedInstanceGroupHandler
from orb.providers.gcp.infrastructure.handlers.single_vm_handler import GCPSingleVMHandler
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy
from orb.providers.gcp.types import GCPCreateOutcome


class _ComputeClientStub:
    def __init__(self) -> None:
        self.created_instances: list[tuple[str, dict]] = []
        self.created_templates: list[tuple[str, dict]] = []
        self.created_migs: list[tuple[str, str, dict]] = []
        self.deleted_regional_migs: list[tuple[str, str]] = []
        self.deleted_regional_managed_instances: list[tuple[str, str, list[str]]] = []
        self.regional_managed_instances: dict[str, list[object]] = {}
        self.fail_create_instance_for: set[str] = set()
        self.fail_start_instance_for: set[str] = set()
        self.fail_stop_instance_for: set[str] = set()
        self.fail_delete_instance_for: set[str] = set()
        self.template_operation_result_called = False

    class _OperationStub(SimpleNamespace):
        def __init__(self, owner: "_ComputeClientStub", **kwargs) -> None:
            super().__init__(**kwargs)
            self._owner = owner

        def result(self) -> "_ComputeClientStub._OperationStub":
            self._owner.template_operation_result_called = True
            return self

    def create_instance(self, *, zone: str, body: object) -> object:
        if body.name in self.fail_create_instance_for:
            raise google_exceptions.ResourceExhausted("quota exhausted")
        self.created_instances.append((zone, body))
        return SimpleNamespace(name=f"op-{body.name}", status="PENDING", target_link=None)

    def create_instance_template(self, *, template_name: str, body: object) -> object:
        self.created_templates.append((template_name, body))
        return self._OperationStub(
            self,
            name=f"template-op-{template_name}",
            status="PENDING",
            target_link=None,
        )

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

    def delete_regional_mig(self, *, region: str, mig_name: str) -> object:
        self.deleted_regional_migs.append((region, mig_name))
        return SimpleNamespace(name=f"delete-{mig_name}")

    def delete_regional_managed_instances(
        self,
        *,
        region: str,
        mig_name: str,
        instance_urls: list[str],
    ) -> object:
        self.deleted_regional_managed_instances.append((region, mig_name, instance_urls))
        return SimpleNamespace(name=f"delete-instances-{mig_name}")

    def list_regional_managed_instances(
        self, *, region: str, mig_name: str, instance_filter: str | None = None,
    ) -> list[object]:
        _ = region, instance_filter
        return self.regional_managed_instances.get(mig_name, [])

    def start_instance(self, *, zone: str, instance_name: str) -> object:
        _ = zone
        if instance_name in self.fail_start_instance_for:
            raise google_exceptions.ServiceUnavailable("service unavailable")
        return SimpleNamespace(name=f"start-{instance_name}")

    def stop_instance(self, *, zone: str, instance_name: str) -> object:
        _ = zone
        if instance_name in self.fail_stop_instance_for:
            raise google_exceptions.ServiceUnavailable("service unavailable")
        return SimpleNamespace(name=f"stop-{instance_name}")

    def delete_instance(self, *, zone: str, instance_name: str) -> object:
        _ = zone
        if instance_name in self.fail_delete_instance_for:
            raise google_exceptions.NotFound("instance was not found")
        return SimpleNamespace(name=f"delete-{instance_name}")


def _config(**overrides: object) -> GCPProviderConfig:
    payload: dict[str, object] = {
        "project_id": "orb-example-12345",
        "region": "us-central1",
        "zones": ["us-central1-a", "us-central1-b"],
    }
    payload.update(overrides)
    return GCPProviderConfig(**payload)

def test_handler_factory_rejects_invalid_handler_type_with_gcp_validation_error() -> None:
    factory = GCPHandlerFactory(
        compute_client=_ComputeClientStub(),
        config=_config(),
        logger=MagicMock(),
    )

    with pytest.raises(GCPValidationError, match="Invalid GCP handler type"):
        factory.create_handler("Bogus")


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

    assert len(result.resource_ids) == 1
    assert result.provider_data["zone"] == "us-central1-a"
    assert compute_client.created_instances[0][0] == "us-central1-a"


def test_single_vm_handler_acquire_hosts_tracks_partial_failures() -> None:
    compute_client = _ComputeClientStub()
    logger = MagicMock()
    handler = GCPSingleVMHandler(
        compute_client=compute_client,
        config=_config(),
        logger=logger,
    )
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-single",
        machine_count=2,
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
    failing_name = "gcp-gcp-single-deadbeef"
    compute_client.fail_create_instance_for = {failing_name}
    generated = [
        SimpleNamespace(hex="deadbeefcafebabe"),
        SimpleNamespace(hex="cafebabedeadbeef"),
    ]

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            uuid,
            "uuid4",
            lambda: generated.pop(0) if generated else SimpleNamespace(hex="feedfacecafebeef"),
        )
        result = handler.acquire_hosts(request, template)

    assert result.provider_data["partial_failure"] is True
    assert result.provider_data["submitted_count"] == 1
    assert result.failed_operations[0].target_id == failing_name
    assert result.failed_operations[0].error_code == "GCPQuotaExceededError"
    assert len(result.resource_ids) == 1


def test_single_vm_handler_start_instances_tracks_partial_failures() -> None:
    compute_client = _ComputeClientStub()
    compute_client.fail_start_instance_for = {"vm-b"}
    handler = GCPSingleVMHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.start_instances(
        instance_ids=["vm-a", "vm-b"],
        context={"zone": "us-central1-a"},
    )

    assert result.attempted_ids == ["vm-a", "vm-b"]
    assert result.successful_ids == ["vm-a"]
    assert [(f.target_id, f.error_code, f.error_message, f.operation) for f in result.failed_operations] == [
        ("vm-b", "GCPNetworkError", "503 service unavailable", "start_instance")
    ]


def test_mig_handler_start_instances_returns_failed_results_for_unsupported_targets() -> None:
    compute_client = _ComputeClientStub()
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.start_instances(
        instance_ids=["vm-a", "vm-b"],
        context={"region": "us-central1", "scope": "regional"},
    )

    assert result.attempted_ids == ["vm-a", "vm-b"]
    assert result.successful_ids == []
    assert result.warning is not None
    assert result.warning.startswith("MIG-managed instances follow group policy")


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

    assert len(result.resource_ids) == 1
    assert result.provider_data["target_size"] == 3
    assert len(compute_client.created_templates) == 1
    assert compute_client.template_operation_result_called is True
    assert len(compute_client.created_migs) == 1


def test_mig_handler_terminates_multiple_resource_ids() -> None:
    compute_client = _ComputeClientStub()
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.terminate_hosts(
        resource_ids=["mig-a", "mig-b"],
        instance_ids=[],
        context={"region": "us-central1", "scope": "regional"},
    )

    assert result.attempted_ids == ["mig-a", "mig-b"]
    assert result.successful_ids == ["mig-a", "mig-b"]
    assert compute_client.deleted_regional_migs == [
        ("us-central1", "mig-a"),
        ("us-central1", "mig-b"),
    ]


def test_mig_handler_status_checks_multiple_resource_ids() -> None:
    compute_client = _ComputeClientStub()
    compute_client.regional_managed_instances = {
        "mig-a": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
                instance_status="RUNNING",
                current_action="NONE",
            )
        ],
        "mig-b": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-b/instances/vm-b",
                instance_status="STAGING",
                current_action="CREATING",
            )
        ],
    }
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.check_hosts_status(
        resource_ids=["mig-a", "mig-b"],
        instance_ids=[],
        context={"region": "us-central1", "scope": "regional"},
    )

    assert [item["instance_id"] for item in result] == ["vm-a", "vm-b"]
    assert [item["provider_data"]["resource_id"] for item in result] == ["mig-a", "mig-b"]


def test_mig_handler_terminates_instances_across_multiple_resource_ids() -> None:
    compute_client = _ComputeClientStub()
    compute_client.regional_managed_instances = {
        "mig-a": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
                instance_status="RUNNING",
                current_action="NONE",
            )
        ],
        "mig-b": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-b/instances/vm-b",
                instance_status="RUNNING",
                current_action="NONE",
            )
        ],
    }
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.terminate_hosts(
        resource_ids=["mig-a", "mig-b"],
        instance_ids=["vm-a", "vm-b"],
        context={"project_id": "orb-example-12345", "region": "us-central1", "scope": "regional"},
    )

    assert result.attempted_ids == ["vm-a", "vm-b"]
    assert result.successful_ids == ["vm-a", "vm-b"]
    assert compute_client.deleted_regional_managed_instances == [
        (
            "us-central1",
            "mig-a",
            ["projects/orb-example-12345/zones/us-central1-a/instances/vm-a"],
        ),
        (
            "us-central1",
            "mig-b",
            ["projects/orb-example-12345/zones/us-central1-b/instances/vm-b"],
        ),
    ]


@pytest.mark.asyncio
async def test_strategy_create_instances_delegates_to_handler() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.acquire_hosts.return_value = GCPCreateOutcome(
        resource_ids=["mig-demo"],
        instances=[],
        provider_data={"scope": "regional"},
    )
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
async def test_strategy_create_instances_dry_run_short_circuits_handler_calls() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.acquire_hosts.side_effect = AssertionError("dry-run should not reach acquire_hosts")
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
            context={"dry_run": True},
        )
    )

    assert result.success is True
    assert result.metadata["dry_run"] is True
    assert result.metadata["method"] == "dry_run"
    assert result.data["provider_api"] == "MIG"
    assert result.data["resource_ids"] == ["dry-run-gcp-mig"]
    assert result.metadata["provider_data"]["dry_run"] is True
    assert is_dry_run_active() is False


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
    assert result.error_code == "GCPValidationError"
    assert "SingleVM templates require exactly one explicit zone" in result.error_message


@pytest.mark.asyncio
async def test_strategy_preserves_direct_gcp_errors() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.acquire_hosts.side_effect = GCPRateLimitError("rate limit exceeded", details={"quota": "api"})
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

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
                    "zones": ["us-central1-a"],
                    "instance_type": "e2-standard-4",
                    "source_image_family": "debian-12",
                    "source_image_project": "debian-cloud",
                },
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPRateLimitError"
    assert result.metadata["details"] == {"quota": "api"}


@pytest.mark.asyncio
async def test_strategy_translates_not_found_failures_to_gcp_entity_errors() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.check_hosts_status.side_effect = google_exceptions.NotFound("instance was not found")
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "provider_api": "SingleVM",
                "resource_ids": ["vm-1"],
                "request_metadata": {"zone": "us-central1-a"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPEntityNotFoundError"
    assert result.metadata["details"]["operation"] == "get_instance_status"
    assert result.metadata["details"]["source_error_type"] == "NotFound"


@pytest.mark.asyncio
async def test_strategy_translates_resource_exhausted_to_gcp_quota_error() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.check_hosts_status.side_effect = google_exceptions.ResourceExhausted("quota exhausted")
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "provider_api": "SingleVM",
                "resource_ids": ["vm-1"],
                "request_metadata": {"zone": "us-central1-a"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPQuotaExceededError"


@pytest.mark.asyncio
async def test_strategy_translates_service_unavailable_to_gcp_network_error() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.check_hosts_status.side_effect = google_exceptions.ServiceUnavailable("service unavailable")
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "provider_api": "SingleVM",
                "resource_ids": ["vm-1"],
                "request_metadata": {"zone": "us-central1-a"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPNetworkError"


@pytest.mark.asyncio
async def test_strategy_terminate_instances_supports_multiple_mig_resource_ids() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True
    strategy._compute_client = _ComputeClientStub()
    strategy._handler_factory = GCPHandlerFactory(
        compute_client=strategy._compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "resource_ids": ["mig-a", "mig-b"],
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
        )
    )

    assert result.success is True
    assert result.data["successful_count"] == 2
    assert result.data["successful_ids"] == ["mig-a", "mig-b"]


@pytest.mark.asyncio
async def test_strategy_terminate_instances_dry_run_short_circuits_handler_calls() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.terminate_hosts.side_effect = AssertionError("dry-run should not reach terminate_hosts")
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "resource_ids": ["mig-a", "mig-b"],
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
            context={"dry_run": True},
        )
    )

    assert result.success is True
    assert result.metadata["dry_run"] is True
    assert result.metadata["method"] == "dry_run"
    assert result.data["successful_ids"] == ["mig-a", "mig-b"]
    assert result.data["results"] == {"mig-a": True, "mig-b": True}


@pytest.mark.asyncio
async def test_strategy_start_instances_surfaces_partial_results() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True
    strategy._compute_client = _ComputeClientStub()
    strategy._handler_factory = GCPHandlerFactory(
        compute_client=strategy._compute_client,
        config=_config(),
        logger=MagicMock(),
    )
    strategy._compute_client.fail_start_instance_for = {"vm-b"}

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.START_INSTANCES,
            parameters={
                "provider_api": "SingleVM",
                "instance_ids": ["vm-a", "vm-b"],
                "request_metadata": {"zone": "us-central1-a"},
            },
        )
    )

    assert result.success is True
    assert result.data["results"] == {"vm-a": True, "vm-b": False}
    assert result.metadata["partial_failure"] is True


@pytest.mark.asyncio
async def test_strategy_get_instance_status_dry_run_short_circuits_handler_calls() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.check_hosts_status.side_effect = AssertionError(
        "dry-run should not reach check_hosts_status"
    )
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.GET_INSTANCE_STATUS,
            parameters={
                "provider_api": "SingleVM",
                "instance_ids": ["vm-a", "vm-b"],
                "request_metadata": {"zone": "us-central1-a"},
            },
            context={"dry_run": True},
        )
    )

    assert result.success is True
    assert result.metadata["dry_run"] is True
    assert result.metadata["method"] == "dry_run"
    assert result.data["instances"] == [
        {"instance_id": "vm-a", "status": "DRY_RUN", "provider_data": {"dry_run": True}},
        {"instance_id": "vm-b", "status": "DRY_RUN", "provider_data": {"dry_run": True}},
    ]


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


@pytest.mark.asyncio
async def test_strategy_resolve_image_dry_run_short_circuits_compute_client() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    compute_client = MagicMock()
    compute_client.get_image_from_family.side_effect = AssertionError(
        "dry-run should not reach get_image_from_family"
    )
    strategy._compute_client = compute_client

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.RESOLVE_IMAGE,
            parameters={
                "source_image_family": "debian-12",
                "source_image_project": "debian-cloud",
            },
            context={"dry_run": True},
        )
    )

    assert result.success is True
    assert result.metadata["dry_run"] is True
    assert result.metadata["method"] == "dry_run"
    assert result.data["resolved_images"]["dry_run"] is True
    assert result.data["resolved_images"]["image_id"].endswith("/debian-12")


def test_health_check_reports_healthy_in_dry_run_mode() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        mp.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        mp.delenv("GCP_PROJECT", raising=False)

        from orb.providers.gcp.infrastructure.dry_run_adapter import gcp_dry_run_context

        with gcp_dry_run_context():
            status = strategy.check_health()

    assert status.is_healthy is True
    assert "DRY-RUN" in status.status_message


def test_mig_handler_missing_membership_raises_gcp_entity_not_found() -> None:
    compute_client = _ComputeClientStub()
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    with pytest.raises(GCPEntityNotFoundError, match="Could not resolve MIG membership"):
        handler.terminate_hosts(
            resource_ids=["mig-a"],
            instance_ids=["vm-missing"],
            context={"project_id": "orb-example-12345", "region": "us-central1", "scope": "regional"},
        )
