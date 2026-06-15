"""Runtime tests for the GCP provider infrastructure and strategy."""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as google_exceptions

from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestType
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.base.strategy.provider_strategy import ProviderResult
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.exceptions import (
    GCPEntityNotFoundError,
    GCPNetworkError,
    GCPRateLimitError,
    GCPValidationError,
)
from orb.providers.gcp.infrastructure.gcp_handler_factory import GCPHandlerFactory
from orb.providers.gcp.infrastructure.handlers.mig_handler import GCPManagedInstanceGroupHandler
from orb.providers.gcp.infrastructure.handlers.single_vm_handler import GCPSingleVMHandler
from orb.providers.gcp.services.provisioning_service import GCPProvisioningService
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy
from orb.providers.gcp.types import (
    GCPCreateOperationContext,
    GCPCreateOutcome,
    GCPFailedOperation,
    GCPInstanceRecord,
    GCPMutationOutcome,
)


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
        self.fail_get_instance_for: set[str] = set()
        self.fail_create_regional_mig = False
        self.fail_regional_mig_operation = False
        self.timeout_regional_mig_operation = False
        self.template_operation_result_called = False
        self.mig_operation_result_called = False
        self.deleted_templates: list[str] = []
        self.instances: dict[str, GCPInstanceRecord] = {}

    class _OperationStub(SimpleNamespace):
        def __init__(
            self,
            owner: _ComputeClientStub,
            *,
            result_flag: str,
            result_failure: Exception | None = None,
            **kwargs,
        ) -> None:
            super().__init__(**kwargs)
            self._owner = owner
            self._result_flag = result_flag
            self._result_failure = result_failure

        def result(self, timeout: float | None = None) -> _ComputeClientStub._OperationStub:
            _ = timeout
            setattr(self._owner, self._result_flag, True)
            if self._result_failure is not None:
                raise self._result_failure
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
            result_flag="template_operation_result_called",
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
        if self.fail_create_regional_mig:
            raise RuntimeError("regional mig create failed")
        self.created_migs.append((region, mig_name, body))
        result_failure: Exception | None = None
        if self.fail_regional_mig_operation:
            result_failure = RuntimeError("regional mig operation failed")
        if self.timeout_regional_mig_operation:
            result_failure = FutureTimeoutError("regional mig operation timed out")
        return self._OperationStub(
            self,
            result_flag="mig_operation_result_called",
            result_failure=result_failure,
            name=f"mig-op-{mig_name}",
            status="PENDING",
            target_link=None,
        )

    def delete_instance_template(self, *, template_name: str) -> object:
        self.deleted_templates.append(template_name)
        return SimpleNamespace(name=f"delete-template-{template_name}")

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

    def get_instance(self, *, zone: str, instance_name: str) -> GCPInstanceRecord:
        _ = zone
        if instance_name in self.fail_get_instance_for:
            raise google_exceptions.NotFound("instance was not found")
        return self.instances[instance_name]


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


def test_single_vm_handler_status_normalizes_compute_instance_record() -> None:
    compute_client = _ComputeClientStub()
    compute_client.instances["vm-a"] = GCPInstanceRecord(
        name="vm-a",
        status="RUNNING",
        self_link="projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
        instance_id="123456789",
        machine_type="e2-standard-4",
        creation_timestamp="2026-06-02T14:07:18.000Z",
        private_ip="10.128.0.10",
        public_ip="203.0.113.10",
        subnet_id="subnet-a",
        vpc_id="default",
        labels={"orb-template": "gcp-single"},
        provisioning_model="STANDARD",
    )
    handler = GCPSingleVMHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.check_hosts_status(
        resource_ids=["vm-a"],
        instance_ids=[],
        context={"zone": "us-central1-a"},
    )

    assert result == [
        {
            "instance_id": "vm-a",
            "name": "vm-a",
            "status": "running",
            "private_ip": "10.128.0.10",
            "public_ip": "203.0.113.10",
            "launch_time": "2026-06-02T14:07:18.000Z",
            "instance_type": "e2-standard-4",
            "subnet_id": "subnet-a",
            "vpc_id": "default",
            "tags": {"orb-template": "gcp-single"},
            "price_type": "ondemand",
            "provider_data": {
                "cloud_host_id": "123456789",
                "resource_id": "projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
                "zone": "us-central1-a",
                "subnet_id": "subnet-a",
                "vpc_id": "default",
            },
        }
    ]

