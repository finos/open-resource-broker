"""Thin wrapper over the official Compute Engine Python client library."""

from __future__ import annotations

# noinspection PyTypeHints
# PyCharm treats google-cloud-compute generated proto classes as Any in annotations here.
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


# noinspection PyTypeHints
# The google-cloud-compute library uses dynamically generated proto classes that are not easily type-annotated.
class GCPComputeClient:
    """Execute a small subset of Compute Engine operations via google-cloud-compute."""

    def __init__(
        self,
        config: GCPProviderConfig,
        logger: LoggingPort,
    ) -> None:
        self._config = config
        self._logger = logger
        self._instances_client: Optional[InstancesClient] = None
        self._instance_templates_client: Optional[InstanceTemplatesClient] = None
        self._region_igm_client: Optional[RegionInstanceGroupManagersClient] = None
        self._zone_igm_client: Optional[InstanceGroupManagersClient] = None
        self._images_client: Optional[ImagesClient] = None
        self._retry_policy: Any | None = None

    def create_instance(
        self,
        *,
        zone: str,
        body: Instance,
    ) -> ExtendedOperation:
        operation = self._get_instances_client().insert(
            project=self._config.project_id,
            zone=zone,
            instance_resource=body,
            **self._request_options(),
        )
        return operation

    def delete_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        operation = self._get_instances_client().delete(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options(),
        )
        return operation

    def get_instance(self, *, zone: str, instance_name: str) -> GCPInstanceRecord:
        instance = self._get_instances_client().get(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options(),
        )
        return GCPInstanceRecord(
            name=str(instance.name),
            status=instance.status,
            self_link=instance.self_link,
        )

    def start_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        operation = self._get_instances_client().start(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options(),
        )
        return operation

    def stop_instance(self, *, zone: str, instance_name: str) -> ExtendedOperation:
        operation = self._get_instances_client().stop(
            project=self._config.project_id,
            zone=zone,
            instance=instance_name,
            **self._request_options(),
        )
        return operation

    def create_instance_template(
        self,
        *,
        template_name: str,
        body: InstanceTemplate,
    ) -> ExtendedOperation:
        body.name = template_name
        operation = self._get_instance_templates_client().insert(
            project=self._config.project_id,
            instance_template_resource=body,
            **self._request_options(),
        )
        return operation

    def delete_instance_template(self, *, template_name: str) -> ExtendedOperation:
        operation = self._get_instance_templates_client().delete(
            project=self._config.project_id,
            instance_template=template_name,
            **self._request_options(),
        )
        return operation

    def create_regional_mig(
        self,
        *,
        region: str,
        mig_name: str,
        body: InstanceGroupManager,
    ) -> ExtendedOperation:
        body.name = mig_name
        operation = self._get_region_igm_client().insert(
            project=self._config.project_id,
            region=region,
            instance_group_manager_resource=body,
            **self._request_options(),
        )
        return operation

    def create_zonal_mig(
        self,
        *,
        zone: str,
        mig_name: str,
        body: InstanceGroupManager,
    ) -> ExtendedOperation:
        body.name = mig_name
        operation = self._get_zone_igm_client().insert(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager_resource=body,
            **self._request_options(),
        )
        return operation

    def delete_regional_mig(self, *, region: str, mig_name: str) -> ExtendedOperation:
        operation = self._get_region_igm_client().delete(
            project=self._config.project_id,
            region=region,
            instance_group_manager=mig_name,
            **self._request_options(),
        )
        return operation

    def delete_zonal_mig(self, *, zone: str, mig_name: str) -> ExtendedOperation:
        operation = self._get_zone_igm_client().delete(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            **self._request_options(),
        )
        return operation

    def list_regional_managed_instances(
        self,
        *,
        region: str,
        mig_name: str,
    ) -> list[GCPManagedInstanceRecord]:
        response = self._get_region_igm_client().list_managed_instances(
            project=self._config.project_id,
            region=region,
            instance_group_manager=mig_name,
            **self._request_options(),
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
        response = self._get_zone_igm_client().list_managed_instances(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            **self._request_options(),
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
            **self._request_options(),
        )
        return operation

    def delete_zonal_managed_instances(
        self,
        *,
        zone: str,
        mig_name: str,
        instance_urls: list[str],
    ) -> ExtendedOperation:
        compute_v1 = self._compute_v1()
        operation = self._get_zone_igm_client().delete_instances(
            project=self._config.project_id,
            zone=zone,
            instance_group_manager=mig_name,
            instance_group_managers_delete_instances_request_resource=(
                compute_v1.InstanceGroupManagersDeleteInstancesRequest(instances=instance_urls)
            ),
            **self._request_options(),
        )
        return operation

    def get_image_from_family(self, *, image_project: str, family: str) -> Image:
        image = self._get_images_client().get_from_family(
            project=image_project,
            family=family,
            **self._request_options(),
        )
        return image

    def _request_options(self) -> dict[str, Any]:
        return {
            "retry": self._get_retry_policy(),
            "timeout": (
                float(self._config.connect_timeout),
                float(self._config.read_timeout),
            ),
        }

    def _get_retry_policy(self) -> Any:
        if self._config.max_retries == 0:
            return None
        if self._retry_policy is None:
            self._retry_policy = self._build_retry_policy()
        return self._retry_policy

    def _build_retry_policy(self) -> Any:
        try:
            from google.api_core import exceptions as google_exceptions
            from google.api_core.retry import Retry, if_exception_type
        except ImportError as exc:
            raise RuntimeError(
                "google-api-core is required for GCP retry configuration"
            ) from exc

        per_attempt_timeout = float(
            self._config.connect_timeout + self._config.read_timeout
        )
        # Keep the reviewed retryable set as string names at module scope for visibility,
        # then resolve those names to sdk exception classes here for if_exception_type().
        # getattr use is intentional at this sdk boundary: it keeps the public constant
        # readable without hiding the actual retry policy inside inline exception references.
        retryable_exceptions = tuple(
            getattr(google_exceptions, exception_name)
            for exception_name in GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS
        )
        return Retry(
            predicate=if_exception_type(*retryable_exceptions),
            initial=1.0,
            maximum=max(1.0, float(self._config.read_timeout)),
            multiplier=2.0,
            timeout=max(1.0, per_attempt_timeout * float(self._config.max_retries)),
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
