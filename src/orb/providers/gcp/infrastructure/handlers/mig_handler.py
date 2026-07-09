"""Managed Instance Group handler for GCP."""

from __future__ import annotations

from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Protocol
import uuid

from orb.domain.request.aggregate import Request
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.domain.template.value_objects import GCPMIGScope
from orb.providers.gcp.exceptions import (
    GCPEntityNotFoundError,
    GCPNetworkError,
    GCPValidationError,
    translate_gcp_exception,
)
from orb.providers.gcp.infrastructure.instance_status import (
    normalize_gcp_managed_instance_status,
)
from orb.providers.gcp.infrastructure.handlers.base_handler import GCPHandler
from orb.providers.gcp.types import (
    GCPCreateOutcome,
    GCPHandlerContext,
    GCPInstanceStatus,
    GCPManagedInstanceRecord,
    GCPMutationOutcome,
    GCPProviderData,
)


class _GCPOperationWithName(Protocol):
    """Minimal GCP operation shape used by the MIG handler."""

    @property
    def name(self) -> str | None:
        """Return the provider operation name."""
        ...


class GCPManagedInstanceGroupHandler(GCPHandler):
    """Create and manage zonal or regional Managed Instance Groups."""

    _DELETE_SUBMITTED_WARNING = (
        "Delete operation submitted to GCP; completion must be confirmed by later polling."
    )

    def acquire_hosts(self, request: Request, template: GCPTemplate) -> GCPCreateOutcome:
        """Create the MIG and backing instance template for a request."""
        mig_name = template.mig_name or f"orb-mig-{template.template_id}-{uuid.uuid4().hex[:8]}"
        template_name = (
            f"{template.instance_template_name_prefix or 'orb'}-{template.template_id}-{uuid.uuid4().hex[:8]}"
        )
        template_operation = self._compute_client.create_instance_template(
            template_name=template_name,
            body=self._build_instance_template_payload(template, template_name),
        )
        # Wait for the template insert to finish before creating the MIG, otherwise
        # the subsequent MIG create can race eventual consistency on template lookup.
        wait_timeout_seconds = self._operation_wait_timeout_seconds()
        try:
            template_operation.result(timeout=wait_timeout_seconds)
        except FutureTimeoutError as exc:
            raise GCPNetworkError(
                "Timed out waiting for GCP instance template creation to finish",
                details={
                    "operation": "create_instance_template",
                    "template_name": template_name,
                    "operation_name": template_operation.name or "",
                    "timeout_seconds": wait_timeout_seconds,
                },
            ) from exc

        try:
            if template.mig_scope == GCPMIGScope.REGIONAL:
                region = str(template.region)
                location_context = {"region": region, "scope": template.mig_scope.value}
                response = self._compute_client.create_regional_mig(
                    region=region,
                    mig_name=mig_name,
                    body=self._build_regional_mig_payload(
                        template=template,
                        template_name=template_name,
                        target_size=request.requested_count,
                    ),
                )
            else:
                zone = str(template.zones[0])
                location_context = {"zone": zone, "scope": template.mig_scope.value}
                response = self._compute_client.create_zonal_mig(
                    zone=zone,
                    mig_name=mig_name,
                    body=self._build_zonal_mig_payload(
                        template=template,
                        template_name=template_name,
                        target_size=request.requested_count,
                    ),
                )
        except Exception:
            self._rollback_instance_template(template_name)
            raise

        try:
            response.result(timeout=wait_timeout_seconds)
        except FutureTimeoutError as exc:
            raise GCPNetworkError(
                "Timed out waiting for GCP managed instance group creation to finish",
                details={
                    "operation": "create_mig",
                    "mig_name": mig_name,
                    "instance_template_name": template_name,
                    "operation_name": response.name or "",
                    "timeout_seconds": wait_timeout_seconds,
                    **location_context,
                },
            ) from exc
        except Exception:
            self._rollback_instance_template(template_name)
            raise

        provider_data: GCPProviderData = {
            "mig_name": mig_name,
            "instance_template_name": template_name,
            "target_size": request.requested_count,
            "operation_name": response.name or "",
            "operation_status": "completed",
            **location_context,
        }

        return GCPCreateOutcome(
            resource_ids=[mig_name],
            instances=[],
            provider_data=provider_data,
        )

    def _rollback_instance_template(self, template_name: str) -> None:
        try:
            self._compute_client.delete_instance_template(template_name=template_name)
        except Exception as cleanup_exc:
            self._logger.warning(
                "Failed to roll back orphaned instance template %s after MIG create failure: %s",
                template_name,
                cleanup_exc,
            )

    def terminate_hosts(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Delete specific MIG members or tear down whole MIG resources."""
        mig_names = self._require_mig_names(resource_ids, context)
        template_name = context.get("instance_template_name")
        scope = str(context.get("scope") or GCPMIGScope.REGIONAL.value)

        if instance_ids:
            managed_instances_by_mig = {
                mig_name: self._list_managed_instances(
                    scope=scope,
                    context=context,
                    mig_name=mig_name,
                )
                for mig_name in mig_names
            }
            grouped_instance_urls = self._group_instance_urls_by_mig(
                mig_names=mig_names,
                instance_ids=instance_ids,
                context=context,
                scope=scope,
            )
            operations: list[dict[str, str | None]] = []
            deleted_mig_names: list[str] = []
            for mig_name, instance_urls in grouped_instance_urls.items():
                all_instance_urls = {
                    str(instance.instance_url) for instance in managed_instances_by_mig[mig_name]
                }
                if set(instance_urls) == all_instance_urls:
                    response = self._delete_mig(scope=scope, context=context, mig_name=mig_name)
                    deleted_mig_names.append(mig_name)
                    operations.append(
                        {
                            "operation_name": response.name,
                            "mig_name": mig_name,
                            "operation_type": "delete_mig",
                        }
                    )
                else:
                    response = self._delete_managed_instances(
                        scope=scope,
                        context=context,
                        mig_name=mig_name,
                        instance_urls=instance_urls,
                    )
                    operations.append(
                        {
                            "operation_name": response.name,
                            "mig_name": mig_name,
                            "operation_type": "delete_managed_instances",
                        }
                    )
            if set(deleted_mig_names) == set(mig_names):
                self._delete_instance_template(template_name)
            return GCPMutationOutcome(
                attempted_ids=instance_ids,
                successful_ids=[],
                operations=operations,
                warning=self._DELETE_SUBMITTED_WARNING,
            )

        operations: list[dict[str, str | None]] = []
        for mig_name in mig_names:
            response = self._delete_mig(scope=scope, context=context, mig_name=mig_name)
            operations.append(
                {
                    "operation_name": response.name,
                    "mig_name": mig_name,
                    "operation_type": "delete_mig",
                }
            )

        self._delete_instance_template(template_name)

        return GCPMutationOutcome(
            attempted_ids=mig_names,
            successful_ids=[],
            operations=operations,
            warning=self._DELETE_SUBMITTED_WARNING,
        )

    def check_hosts_status(
        self,
        *,
        resource_ids: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> list[GCPInstanceStatus]:
        """Describe the current status of instances managed by the MIG."""
        mig_names = self._require_mig_names(resource_ids, context)
        scope = str(context.get("scope") or GCPMIGScope.REGIONAL.value)
        results: list[GCPInstanceStatus] = []
        for mig_name in mig_names:
            payload = self._list_managed_instances_for_status(
                scope=scope,
                context=context,
                mig_name=mig_name,
            )

            for instance in payload:
                instance_name = instance.instance_url.rsplit("/", 1)[-1]
                if instance_ids and instance_name not in instance_ids:
                    continue
                status = normalize_gcp_managed_instance_status(
                    instance_status=instance.instance_status,
                    current_action=instance.current_action,
                )
                results.append(
                    {
                        "instance_id": instance_name,
                        "status": status,
                        "provider_data": {
                            # The managed-instances listing exposes the URL but
                            # not the numeric Google instance ID; the name is
                            # the stable HostFactory-visible identifier here.
                            "cloud_host_id": instance_name,
                            "resource_id": mig_name,
                            "scope": scope,
                            "instance_url": instance.instance_url,
                            "gcp_instance_status": instance.instance_status,
                            "gcp_current_action": instance.current_action,
                        },
                    }
                )
        return results

    def start_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Report that direct start operations are unsupported for MIG-managed instances."""
        return GCPMutationOutcome(
            attempted_ids=instance_ids,
            successful_ids=[],
            operations=[],
            warning="MIG-managed instances follow group policy; start is not supported directly",
        )

    def stop_instances(
        self,
        *,
        instance_ids: list[str],
        context: GCPHandlerContext,
    ) -> GCPMutationOutcome:
        """Report that direct stop operations are unsupported for MIG-managed instances."""
        return GCPMutationOutcome(
            attempted_ids=instance_ids,
            successful_ids=[],
            operations=[],
            warning="MIG-managed instances follow group policy; stop is not supported directly",
        )

    def _build_instance_template_payload(
        self,
        template: GCPTemplate,
        template_name: str,
    ):
        from google.cloud import compute_v1

        properties = compute_v1.InstanceProperties(
            **self._build_instance_configuration(
                template=template,
                machine_type=template.instance_type,
                zone=str(template.zones[0]) if template.zones else None,
                payload_context="instance_template",
            )
        )
        return compute_v1.InstanceTemplate(properties=properties)

    def _build_regional_mig_payload(
        self,
        *,
        template: GCPTemplate,
        template_name: str,
        target_size: int,
    ):
        from google.cloud import compute_v1

        distribution = None
        if template.zones:
            distribution = compute_v1.DistributionPolicy(
                zones=[
                    {"zone": f"projects/{template.project_id}/zones/{zone}"}
                    for zone in template.zones
                ]
            )
        return compute_v1.InstanceGroupManager(
            base_instance_name=template.template_id,
            instance_template=f"projects/{template.project_id}/global/instanceTemplates/{template_name}",
            target_size=target_size,
            distribution_policy=distribution,
        )

    def _build_zonal_mig_payload(
        self,
        *,
        template: GCPTemplate,
        template_name: str,
        target_size: int,
    ):
        from google.cloud import compute_v1

        return compute_v1.InstanceGroupManager(
            base_instance_name=template.template_id,
            instance_template=f"projects/{template.project_id}/global/instanceTemplates/{template_name}",
            target_size=target_size,
        )

    def _delete_mig(
        self,
        *,
        scope: str,
        context: GCPHandlerContext,
        mig_name: str,
    ) -> _GCPOperationWithName:
        if scope == GCPMIGScope.ZONAL.value:
            return self._compute_client.delete_zonal_mig(
                zone=self._require_zone(context),
                mig_name=mig_name,
            )
        return self._compute_client.delete_regional_mig(
            region=self._require_region(context),
            mig_name=mig_name,
        )

    def _delete_managed_instances(
        self,
        *,
        scope: str,
        context: GCPHandlerContext,
        mig_name: str,
        instance_urls: list[str],
    ) -> _GCPOperationWithName:
        if scope == GCPMIGScope.ZONAL.value:
            return self._compute_client.delete_zonal_managed_instances(
                zone=self._require_zone(context),
                mig_name=mig_name,
                instance_urls=instance_urls,
            )
        return self._compute_client.delete_regional_managed_instances(
            region=self._require_region(context),
            mig_name=mig_name,
            instance_urls=instance_urls,
        )

    def _delete_instance_template(self, template_name: object) -> None:
        if not template_name:
            return
        try:
            self._compute_client.delete_instance_template(template_name=str(template_name))
        except Exception as exc:
            self._logger.warning(
                "Best-effort instance template cleanup failed for %s: %s",
                template_name,
                exc,
            )

    def _list_managed_instances_for_status(
        self,
        *,
        scope: str,
        context: GCPHandlerContext,
        mig_name: str,
    ) -> list[GCPManagedInstanceRecord]:
        try:
            return self._list_managed_instances(
                scope=scope,
                context=context,
                mig_name=mig_name,
            )
        except GCPEntityNotFoundError:
            return []

    def _list_managed_instances(
        self,
        *,
        scope: str,
        context: GCPHandlerContext,
        mig_name: str,
        instance_filter: str | None = None,
    ) -> list[GCPManagedInstanceRecord]:
        try:
            if scope == GCPMIGScope.ZONAL.value:
                return self._compute_client.list_zonal_managed_instances(
                    zone=self._require_zone(context),
                    mig_name=mig_name,
                    instance_filter=instance_filter,
                )
            return self._compute_client.list_regional_managed_instances(
                region=self._require_region(context),
                mig_name=mig_name,
                instance_filter=instance_filter,
            )
        except Exception as exc:
            translated = translate_gcp_exception(
                exc,
                operation="list_managed_instances",
                details={"mig_name": mig_name, "scope": scope},
            )
            raise translated from exc

    @staticmethod
    def _require_mig_names(resource_ids: list[str], context: GCPHandlerContext) -> list[str]:
        mig_names = [str(resource_id) for resource_id in resource_ids if resource_id]
        if mig_names:
            return mig_names
        mig_name = context.get("mig_name")
        if not mig_name:
            raise GCPValidationError("MIG operations require a mig resource id")
        return [str(mig_name)]

    def _group_instance_urls_by_mig(
        self,
        *,
        mig_names: list[str],
        instance_ids: list[str],
        context: GCPHandlerContext,
        scope: str,
    ) -> dict[str, list[str]]:
        # Ask GCP for only the instances we care about by passing a server-side
        # filter on the ``instance`` field (full URL).  Each instance name is
        # matched with a suffix regex so both short names and full URLs work.
        instance_filter = self._build_instance_filter(instance_ids)

        name_to_membership: dict[str, list[tuple[str, str]]] = {}
        for mig_name in mig_names:
            members = self._list_managed_instances(
                scope=scope,
                context=context,
                mig_name=mig_name,
                instance_filter=instance_filter,
            )
            for member in members:
                instance_url = str(member.instance_url)
                instance_name = instance_url.rsplit("/", 1)[-1]
                entry = (mig_name, instance_url)
                name_to_membership.setdefault(instance_name, []).append(entry)
                name_to_membership.setdefault(instance_url, []).append(entry)

        grouped: dict[str, list[str]] = {}
        for instance_id in instance_ids:
            requested_name = str(instance_id).rsplit("/", 1)[-1]
            matches = name_to_membership.get(str(instance_id)) or name_to_membership.get(
                requested_name
            )
            if not matches:
                raise GCPEntityNotFoundError(
                    f"Could not resolve MIG membership for instance '{instance_id}'",
                    details={"instance_id": str(instance_id), "mig_names": mig_names},
                )
            if len(matches) > 1:
                raise GCPValidationError(
                    f"Instance '{instance_id}' matches multiple MIG resources; "
                    "use fully qualified instance URLs"
                )
            mig_name, instance_url = matches[0]
            grouped.setdefault(mig_name, []).append(instance_url)
        return grouped

    @staticmethod
    def _build_instance_filter(instance_ids: list[str]) -> str:
        """Build a Compute API filter expression matching specific instances.

        The ``instance`` field on ManagedInstance is a full URL
        (``projects/…/zones/…/instances/{name}``).  A suffix regex handles
        both short names and fully-qualified URLs as input.
        """
        clauses = []
        for instance_id in instance_ids:
            name = str(instance_id).rsplit("/", 1)[-1]
            clauses.append(f'(instance eq ".*/{name}")')
        return " OR ".join(clauses)

    @staticmethod
    def _require_region(context: GCPHandlerContext) -> str:
        region = context.get("region")
        if not region:
            raise GCPValidationError("region is required for regional MIG operations")
        return str(region)

    @staticmethod
    def _require_zone(context: GCPHandlerContext) -> str:
        zone = context.get("zone")
        if not zone:
            raise GCPValidationError("zone is required for zonal MIG operations")
        return str(zone)

    @staticmethod
    def _instance_url(instance_id: str, *, context: GCPHandlerContext) -> str:
        existing = str(instance_id)
        if existing.startswith("https://") or existing.startswith("projects/"):
            return existing
        zone = context.get("zone")
        project_id = context.get("project_id")
        if not zone or not project_id:
            return existing
        return f"projects/{project_id}/zones/{zone}/instances/{existing}"