def test_single_vm_handler_status_omits_missing_instances() -> None:
    compute_client = _ComputeClientStub()
    compute_client.fail_get_instance_for = {"vm-deleted"}
    handler = GCPSingleVMHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.check_hosts_status(
        resource_ids=["vm-deleted"],
        instance_ids=[],
        context={"zone": "us-central1-a"},
    )

    assert result == []


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
    assert result.provider_data["operation_status"] == "completed"
    assert len(compute_client.created_templates) == 1
    assert compute_client.template_operation_result_called is True
    assert compute_client.mig_operation_result_called is True
    assert len(compute_client.created_migs) == 1


def test_mig_handler_acquire_hosts_times_out_waiting_for_template_operation() -> None:
    compute_client = _ComputeClientStub()

    class _TimeoutOperationStub(compute_client._OperationStub):
        def result(self, timeout: float | None = None) -> object:
            self._owner.template_operation_result_called = True
            raise FutureTimeoutError(f"timed out after {timeout}")

    def _create_timeout_template(*, template_name: str, body: object) -> object:
        compute_client.created_templates.append((template_name, body))
        return _TimeoutOperationStub(
            compute_client,
            result_flag="template_operation_result_called",
            name=f"template-op-{template_name}",
            status="PENDING",
            target_link=None,
        )

    compute_client.create_instance_template = _create_timeout_template  # type: ignore[method-assign]
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(connect_timeout=7, read_timeout=11, max_retries=2),
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

    with pytest.raises(
        GCPNetworkError,
        match="Timed out waiting for GCP instance template creation to finish",
    ) as exc_info:
        handler.acquire_hosts(request, template)

    assert exc_info.value.details == {
        "operation": "create_instance_template",
        "template_name": compute_client.created_templates[0][0],
        "operation_name": f"template-op-{compute_client.created_templates[0][0]}",
        "timeout_seconds": 36,
    }
    assert compute_client.template_operation_result_called is True
    assert compute_client.created_migs == []


def test_mig_handler_rolls_back_instance_template_when_mig_create_fails() -> None:
    compute_client = _ComputeClientStub()
    compute_client.fail_create_regional_mig = True
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

    with pytest.raises(RuntimeError, match="regional mig create failed"):
        handler.acquire_hosts(request, template)

    assert compute_client.deleted_templates == [compute_client.created_templates[0][0]]


def test_mig_handler_rolls_back_instance_template_when_mig_operation_fails() -> None:
    compute_client = _ComputeClientStub()
    compute_client.fail_regional_mig_operation = True
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

    with pytest.raises(RuntimeError, match="regional mig operation failed"):
        handler.acquire_hosts(request, template)

    assert compute_client.mig_operation_result_called is True
    assert compute_client.deleted_templates == [compute_client.created_templates[0][0]]


def test_mig_handler_does_not_roll_back_template_when_mig_operation_times_out() -> None:
    compute_client = _ComputeClientStub()
    compute_client.timeout_regional_mig_operation = True
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(connect_timeout=7, read_timeout=11, max_retries=2),
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

    with pytest.raises(
        GCPNetworkError,
        match="Timed out waiting for GCP managed instance group creation to finish",
    ) as exc_info:
        handler.acquire_hosts(request, template)

    assert compute_client.mig_operation_result_called is True
    assert compute_client.deleted_templates == []
    assert exc_info.value.details["operation"] == "create_mig"
    assert exc_info.value.details["timeout_seconds"] == 36


def test_provisioning_service_projects_failed_operations_into_fleet_errors() -> None:
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
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-single",
        machine_count=1,
        provider_type="gcp",
    )
    context = GCPCreateOperationContext(
        template=template,
        request=request,
        handler=MagicMock(),
        count=1,
    )
    result = GCPProvisioningService.build_provider_result(
        context=context,
        outcome=GCPCreateOutcome(
            resource_ids=[],
            instances=[],
            provider_data={"submitted_count": 0},
            failed_operations=[
                GCPFailedOperation(
                    target_id="vm-a",
                    error_code="GCPQuotaExceededError",
                    error_message="Quota exceeded",
                    operation="create_instance",
                )
            ],
        ),
    )

    assert result.success is True
    assert result.metadata["provider_data"]["fleet_errors"] == [
        {
            "instance_id": "vm-a",
            "error_code": "GCPQuotaExceededError",
            "error_message": "Quota exceeded",
        }
    ]


