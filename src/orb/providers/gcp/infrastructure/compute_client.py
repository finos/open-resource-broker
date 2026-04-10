"""Thin wrapper over the official Compute Engine Python client library."""

from __future__ import annotations

# noinspection PyTypeHints
# PyCharm treats google-cloud-compute generated proto classes as Any in annotations here.
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from orb.domain.base.ports import LoggingPort
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.types import GCPInstanceRecord, GCPManagedInstanceRecord

if TYPE_CHECKING:
    from google.api_core.extended_operation import ExtendedOperation
    from google.cloud.compute_v1 import (
        ImagesClient,
        InstanceGroupManagersClient,
        InstanceTemplatesClient,
        InstancesClient,
        RegionInstanceGroupManagersClient,
        Image, Instance, InstanceGroupManager, InstanceTemplate
    )


# Keep the retryable GCP API failures visible at module scope so reviewers can
# reason about retry policy without digging through the Retry builder.
GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS: tuple[str, ...] = (
    "InternalServerError",
    "BadGateway",
    "ServiceUnavailable",
    "GatewayTimeout",
    "TooManyRequests",
)

GCP_READ_RETRYABLE_GOOGLE_API_EXCEPTIONS: tuple[str, ...] = (
    *GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS,
    "DeadlineExceeded",
)

GCP_MUTATION_RETRYABLE_GOOGLE_API_EXCEPTIONS: tuple[str, ...] = (
    *GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS,
    "ResourceExhausted",
)


@dataclass(frozen=True)
class GCPRetryProfile:
    """Reviewed retry/backoff settings for one class of GCP API operation."""

    policy_key: str
    retryable_exception_names: tuple[str, ...]
    initial_delay_seconds: float
    max_delay_seconds: float
    multiplier: float


