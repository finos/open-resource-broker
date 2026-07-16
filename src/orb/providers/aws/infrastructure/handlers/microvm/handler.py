"""AWS Lambda MicroVM Handler.

Provisions isolated MicroVM sandboxes via the AWS Lambda MicroVMs API.
Each MicroVM is an independent Firecracker-based execution environment
with its own endpoint URL, lifecycle, and state.

Unlike fleet-based handlers, MicroVMs are individual resources — requesting
N machines means N separate run_microvm API calls, executed in parallel.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from uuid import uuid4

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.provider_fulfilment import CheckHostsStatusResult, ProviderFulfilment
from orb.domain.request.aggregate import Request
from orb.domain.template.template_aggregate import Template
from orb.infrastructure.adapters.ports.request_adapter_port import RequestAdapterPort
from orb.infrastructure.di.injectable import injectable
from orb.infrastructure.error.decorators import handle_infrastructure_exceptions
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.exceptions.aws_exceptions import (
    AWSInfrastructureError,
    AWSValidationError,
)
from orb.providers.aws.infrastructure.adapters.machine_adapter import AWSMachineAdapter
from orb.providers.aws.infrastructure.aws_client import AWSClient
from orb.providers.aws.infrastructure.handlers.base_handler import AWSHandler
from orb.providers.aws.infrastructure.handlers.microvm.example_templates import (
    MICROVM_EXAMPLE_TEMPLATES,
)
from orb.providers.aws.infrastructure.launch_template.manager import AWSLaunchTemplateManager
from orb.providers.aws.infrastructure.tags import build_system_tags
from orb.providers.aws.utilities.aws_operations import AWSOperations

# MicroVM state → ORB status mapping.
# SUSPENDING/SUSPENDED map to "running" because ORB assumes MicroVMs operate in
# PULL mode (polling SQS, Kafka, etc.) rather than receiving inbound traffic.
# The platform's suspend/resume mechanism is based on inbound HTTP traffic — it
# doesn't apply to pull-based workloads. MicroVMs in suspended state are still
# considered available from ORB's perspective since they resume transparently.
_MICROVM_STATE_MAP = {
    "PENDING": "pending",
    "RUNNING": "running",
    "SUSPENDING": "running",
    "SUSPENDED": "running",
    "TERMINATING": "shutting-down",
    "TERMINATED": "terminated",
}

_MAX_WORKERS = 25


@injectable
class MicroVMHandler(AWSHandler):
    """Handler for AWS Lambda MicroVM operations."""

    def __init__(
        self,
        aws_client: AWSClient,
        logger: LoggingPort,
        aws_ops: AWSOperations,
        launch_template_manager: AWSLaunchTemplateManager,
        request_adapter: Optional[RequestAdapterPort] = None,
        machine_adapter: Optional[AWSMachineAdapter] = None,
        aws_native_spec_service=None,
        config_port: Optional[ConfigurationPort] = None,
    ) -> None:
        super().__init__(
            aws_client,
            logger,
            aws_ops,
            launch_template_manager,
            request_adapter,
            machine_adapter,
            aws_native_spec_service=aws_native_spec_service,
            config_port=config_port,
        )

    def _validate_prerequisites(self, template: AWSTemplate) -> None:
        """MicroVMs require only an image ARN — skip EC2-specific validation."""
        if not template.image_id:
            raise AWSValidationError(
                "image_id is required for MicroVM templates (MicroVM image ARN)"
            )

    def _default_provider_api(self) -> str:
        return "MicroVM"

    # ------------------------------------------------------------------
    # Acquire
    # ------------------------------------------------------------------

    @handle_infrastructure_exceptions(context="microvm_creation")
    def _acquire_hosts_internal(
        self, request: Request, aws_template: AWSTemplate
    ) -> dict[str, Any]:
        """Launch N MicroVMs in parallel."""
        try:
            params = self._build_run_params(aws_template)
            count = request.requested_count

            self._logger.info(
                "Launching %d MicroVM(s) from image %s",
                count,
                aws_template.image_id,
            )

            results = self._run_microvms_parallel(params, count)

            microvm_ids = [r["microvmId"] for r in results]
            instances = [self._build_machine_payload(r) for r in results]

            # Tag each MicroVM with ORB system tags
            self._tag_microvms(microvm_ids, request, aws_template)

            self._logger.info(
                "Successfully launched %d MicroVM(s): %s", len(microvm_ids), microvm_ids
            )

            return {
                "success": True,
                "resource_ids": microvm_ids,
                "instances": instances,
                "provider_data": {
                    "resource_type": "microvm",
                    "microvm_ids": microvm_ids,
                    "requires_async_polling": True,
                },
            }
        except Exception as e:
            self._logger.error("MicroVM provisioning failed: %s", e)
            return {
                "success": False,
                "resource_ids": [],
                "instances": [],
                "error_message": str(e),
            }

    def _build_run_params(self, aws_template: AWSTemplate) -> dict[str, Any]:
        """Build run_microvm API kwargs from template fields and metadata."""
        metadata = aws_template.metadata or {}
        params: dict[str, Any] = {"imageIdentifier": aws_template.image_id}

        if metadata.get("image_version"):
            params["imageVersion"] = metadata["image_version"]
        if metadata.get("execution_role_arn"):
            params["executionRoleArn"] = metadata["execution_role_arn"]
        if metadata.get("idle_policy"):
            params["idlePolicy"] = metadata["idle_policy"]
        if metadata.get("maximum_duration_in_seconds"):
            params["maximumDurationInSeconds"] = metadata["maximum_duration_in_seconds"]
        if metadata.get("ingress_network_connectors"):
            params["ingressNetworkConnectors"] = metadata["ingress_network_connectors"]
        if metadata.get("egress_network_connectors"):
            params["egressNetworkConnectors"] = metadata["egress_network_connectors"]
        if metadata.get("run_hook_payload"):
            params["runHookPayload"] = metadata["run_hook_payload"]
        if metadata.get("logging"):
            params["logging"] = metadata["logging"]

        return params

    def _run_microvms_parallel(
        self, base_params: dict[str, Any], count: int
    ) -> list[dict[str, Any]]:
        """Execute run_microvm N times in parallel with idempotency tokens."""
        results: list[dict[str, Any]] = []
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {}
            for i in range(count):
                params = {**base_params, "clientToken": str(uuid4())}
                future = executor.submit(self._run_single_microvm, params)
                futures[future] = i

            for future in as_completed(futures):
                idx = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self._logger.error("MicroVM launch %d failed: %s", idx, e)
                    errors.append(f"Launch {idx}: {e}")

        if not results:
            raise AWSInfrastructureError(
                f"All {count} MicroVM launches failed: {'; '.join(errors)}"
            )

        if errors:
            self._logger.warning(
                "%d/%d MicroVM launches failed: %s", len(errors), count, errors
            )

        return results

    def _run_single_microvm(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a single run_microvm call with retry and jitter for throttling."""
        import random

        max_attempts = 6
        base_delay = 1.0

        for attempt in range(max_attempts):
            try:
                return self.aws_client.microvm_client.run_microvm(**params)
            except Exception as e:
                is_throttle = "ThrottlingException" in str(type(e).__name__) or "Throttling" in str(e)
                if attempt == max_attempts - 1 or not is_throttle:
                    raise
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                self._logger.debug("Throttled on run_microvm, retrying in %.1fs", delay)
                import time
                time.sleep(delay)

    def _tag_microvms(
        self,
        microvm_ids: list[str],
        request: Request,
        aws_template: AWSTemplate,
    ) -> None:
        """Apply standard ORB tags to MicroVMs in parallel."""
        tags = build_system_tags(
            request_id=str(request.request_id),
            template_id=str(aws_template.template_id),
            provider_api="MicroVM",
        )

        tag_dict = {t["Key"]: t["Value"] for t in tags}

        def _tag_one(microvm_id: str) -> None:
            self._retry_with_backoff(
                self.aws_client.microvm_client.tag_resource,
                operation_type="standard",
                resourceArn=microvm_id,
                tags=tag_dict,
            )

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {executor.submit(_tag_one, mid): mid for mid in microvm_ids}
            for future in as_completed(futures):
                mid = futures[future]
                try:
                    future.result()
                except Exception as e:
                    self._logger.warning("Failed to tag MicroVM %s: %s", mid, e)

    # ------------------------------------------------------------------
    # Check Status
    # ------------------------------------------------------------------

    def check_hosts_status(self, request: Request) -> CheckHostsStatusResult:
        """Check the status of MicroVMs for a request."""
        microvm_ids = request.resource_ids
        if not microvm_ids:
            provider_data = getattr(request, "provider_data", None) or {}
            microvm_ids = provider_data.get("microvm_ids", [])

        if not microvm_ids:
            return CheckHostsStatusResult(
                instances=[],
                fulfilment=ProviderFulfilment(
                    state="in_progress",
                    message="No MicroVM IDs yet — waiting for provisioning",
                ),
            )

        instances: list[dict[str, Any]] = []
        for microvm_id in microvm_ids:
            try:
                resp = self._retry_with_backoff(
                    self.aws_client.microvm_client.get_microvm,
                    operation_type="read_only",
                    microvmIdentifier=microvm_id,
                )
                instances.append(self._build_machine_payload(resp))
            except Exception as e:
                self._logger.warning(
                    "Failed to get status for MicroVM %s: %s", microvm_id, e
                )

        fulfilment = self._compute_microvm_fulfilment(instances, request.requested_count)
        return CheckHostsStatusResult(instances=instances, fulfilment=fulfilment)

    def _compute_microvm_fulfilment(
        self, instances: list[dict[str, Any]], requested_count: int
    ) -> ProviderFulfilment:
        """Compute fulfilment from MicroVM instance states."""
        running_count = sum(1 for i in instances if i.get("status") == "running")
        pending_count = sum(1 for i in instances if i.get("status") == "pending")
        failed_count = sum(
            1 for i in instances if i.get("status") in ("shutting-down", "terminated")
        )

        if running_count >= requested_count and failed_count == 0:
            return ProviderFulfilment(
                state="fulfilled",
                message=f"All {running_count} MicroVM(s) running",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        elif pending_count > 0:
            return ProviderFulfilment(
                state="in_progress",
                message=f"{running_count}/{requested_count} running, {pending_count} pending",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        elif failed_count == len(instances) and len(instances) > 0:
            return ProviderFulfilment(
                state="failed",
                message=f"All {failed_count} MicroVM(s) terminated",
                target_units=requested_count,
                fulfilled_units=0,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        elif running_count > 0:
            return ProviderFulfilment(
                state="partial",
                message=f"{running_count}/{requested_count} MicroVM(s) running",
                target_units=requested_count,
                fulfilled_units=running_count,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )
        else:
            return ProviderFulfilment(
                state="in_progress",
                message="MicroVMs starting",
                target_units=requested_count,
                fulfilled_units=0,
                running_count=running_count,
                pending_count=pending_count,
                failed_count=failed_count,
            )

    # ------------------------------------------------------------------
    # Release
    # ------------------------------------------------------------------

    def release_hosts(
        self,
        machine_ids: list[str],
        resource_mapping: Optional[dict[str, tuple[Optional[str], int]]] = None,
        request_id: str = "",
    ) -> None:
        """Terminate MicroVMs in parallel."""
        if not machine_ids:
            self._logger.warning("No MicroVM IDs provided for termination")
            return

        self._logger.info("Terminating %d MicroVM(s): %s", len(machine_ids), machine_ids)
        errors: list[str] = []

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
            futures = {
                executor.submit(self._terminate_single_microvm, mid): mid
                for mid in machine_ids
            }
            for future in as_completed(futures):
                mid = futures[future]
                try:
                    future.result()
                except Exception as e:
                    self._logger.error("Failed to terminate MicroVM %s: %s", mid, e)
                    errors.append(f"{mid}: {e}")

        if errors:
            raise AWSInfrastructureError(
                f"Failed to terminate {len(errors)} MicroVM(s): {'; '.join(errors)}"
            )

        self._logger.info("Successfully terminated %d MicroVM(s)", len(machine_ids))

    def _terminate_single_microvm(self, microvm_id: str) -> None:
        """Terminate a single MicroVM with retry."""
        self._retry_with_backoff(
            self.aws_client.microvm_client.terminate_microvm,
            operation_type="standard",
            microvmIdentifier=microvm_id,
        )

    def cancel_resource(self, resource_id: str, request_id: str) -> dict[str, Any]:
        """Cancel a MicroVM by terminating it."""
        try:
            self._terminate_single_microvm(resource_id)
            return {"status": "success", "message": f"MicroVM {resource_id} terminated"}
        except Exception as e:
            self._logger.error("Failed to cancel MicroVM %s: %s", resource_id, e)
            return {
                "status": "error",
                "message": f"Failed to cancel MicroVM {resource_id}: {e!s}",
            }

    # ------------------------------------------------------------------
    # Machine Payload
    # ------------------------------------------------------------------

    @staticmethod
    def _build_machine_payload(microvm_response: dict[str, Any]) -> dict[str, Any]:
        """Convert a MicroVM API response to an ORB machine dict."""
        state = microvm_response.get("state", "PENDING")
        started_at = microvm_response.get("startedAt")
        if hasattr(started_at, "isoformat"):
            started_at = started_at.isoformat()

        return {
            "instance_id": microvm_response.get("microvmId"),
            "resource_id": microvm_response.get("microvmId"),
            "status": _MICROVM_STATE_MAP.get(state, "pending"),
            "private_ip": None,
            "public_ip": None,
            "launch_time": started_at,
            "instance_type": "microvm",
            "image_id": microvm_response.get("imageArn"),
            "subnet_id": None,
            "security_group_ids": [],
            "vpc_id": None,
            "tags": {},
            "price_type": None,
            "provider_api": "MicroVM",
            "name": microvm_response.get("microvmId", ""),
            "status_reason": microvm_response.get("stateReason"),
            "provider_data": {
                "endpoint": microvm_response.get("endpoint"),
                "image_version": microvm_response.get("imageVersion"),
                "execution_role_arn": microvm_response.get("executionRoleArn"),
                "state_reason": microvm_response.get("stateReason"),
                "maximum_duration_in_seconds": microvm_response.get("maximumDurationInSeconds"),
            },
            "metadata": {},
        }

    # ------------------------------------------------------------------
    # Example Templates
    # ------------------------------------------------------------------

    @classmethod
    def get_example_templates(cls) -> list[Template]:
        """Get example templates for the MicroVM handler."""
        return list(MICROVM_EXAMPLE_TEMPLATES)