def test_provisioning_service_single_vm_create_result_waits_for_status_sync() -> None:
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
    request = Request.create_new_request(
        request_type=RequestType.ACQUIRE,
        template_id="gcp-single",
        machine_count=1,
        provider_type="gcp",
    )
    context = GCPCreateOperationContext(
        template=template,
        request=request,
        handler=MagicMock(),
        count=1,
    )

    result = GCPProvisioningService.build_provider_result(
        context=context,
        outcome=GCPCreateOutcome(
            resource_ids=["vm-a"],
            instances=[
                {
                    "instance_id": "vm-a",
                    "status": "PROVISIONING",
                    "provider_data": {"zone": "us-central1-a", "operation_name": "op-vm-a"},
                }
            ],
            provider_data={
                "zone": "us-central1-a",
                "requested_count": 1,
                "submitted_count": 1,
                "operation_status": "submitted",
            },
        ),
    )

    assert result.data["resource_ids"] == ["vm-a"]
    assert result.data["instance_ids"] == ["vm-a"]
    assert result.data["instances"] == []
    assert result.data["results"] == {"vm-a": True}
    assert result.metadata["provider_data"]["operation_status"] == "submitted"


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
    assert result.successful_ids == []
    assert result.warning is not None
    assert "completion must be confirmed" in result.warning
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
    assert [item["status"] for item in result] == ["running", "launching"]
    assert [item["provider_data"]["resource_id"] for item in result] == ["mig-a", "mig-b"]
    assert result[0]["provider_data"]["gcp_instance_status"] == "RUNNING"
    assert result[1]["provider_data"]["gcp_current_action"] == "CREATING"


def test_mig_handler_status_uses_current_action_when_instance_status_missing() -> None:
    compute_client = _ComputeClientStub()
    compute_client.regional_managed_instances = {
        "mig-a": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
                instance_status=None,
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
        resource_ids=["mig-a"],
        instance_ids=[],
        context={"region": "us-central1", "scope": "regional"},
    )

    assert result[0]["status"] == "pending"


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
        context={
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "scope": "regional",
            "instance_template_name": "orb-template-a",
        },
    )

    assert result.attempted_ids == ["vm-a", "vm-b"]
    assert result.successful_ids == []
    assert result.warning is not None
    assert "completion must be confirmed" in result.warning
    assert compute_client.deleted_regional_migs == [
        ("us-central1", "mig-a"),
        ("us-central1", "mig-b"),
    ]
    assert compute_client.deleted_regional_managed_instances == []
    assert compute_client.deleted_templates == ["orb-template-a"]


def test_mig_handler_terminates_subset_with_delete_managed_instances() -> None:
    compute_client = _ComputeClientStub()
    compute_client.regional_managed_instances = {
        "mig-a": [
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-a/instances/vm-a",
                instance_status="RUNNING",
                current_action="NONE",
            ),
            SimpleNamespace(
                instance_url="projects/orb-example-12345/zones/us-central1-b/instances/vm-b",
                instance_status="RUNNING",
                current_action="NONE",
            ),
        ],
    }
    handler = GCPManagedInstanceGroupHandler(
        compute_client=compute_client,
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.terminate_hosts(
        resource_ids=["mig-a"],
        instance_ids=["vm-a"],
        context={
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "scope": "regional",
            "instance_template_name": "orb-template-a",
        },
    )

    assert result.attempted_ids == ["vm-a"]
    assert result.successful_ids == []
    assert compute_client.deleted_regional_migs == []
    assert compute_client.deleted_regional_managed_instances == [
        (
            "us-central1",
            "mig-a",
            ["projects/orb-example-12345/zones/us-central1-a/instances/vm-a"],
        )
    ]
    assert compute_client.deleted_templates == []


def test_mig_handler_status_treats_missing_mig_as_empty() -> None:
    class _MissingMigComputeClient(_ComputeClientStub):
        def list_regional_managed_instances(
            self,
            *,
            region: str,
            mig_name: str,
            instance_filter: str | None = None,
        ) -> list[object]:
            _ = region, mig_name, instance_filter
            raise google_exceptions.NotFound("managed instance group was not found")

    handler = GCPManagedInstanceGroupHandler(
        compute_client=_MissingMigComputeClient(),
        config=_config(),
        logger=MagicMock(),
    )

    result = handler.check_hosts_status(
        resource_ids=["mig-a"],
        instance_ids=["vm-a"],
        context={"region": "us-central1", "scope": "regional"},
    )

    assert result == []


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
    assert result.metadata["provider_data"] == {
        "scope": "regional",
        "fulfillment_final": True,
    }


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
    assert result.metadata["provider_data"]["fulfillment_final"] is True
    assert is_dry_run_active() is False


@pytest.mark.asyncio
async def test_strategy_execute_operation_preserves_dry_run_context_inside_to_thread() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    observed: dict[str, bool] = {}

    def fake_execute(operation):
        _ = operation
        observed["dry_run_active"] = is_dry_run_active()
        return ProviderResult.success_result({})

    strategy._execute_operation_internal_sync = fake_execute

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.HEALTH_CHECK,
            parameters={},
            context={"dry_run": True},
        )
    )

    assert result.success is True
    assert observed["dry_run_active"] is True
    assert result.metadata["dry_run"] is True
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
    assert result.data["successful_count"] == 0
    assert result.data["successful_ids"] == []
    assert result.data["results"] == {"mig-a": False, "mig-b": False}
    assert "completion must be confirmed" in result.data["warning"]
    assert "completion must be confirmed" in result.metadata["provider_data"]["warning"]