# noinspection PyTypeHints
# The google-cloud-compute library uses dynamically generated proto classes that are not easily type-annotated.
class GCPComputeClient:
    """Execute a small subset of Compute Engine operations via google-cloud-compute."""

    def __init__(
        self,
        config: GCPProviderConfig,
        logger: LoggingPort,
    ) -> None:
        """Store provider config and lazily initialized Compute Engine clients."""
        self._config = config
        self._logger = logger
        self._instances_client: Optional[InstancesClient] = None
        self._instance_templates_client: Optional[InstanceTemplatesClient] = None
        self._region_igm_client: Optional[RegionInstanceGroupManagersClient] = None
        self._zone_igm_client: Optional[InstanceGroupManagersClient] = None
        self._images_client: Optional[ImagesClient] = None
        self._retry_policies: dict[str, Any] = {}

    def create_instance(
        self,
        *,
        zone: str,
        body: Instance,
    ) -> ExtendedOperation:
        """Create a standalone Compute Engine instance."""
        operation = self._get_instances_client().insert(
            project=self._config.project_id,
            zone=zone,
            instance_resource=body,
            **self._request_options("mutation"),
        )
        return operation

    def delete_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        """Delete a standalone Compute Engine instance."""
        operation = self._get_instances_client().delete(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options("delete"),
        )
        return operation

    def get_instance(self, *, zone: str, instance_name: str) -> GCPInstanceRecord:
        """Fetch one Compute Engine instance and normalize the response."""
        instance = self._get_instances_client().get(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options("read"),
        )
        return GCPInstanceRecord(
            name=str(instance.name),
            status=instance.status,
            self_link=instance.self_link,
        )

    def start_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        """Start a stopped Compute Engine instance."""
        operation = self._get_instances_client().start(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options("mutation"),
        )
        return operation

    def stop_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        """Stop a running Compute Engine instance."""
        operation = self._get_instances_client().stop(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options("mutation"),
        )
        return operation

    def create_instance_template(
        self,
        *,
        template_name: str,
        body: InstanceTemplate,
    ) -> ExtendedOperation:
        """Create an instance template for a managed instance group."""
        body.name = template_name
        operation = self._get_instance_templates_client().insert(
            project=self._config.project_id,
            instance_template_resource=body,
            **self._request_options("mutation"),
        )
        return operation

    def delete_instance_template(self, *, template_name: str) -> ExtendedOperation:
        """Delete an instance template by name."""
        operation = self._get_instance_templates_client().delete(
            project=self._config.project_id,
            instance_template=template_name,
            **self._request_options("delete"),
        )
        return operation

    def create_regional_mig(
        self,
        *,
        region: str,
        mig_name: str,
        body: InstanceGroupManager,
    ) -> ExtendedOperation:
        """Create a regional managed instance group."""
        body.name = mig_name
        operation = self._get_region_igm_client().insert(
            project=self._config.project_id,
            region=region,
            instance_group_manager_resource=body,
            **self._request_options("mutation"),
        )
        return operation

    def create_zonal_mig(
        self,
        *,
        zone: str,
        mig_name: str,
        body: InstanceGroupManager,
    ) -> ExtendedOperation:
        """Create a zonal managed instance group."""
        body.name = mig_name
        operation = self._get_zone_igm_client().insert(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager_resource=body,
            **self._request_options("mutation"),
        )
        return operation

    def delete_regional_mig(self, *, region: str, mig_name: str) -> ExtendedOperation:
        """Delete a regional managed instance group."""
        operation = self._get_region_igm_client().delete(
            project=self._config.project_id,
            region=region,
            instance_group_manager=mig_name,
            **self._request_options("delete"),
        )
        return operation

    def delete_zonal_mig(self, *, zone: str, mig_name: str) -> ExtendedOperation:
        """Delete a zonal managed instance group."""
        operation = self._get_zone_igm_client().delete(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            **self._request_options("delete"),
        )
        return operation

    def list_regional_managed_instances(
        self,
        *,
        region: str,
        mig_name: str,
    ) -> list[GCPManagedInstanceRecord]:
        """List instances currently tracked by a regional managed instance group."""
        response = self._get_region_igm_client().list_managed_instances(
            project=self._config.project_id,
            region=region,
            instance_group_manager=mig_name,
            **self._request_options("read"),
        )
        return [
            GCPManagedInstanceRecord(
                instance_url=str(item.instance),
                instance_status=item.instance_status,
                current_action=item.current_action,
            )
            for item in response
        ]

    def list_zonal_managed_instances(
        self,
        *,
        zone: str,
        mig_name: str,
    ) -> list[GCPManagedInstanceRecord]:
        """List instances currently tracked by a zonal managed instance group."""
        response = self._get_zone_igm_client().list_managed_instances(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            **self._request_options("read"),
        )
        return [
            GCPManagedInstanceRecord(
                instance_url=str(item.instance),
                instance_status=item.instance_status,
                current_action=item.current_action,
            )
            for item in response
        ]

    def delete_regional_managed_instances(
        self,
        *,
        region: str,
        mig_name: str,
        instance_urls: list[str],
    ) -> ExtendedOperation:
        """Delete specific instances from a regional managed instance group."""
        compute_v1 = self._compute_v1()
        operation = self._get_region_igm_client().delete_instances(
            project=self._config.project_id,
            region=region,
            instance_group_manager=mig_name,
            region_instance_group_managers_delete_instances_request_resource=(
                compute_v1.RegionInstanceGroupManagersDeleteInstancesRequest(
                    instances=instance_urls
                )
            ),
            **self._request_options("delete"),
        )
        return operation

    def delete_zonal_managed_instances(
        self,
        *,
        zone: str,
        mig_name: str,
        instance_urls: list[str],
    ) -> ExtendedOperation:
        """Delete specific instances from a zonal managed instance group."""
        compute_v1 = self._compute_v1()
        operation = self._get_zone_igm_client().delete_instances(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            instance_group_managers_delete_instances_request_resource=(
                compute_v1.InstanceGroupManagersDeleteInstancesRequest(instances=instance_urls)
            ),
            **self._request_options("delete"),
        )
        return operation

    def get_image_from_family(self, *, image_project: str, family: str) -> Image:
        """Resolve the latest image in a Compute Engine image family."""
        image = self._get_images_client().get_from_family(
            project=image_project,
            family=family,
            **self._request_options("image_read"),
        )
        return image

    def _request_options(self, operation_name: str) -> dict[str, Any]:
        return {
            "retry": self._get_retry_policy(operation_name),
            "timeout": (
                float(self._config.connect_timeout),
                float(self._config.read_timeout),
            ),
        }

    def _get_retry_policy(self, operation_name: str) -> Any:
        if self._config.max_retries == 0:
            return None
        retry_profile = self._retry_profile_for(operation_name)
        cached_policy = self._retry_policies.get(retry_profile.policy_key)
        if cached_policy is None:
            cached_policy = self._build_retry_policy(operation_name)
            self._retry_policies[retry_profile.policy_key] = cached_policy
        return cached_policy

    def _build_retry_policy(self, operation_name: str) -> Any:
        try:
            from google.api_core import exceptions as google_exceptions
            from google.api_core.retry import Retry, if_exception_type
        except ImportError as exc:
            raise RuntimeError(
                "google-api-core is required for GCP retry configuration"
            ) from exc

        retry_profile = self._retry_profile_for(operation_name)
        per_attempt_timeout = float(self._config.connect_timeout + self._config.read_timeout)
        # getattr use is intentional at this sdk boundary: it keeps the public constant
        # readable without hiding the actual retry policy inside inline exception references.
        retryable_exceptions = tuple(
            getattr(google_exceptions, exception_name)
            for exception_name in retry_profile.retryable_exception_names
        )
        return Retry(
            predicate=if_exception_type(*retryable_exceptions),
            initial=retry_profile.initial_delay_seconds,
            maximum=max(1.0, retry_profile.max_delay_seconds),
            multiplier=retry_profile.multiplier,
            timeout=max(1.0, per_attempt_timeout * float(self._config.max_retries)),
            on_error=lambda exc: self._log_retry_attempt(operation_name, exc),
        )

    def _retry_profile_for(self, operation_name: str) -> GCPRetryProfile:
        if operation_name in {"read", "image_read"}:
            max_delay = min(float(self._config.read_timeout), 5.0)
            return GCPRetryProfile(
                policy_key="read",
                retryable_exception_names=GCP_READ_RETRYABLE_GOOGLE_API_EXCEPTIONS,
                initial_delay_seconds=0.5,
                max_delay_seconds=max(1.0, max_delay),
                multiplier=1.5,
            )
        if operation_name in {"mutation", "delete"}:
            max_delay = min(float(self._config.read_timeout), 20.0)
            return GCPRetryProfile(
                policy_key=operation_name,
                retryable_exception_names=GCP_MUTATION_RETRYABLE_GOOGLE_API_EXCEPTIONS,
                initial_delay_seconds=1.0 if operation_name == "mutation" else 2.0,
                max_delay_seconds=max(1.0, max_delay),
                multiplier=2.0,
            )
        raise ValueError(f"Unsupported GCP retry operation profile: {operation_name}")

    def _log_retry_attempt(self, operation_name: str, exc: Exception) -> None:
        self._logger.warning(
            "Retrying GCP %s operation after %s: %s",
            operation_name,
            exc.__class__.__name__,
            exc,
        )

    def _compute_v1(self) -> Any:
        try:
            from google.cloud import compute_v1
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-compute is required for the GCP provider runtime"
            ) from exc
        return compute_v1

    def _get_instances_client(self) -> InstancesClient:
        if self._instances_client is None:
            self._instances_client = self._compute_v1().InstancesClient()
        assert self._instances_client is not None
        return self._instances_client

    def _get_instance_templates_client(self) -> InstanceTemplatesClient:
        if self._instance_templates_client is None:
            self._instance_templates_client = self._compute_v1().InstanceTemplatesClient()
        assert self._instance_templates_client is not None
        return self._instance_templates_client

    def _get_region_igm_client(self) -> RegionInstanceGroupManagersClient:
        if self._region_igm_client is None:
            self._region_igm_client = self._compute_v1().RegionInstanceGroupManagersClient()
        assert self._region_igm_client is not None
        return self._region_igm_client

    def _get_zone_igm_client(self) -> InstanceGroupManagersClient:
        if self._zone_igm_client is None:
            self._zone_igm_client = self._compute_v1().InstanceGroupManagersClient()
        assert self._zone_igm_client is not None
        return self._zone_igm_client

    def _get_images_client(self) -> ImagesClient:
        if self._images_client is None:
            self._images_client = self._compute_v1().ImagesClient()
        assert self._images_client is not None
        return self._images_client
