"""Compute Engine single-instance handler."""

from __future__ import annotations

import uuid
from typing import Callable

from orb.domain.request.aggregate import Request
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.exceptions import GCPError, GCPValidationError, translate_gcp_exception
from orb.providers.gcp.infrastructure.instance_status import normalize_gcp_instance_status
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.types import (
    GCPCreateOutcome,
    GCPFailedOperation,
    GCPHandlerContext,
    GCPInstanceRecord,
    GCPInstanceStatus,
    GCPMutationOutcome,
    GCPProviderData,
)
from google.api_core.extended_operation import ExtendedOperation
from google.cloud.compute_v1.types import Instance

try:
    from google.api_core import exceptions as google_exceptions
except ImportError:  # pragma: no cover - exercised only when optional sdk deps are absent
    google_exceptions = None


class GCPSingleVMHandler(GCPHandler):
    """Create and manage standalone Compute Engine instances."""

    def acquire_hosts(self, request: Request, template: GCPTemplate) -> GCPCreateOutcome:
        """Create the requested number of standalone VM instances."""
        zone = self._template_zone(template)
        instances: list[GCPInstanceStatus] = []
        resource_ids: list[str] = []
        failed_operations: list[GCPFailedOperation] = []

        for _ in range(request.requested_count):
            instance_name = f"gcp-{template.template_id}-{uuid.uuid4().hex[:8]}"
            payload = self._build_instance_payload(instance_name, template)
            try:
                operation = self._compute_client.create_instance(
                    zone=zone,
                    body=payload,
                )
            except _recoverable_gcp_operation_exceptions() as exc:
                translated = translate_gcp_exception(
                    exc,
                    operation="create_instance",
                    details={"instance_id": instance_name, "zone": zone},
                )
                self._logger.warning(
                    "GCP create_instance failed for %s in %s: %s",
                    instance_name,
                    zone,
                    translated,
                )
                failed_operations.append(
                    GCPFailedOperation(
                        target_id=instance_name,
                        error_code=translated.error_code,
                        error_message=str(translated),
                        operation="create_instance",
                    )
                )
                continue

            resource_ids.append(instance_name)
            instances.append(
                {
                    "instance_id": instance_name,
                    "status": "PROVISIONING",
                    "provider_data": {"zone": zone, "operation_name": operation.name or ""},
                }
            )

        provider_data: GCPProviderData = {
            "zone": zone,
            "requested_count": request.requested_count,
            "submitted_count": len(resource_ids),
            "partial_failure": bool(failed_operations),
            "operation_status": "submitted",
            "failed_operations": len(failed_operations),
        }

        return GCPCreateOutcome(
            resource_ids=resource_ids,
            instances=instances,
            provider_data=provider_data,
            failed_operations=failed_operations,
        )

    def terminate_hosts(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Terminate the targeted standalone VM instances."""
        zone = self._require_zone(context)
        target_ids = instance_ids or resource_ids
        return self._run_per_instance_mutation(
            target_ids=target_ids,
            operation_name="terminate_instance",
            mutation=lambda instance_name: self._compute_client.delete_instance(
                zone=zone,
                instance_name=instance_name,
            ),
        )

    def check_hosts_status(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> list[GCPInstanceStatus]:
        """Fetch current status for the targeted standalone VM instances."""
        zone = self._require_zone(context)
        target_ids = instance_ids or resource_ids
        results: list[GCPInstanceStatus] = []
        for instance_name in target_ids:
            try:
                instance = self._compute_client.get_instance(zone=zone, instance_name=instance_name)
            except _not_found_gcp_operation_exceptions():
                self._logger.info(
                    "GCP instance %s not found in %s during status check",
                    instance_name,
                    zone,
                )
                continue
            results.append(_build_gcp_status_result(instance, instance_name, zone))
        return results

    def start_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Start the targeted standalone VM instances."""
        zone = self._require_zone(context)
        return self._run_per_instance_mutation(
            target_ids=instance_ids,
            operation_name="start_instance",
            mutation=lambda instance_name: self._compute_client.start_instance(
                zone=zone,
                instance_name=instance_name,
            ),
        )

    def stop_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Stop the targeted standalone VM instances."""
        zone = self._require_zone(context)
        return self._run_per_instance_mutation(
            target_ids=instance_ids,
            operation_name="stop_instance",
            mutation=lambda instance_name: self._compute_client.stop_instance(
                zone=zone,
                instance_name=instance_name,
            ),
        )

    def _build_instance_payload(self, instance_name: str, template: GCPTemplate) -> Instance:
        from google.cloud import compute_v1

        zone = self._template_zone(template)
        machine_type = (
            template.instance_type
            if template.instance_type.startswith("zones/")
            else f"zones/{zone}/machineTypes/{template.instance_type}"
        )
        return compute_v1.Instance(
            name=instance_name,
            **self._build_instance_configuration(
                template=template,
                machine_type=machine_type,
                zone=zone,
                payload_context="instance",
            ),
        )

    @staticmethod
    def _require_zone(context: GCPHandlerContext) -> str:
        zone = context.get("zone")
        if not zone:
            raise GCPValidationError("zone is required for SingleVM operations")
        return str(zone)

    @staticmethod
    def _template_zone(template: GCPTemplate) -> str:
        if len(template.zones) != 1:
            raise GCPValidationError("SingleVM templates require exactly one explicit zone")
        return str(template.zones[0])

    def _run_per_instance_mutation(
        self,
        *,
        target_ids: list[str],
        operation_name: str,
        mutation: Callable[[str], ExtendedOperation],
    ) -> GCPMutationOutcome:
        result = GCPMutationOutcome(
            attempted_ids=list(target_ids),
            successful_ids=[],
            operations=[],
        )

        for instance_name in target_ids:
            try:
                response = mutation(instance_name)
            except _recoverable_gcp_operation_exceptions() as exc:
                translated = translate_gcp_exception(
                    exc,
                    operation=operation_name,
                    details={"instance_id": instance_name},
                )
                self._logger.warning(
                    "GCP %s failed for %s: %s",
                    operation_name,
                    instance_name,
                    translated,
                )
                result.failed_operations.append(
                    GCPFailedOperation(
                        target_id=instance_name,
                        error_code=translated.error_code,
                        error_message=str(translated),
                        operation=operation_name,
                    )
                )
                continue

            result.operations.append(
                {
                    "instance_id": instance_name,
                    "operation_name": response.name,
                }
            )
            result.successful_ids.append(instance_name)

        return result


def _recoverable_gcp_operation_exceptions() -> tuple[type[Exception], ...]:
    """Return the exception types that should become per-target provider failures."""
    exceptions: tuple[type[Exception], ...] = (GCPError, RuntimeError)
    if google_exceptions is not None:
        exceptions = exceptions + (google_exceptions.GoogleAPICallError,)
    return exceptions


def _not_found_gcp_operation_exceptions() -> tuple[type[Exception], ...]:
    """Return exception types that mean an instance is absent from GCP."""
    exceptions: tuple[type[Exception], ...] = ()
    if google_exceptions is not None:
        exceptions = exceptions + (google_exceptions.NotFound,)
    return exceptions


def _build_gcp_status_result(
    record: GCPInstanceRecord, instance_name: str, zone: str
) -> GCPInstanceStatus:
    """Build the normalized GCP status record from a ``GCPInstanceRecord``.

    Maps the boundary type to the AWS-pattern instance dict: provider-specific
    fields (zone, region, cloud_host_id) live under ``provider_data``; tags
    come from the record's ``labels`` (GCP's user-key/value analogue, not the
    firewall ``tags`` concept); price_type is derived from the scheduling
    provisioning model.
    """
    price_type = "spot" if record.provisioning_model == "SPOT" else "ondemand"
    return {
        "instance_id": instance_name,
        "name": record.name or instance_name,
        "status": normalize_gcp_instance_status(record.status),
        "private_ip": record.private_ip,
        "public_ip": record.public_ip,
        "launch_time": record.creation_timestamp,
        "instance_type": record.machine_type,
        "subnet_id": record.subnet_id,
        "vpc_id": record.vpc_id,
        "tags": record.labels,
        "price_type": price_type,
        "provider_data": {
            "cloud_host_id": record.instance_id or instance_name,
            "resource_id": record.self_link or instance_name,
            "zone": zone,
            "subnet_id": record.subnet_id,
            "vpc_id": record.vpc_id,
        },
    }