@pytest.mark.asyncio
async def test_strategy_terminate_single_vm_derives_zone_from_instance_resource_id() -> None:
    strategy = GCPProviderStrategy(
        config=_config(zones=["us-east1-b"]),
        logger=MagicMock(),
        provider_name="gcp-default",
    )
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.terminate_hosts.return_value = GCPMutationOutcome(
        attempted_ids=["gcp-gcp-single-12345678"],
        successful_ids=["gcp-gcp-single-12345678"],
    )
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "SingleVM",
                "instance_ids": ["gcp-gcp-single-12345678"],
                "resource_id": (
                    "projects/orb-example-12345/zones/us-central1-a/"
                    "instances/gcp-gcp-single-12345678"
                ),
            },
        )
    )

    assert result.success is True
    call = handler.terminate_hosts.call_args.kwargs
    assert call["context"]["zone"] == "us-central1-a"


@pytest.mark.asyncio
async def test_strategy_terminate_mig_uses_resource_mapping() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    handler = MagicMock()
    handler.terminate_hosts.return_value = GCPMutationOutcome(
        attempted_ids=["vm-a"],
        operations=[{"operation_name": "delete-instances-mig-a", "mig_name": "mig-a"}],
        warning="Delete operation submitted to GCP; completion must be confirmed by later polling.",
    )
    strategy._handler_factory = SimpleNamespace(create_handler=lambda _api: handler)

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "instance_ids": ["vm-a"],
                "resource_mapping": {"vm-a": ("mig-a", 1)},
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
        )
    )

    assert result.success is True
    call = handler.terminate_hosts.call_args.kwargs
    assert call["resource_ids"] == ["mig-a"]
    assert call["instance_ids"] == ["vm-a"]
    assert call["context"]["mig_name"] == "mig-a"


@pytest.mark.asyncio
async def test_strategy_terminate_mig_requires_resource_mapping_for_instance_ids() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "instance_ids": ["vm-a"],
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPValidationError"
    assert "resource_mapping or resource_ids" in result.error_message


@pytest.mark.asyncio
async def test_strategy_terminate_mig_rejects_incomplete_resource_mapping() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "instance_ids": ["vm-a", "vm-b"],
                "resource_mapping": {"vm-a": ("mig-a", 1)},
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPValidationError"
    assert "resource_mapping is missing instance 'vm-b'" in result.error_message


@pytest.mark.asyncio
async def test_strategy_terminate_mig_rejects_empty_resource_mapping_resource_id() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.TERMINATE_INSTANCES,
            parameters={
                "provider_api": "MIG",
                "instance_ids": ["vm-a"],
                "resource_mapping": {"vm-a": ("", 1)},
                "request_metadata": {"region": "us-central1", "scope": "regional"},
            },
        )
    )

    assert result.success is False
    assert result.error_code == "GCPValidationError"
    assert "Invalid GCP mutation operation parameters" in result.error_message


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


def test_strategy_reuses_operation_context_service_until_handler_factory_changes() -> None:
    strategy = GCPProviderStrategy(config=_config(), logger=MagicMock(), provider_name="gcp-default")
    assert strategy.initialize() is True

    first_service = strategy._get_operation_context_service()
    second_service = strategy._get_operation_context_service()

    assert second_service is first_service

    replacement_factory = GCPHandlerFactory(
        compute_client=_ComputeClientStub(),
        config=_config(),
        logger=MagicMock(),
    )
    strategy._handler_factory = replacement_factory

    refreshed_service = strategy._get_operation_context_service()

    assert refreshed_service is not first_service
    assert refreshed_service.handler_factory is replacement_factory


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
