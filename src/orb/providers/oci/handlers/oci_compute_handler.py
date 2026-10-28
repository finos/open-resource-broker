"""Provider-local OCI compute operation handler."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from orb.domain.base.ports import LoggingPort
from orb.domain.base.provider_fulfilment import FulfilmentState, ProviderFulfilment
from orb.infrastructure.mocking.dry_run_context import is_dry_run_active
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.oci.async_utils import run_blocking_operation
from orb.providers.oci.cli_client import OCICommandError, run_oci, service_error_summary
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.services import OCIPricingService


class OCIComputeHandler:
    """Handles OCI compute operation contracts and executes real OCI calls via OCI CLI."""

    def __init__(
        self,
        logger: LoggingPort,
        region: str,
        profile: str | None = None,
        credential_source: str | None = None,
        auth_config: dict[str, Any] | None = None,
        *,
        run_command: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._logger = logger
        self._region = region
        self._profile = profile
        self._credential_source = credential_source
        self._auth_config = auth_config or {}
        self._run_command = run_command if run_command is not None else subprocess.run
        self._oci_cli_available = run_command is not None or shutil.which("oci") is not None
        self._region_aliases = {
            "phx": "us-phoenix-1",
            "iad": "us-ashburn-1",
            "fra": "eu-frankfurt-1",
            "lhr": "uk-london-1",
        }

    def _use_live_cli(self, operation: ProviderOperation | None = None) -> bool:
        if is_dry_run_active():
            return False
        if operation is not None and any(
            (operation.context or {}).get(key) for key in ("dry_run", "mock", "allow_mock")
        ):
            return False
        return self._oci_cli_available

    def _use_mock_mode(self, operation: ProviderOperation) -> bool:
        context = operation.context or {}
        return bool(
            is_dry_run_active()
            or context.get("dry_run")
            or context.get("mock")
            or context.get("allow_mock")
        )

    @staticmethod
    def _infer_region_from_ocid(ocid: str | None) -> str | None:
        if not ocid or not isinstance(ocid, str):
            return None
        # Example: ocid1.subnet.oc1.eu-frankfurt-1.xxxxx
        match = re.match(r"^ocid1\.[^.]+\.[^.]+\.([a-z0-9-]+)\..+$", ocid)
        if match:
            return match.group(1)
        return None

    def _run_oci(
        self,
        args: list[str],
        payload: dict[str, Any] | None = None,
        region_override: str | None = None,
        payload_argument: str = "--from-json",
    ) -> dict[str, Any]:
        if region_override and region_override != self._region:
            raise ValueError("OCI operations must use the provider's configured region")
        return run_oci(
            args,
            region=self._region,
            profile=self._profile,
            credential_source=self._credential_source,
            auth_config=self._auth_config,
            runner=self._run_command,
            payload=payload,
            payload_argument=payload_argument,
        )

    _service_error_summary = staticmethod(service_error_summary)

    def _validate_resource_regions(self, resource_ids: list[str | None]) -> None:
        regions = {
            region for ocid in resource_ids if (region := self._infer_region_from_ocid(ocid))
        }
        # Modern OCIDs embed the full region name; older IDs use region keys.
        # Resolve unfamiliar keys through OCI rather than maintaining a region catalog.
        if any("-" not in region and region not in self._region_aliases for region in regions):
            entries = self._run_oci(["iam", "region", "list"]).get("data", [])
            self._region_aliases.update(
                {str(item["key"]).lower(): item["name"] for item in entries}
            )
        configured = self._region_aliases.get(self._region.lower(), self._region.lower())
        if any(self._region_aliases.get(region, region) != configured for region in regions):
            raise ValueError("OCI resource region must match the configured provider region")

    def _resolve_availability_domain(
        self,
        explicit_ad: str | None,
        subnet_id: str | None,
        compartment_id: str | None,
        region_override: str | None = None,
    ) -> str | None:
        if explicit_ad:
            return explicit_ad

        resolved_compartment = compartment_id
        if subnet_id:
            try:
                subnet_response = self._run_oci(
                    ["network", "subnet", "get", "--subnet-id", subnet_id],
                    region_override=region_override,
                )
                subnet_data = subnet_response.get("data", {})
                subnet_ad = subnet_data.get("availability-domain") or subnet_data.get(
                    "availabilityDomain"
                )
                if subnet_ad:
                    return subnet_ad
                resolved_compartment = resolved_compartment or subnet_data.get("compartment-id")
            except Exception as exc:
                self._logger.warning(
                    "Subnet lookup for availability domain failed (%s); using compartment fallback",
                    exc,
                )

        if resolved_compartment:
            ad_list_response = self._run_oci(
                ["iam", "availability-domain", "list", "--compartment-id", resolved_compartment],
                region_override=region_override,
            )
            ad_items = ad_list_response.get("data", [])
            if not isinstance(ad_items, list):
                ad_items = []
            for item in ad_items:
                if not isinstance(item, dict):
                    continue
                ad_name = item.get("name")
                if ad_name:
                    return str(ad_name)

        return None

    def _validate_compute_cluster(self, launch_payload: dict[str, Any]) -> None:
        """Check shared RDMA placement before any nodes in a request are launched."""
        cluster_id = launch_payload.get("compute_cluster_id")
        if not cluster_id:
            return
        cluster = self._run_oci(
            ["compute", "compute-cluster", "get", "--compute-cluster-id", str(cluster_id)],
        ).get("data", {})
        if cluster.get("lifecycle-state") != "ACTIVE":
            raise ValueError("Compute cluster must be ACTIVE before launching RDMA nodes")
        for field in ("compartment_id", "availability_domain"):
            if cluster.get(field.replace("_", "-")) != launch_payload.get(field):
                raise ValueError(f"Compute cluster {field} must match the template")

    def _launch_with_cli(
        self, launch_payload: dict[str, Any], force_ondemand: bool = False
    ) -> dict[str, Any]:
        vnic = launch_payload.get("create_vnic_details", {})
        source = launch_payload.get("source_details", {})
        subnet_id = vnic.get("subnet_id")
        compartment_id = launch_payload.get("compartment_id")
        shape = launch_payload.get("shape")
        image_id = source.get("image_id")
        self._validate_resource_regions(
            [
                subnet_id,
                image_id,
                launch_payload.get("compute_cluster_id"),
                *(vnic.get("nsg_ids") or []),
            ]
        )

        resolved_ad = self._resolve_availability_domain(
            launch_payload.get("availability_domain"),
            subnet_id,
            compartment_id,
        )
        if not resolved_ad:
            raise RuntimeError("Could not resolve availability domain from template/subnet/region")
        if not subnet_id:
            raise RuntimeError("Missing subnet_id for OCI launch")
        if not compartment_id:
            raise RuntimeError("Missing compartment_id for OCI launch")
        if not shape:
            raise RuntimeError("Missing shape for OCI launch")
        if not image_id:
            raise RuntimeError("Missing image_id for OCI launch")

        # The high-level launch command does not expose opc-retry-token.
        # raw-request retains CLI signing and lets us set the idempotency header.
        region = self._region_aliases.get(self._region.lower(), self._region.lower())
        domain = self._auth_config.get("realm_domain_component", "oraclecloud.com")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", region) or not re.fullmatch(
            r"[a-z0-9]+(?:[.-][a-z0-9]+)*", domain
        ):
            raise ValueError("OCI launch requires a full region name and valid realm domain")
        body: dict[str, Any] = {
            "availabilityDomain": str(resolved_ad),
            "compartmentId": str(compartment_id),
            "shape": str(shape),
            "createVnicDetails": {"subnetId": str(subnet_id)},
            "sourceDetails": {"sourceType": "image", "imageId": str(image_id)},
        }
        nsg_ids = vnic.get("nsg_ids")
        if nsg_ids:
            body["createVnicDetails"]["nsgIds"] = nsg_ids
        shape_config = launch_payload.get("shape_config")
        if not isinstance(shape_config, dict) and "Flex" in str(shape):
            shape_config = {"ocpus": 1, "memoryInGBs": 16}
        if isinstance(shape_config, dict) and shape_config:
            body["shapeConfig"] = shape_config
        boot_volume_gbs = launch_payload.get("boot_volume_gbs")
        if boot_volume_gbs is not None:
            body["sourceDetails"]["bootVolumeSizeInGBs"] = int(float(boot_volume_gbs))
        for field, target in {
            "compute_cluster_id": "computeClusterId",
            "display_name": "displayName",
            "metadata": "metadata",
            "agent_config": "agentConfig",
            "freeform_tags": "freeformTags",
        }.items():
            if launch_payload.get(field):
                body[target] = launch_payload[field]
        if launch_payload.get("capacity_type") == "preemptible" and not force_ondemand:
            body["preemptibleInstanceConfig"] = launch_payload.get(
                "preemptible_instance_config"
            ) or {"preemptionAction": {"type": "TERMINATE", "preserveBootVolume": False}}
        retry_token = launch_payload.get("retry_token") or str(uuid4())
        if force_ondemand:
            retry_token = str(uuid5(NAMESPACE_URL, retry_token + ":ondemand"))
        result = self._run_oci(
            [
                "raw-request",
                "--http-method",
                "POST",
                "--target-uri",
                f"https://iaas.{region}.{domain}/20160918/instances",
                "--request-headers",
                json.dumps({"opc-retry-token": retry_token, "content-type": "application/json"}),
                "--no-retry",
                "--connection-timeout",
                "15",
                "--read-timeout",
                "90",
            ],
            payload=body,
            payload_argument="--request-body",
        )
        # raw-request uses API field casing; existing CLI reads use kebab case.
        data = result.get("data", {})
        return {
            "data": {
                **data,
                "lifecycle-state": data.get("lifecycleState") or data.get("lifecycle-state"),
            }
        }

    def _reconcile_launch(self, launch_payload: dict[str, Any]) -> dict[str, Any] | None:
        """Find only this logical launch after an uncertain response, including eventual visibility."""
        for attempt in range(3):
            response = self._run_oci(
                [
                    "compute",
                    "instance",
                    "list",
                    "--compartment-id",
                    launch_payload["compartment_id"],
                    "--display-name",
                    launch_payload["display_name"],
                    "--all",
                ]
            )
            matches = [
                item
                for item in response.get("data", [])
                if (item.get("freeform-tags") or {}).get("orb_launch_id")
                == launch_payload["retry_token"]
                and item.get("display-name") == launch_payload["display_name"]
                and item.get("lifecycle-state") not in {"TERMINATED", "TERMINATING"}
            ]
            if len(matches) == 1 and matches[0].get("id"):
                return {"data": matches[0]}
            if len(matches) > 1:
                raise OCICommandError(
                    "Multiple instances match the OCI launch identity; manual reconciliation required",
                    reason="reconciliation",
                )
            if attempt < 2:
                time.sleep(1)
        return None

    def _launch_idempotently(
        self, launch_payload: dict[str, Any], force_ondemand: bool = False
    ) -> dict[str, Any]:
        try:
            response = self._launch_with_cli(launch_payload, force_ondemand=force_ondemand)
            if not response.get("data", {}).get("id"):
                raise OCICommandError(
                    "OCI launch response missing instance ID", reason="invalid_response"
                )
            return response
        except OCICommandError as exc:
            if exc.ambiguous:
                reconciled = self._reconcile_launch(launch_payload)
                if reconciled is not None:
                    return reconciled
            raise

    async def create_instances(self, operation: ProviderOperation) -> ProviderResult:
        return await run_blocking_operation(self._create_instances, operation)

    def _create_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        template_data = (
            params.get("template_config")
            or params.get("template_data")
            or params.get("template")
            or params.get("configuration")
        )
        if not isinstance(template_data, dict):
            template_data = {}

        merged_template = {**template_data, **params}
        template_id = merged_template.get("template_id") or merged_template.get("templateId")
        if not template_id:
            return ProviderResult.error_result("template_id is required", "MISSING_TEMPLATE_ID")

        requested = int(params.get("count", 1))
        if requested <= 0:
            return ProviderResult.error_result("count must be greater than 0", "INVALID_COUNT")

        try:
            missing_required = OCITemplateMapper.validate_required_fields(merged_template)
        except ValueError as exc:
            return ProviderResult.error_result(str(exc), "INVALID_TEMPLATE")
        if missing_required:
            return ProviderResult.error_result(
                f"Missing required OCI template fields: {', '.join(missing_required)}",
                "MISSING_REQUIRED_FIELDS",
                {"missing_fields": missing_required},
            )

        normalized_template = OCITemplateMapper.normalize_template_fields(merged_template)
        request_id = params.get("request_id") or f"oci-req-{uuid4().hex[:10]}"
        launch_requests = [
            OCITemplateMapper.build_launch_payload(
                merged_template, display_name=f"{template_id}-{idx + 1}"
            )
            for idx in range(requested)
        ]
        for index, launch_request in enumerate(launch_requests):
            token = str(uuid5(NAMESPACE_URL, f"orb:oci:{self._region}:{request_id}:{index}"))
            launch_request["retry_token"] = token
            launch_request["display_name"] = f"{template_id}-{index + 1}-{token[:12]}"
            launch_request["freeform_tags"]["orb_request_id"] = request_id
            launch_request["freeform_tags"]["orb_launch_id"] = token

        instance_ids: list[str] = []
        instances: list[dict[str, Any]] = []
        pricing_estimate = OCIPricingService.estimate_hourly_cost(merged_template)
        effective_capacity_types: list[str] = []
        fallback_attempted_any = False
        if self._use_live_cli(operation):
            try:
                self._validate_resource_regions(
                    [
                        normalized_template.get("subnet_id"),
                        normalized_template.get("image_id"),
                        normalized_template.get("compute_cluster_id"),
                        *(normalized_template.get("nsg_ids") or []),
                    ]
                )
                self._validate_compute_cluster(launch_requests[0])
            except Exception as exc:
                return ProviderResult.error_result(
                    f"OCI placement validation failed: {exc}",
                    "OCI_COMPUTE_CLUSTER_INVALID",
                    {"compute_cluster_id": normalized_template.get("compute_cluster_id")},
                )
            unresolved_launch = None
            try:
                for launch_request in launch_requests:
                    unresolved_launch = {
                        key: launch_request[key]
                        for key in ("retry_token", "display_name", "compartment_id")
                    }
                    requested_capacity_type = str(
                        launch_request.get("capacity_type") or "ondemand"
                    ).lower()
                    fallback_to_ondemand = bool(launch_request.get("fallback_to_ondemand"))
                    fallback_attempted = False
                    effective_capacity_type = requested_capacity_type
                    try:
                        launch_result = self._launch_idempotently(launch_request)
                    except Exception as launch_exc:
                        if (
                            requested_capacity_type == "preemptible"
                            and fallback_to_ondemand
                            and isinstance(launch_exc, OCICommandError)
                            and launch_exc.code
                            in {"OutOfHostCapacity", "OutOfCapacity", "InsufficientCapacity"}
                        ):
                            fallback_attempted = True
                            fallback_attempted_any = True
                            effective_capacity_type = "ondemand"
                            self._logger.warning(
                                "Preemptible launch failed; falling back to on-demand for template %s: %s",
                                template_id,
                                launch_exc,
                            )
                            launch_result = self._launch_idempotently(
                                launch_request,
                                force_ondemand=True,
                            )
                        else:
                            raise
                    data = launch_result.get("data", {})
                    instance_id = data.get("id")
                    if not instance_id:
                        raise RuntimeError("OCI launch response missing instance ID")
                    unresolved_launch = None
                    instance_ids.append(instance_id)
                    effective_capacity_types.append(effective_capacity_type)
                    instances.append(
                        {
                            "instance_id": instance_id,
                            "resource_id": instance_id,
                            "instance_type": data.get("shape") or normalized_template.get("shape"),
                            "image_id": normalized_template.get("image_id"),
                            "status": data.get("lifecycle-state"),
                            "provider_data": {
                                "provider": "oci",
                                "provider_api": "OCICompute",
                                "compartment_id": normalized_template.get("compartment_id"),
                                "subnet_id": normalized_template.get("subnet_id"),
                                "compute_cluster_id": normalized_template.get("compute_cluster_id"),
                                "capacity_type": effective_capacity_type,
                                "pricing_estimate": pricing_estimate,
                                "requested_capacity_type": requested_capacity_type,
                                "fallback_to_ondemand": fallback_to_ondemand,
                                "fallback_attempted": fallback_attempted,
                            },
                        }
                    )
            except Exception as exc:
                self._logger.error("OCI live launch failed for template %s: %s", template_id, exc)
                rollback_ids: list[str] = []
                rollback_errors: list[dict[str, str]] = []
                for instance_id in instance_ids:
                    try:
                        self._run_oci(
                            [
                                "compute",
                                "instance",
                                "terminate",
                                "--instance-id",
                                instance_id,
                                "--preserve-boot-volume",
                                "false",
                                "--force",
                            ]
                        )
                        rollback_ids.append(instance_id)
                    except Exception as rollback_exc:
                        rollback_errors.append(
                            {"instance_id": instance_id, "error": str(rollback_exc)}
                        )
                        self._logger.error(
                            "OCI launch rollback failed for %s: %s", instance_id, rollback_exc
                        )
                return ProviderResult.error_result(
                    f"OCI live launch failed: {exc}",
                    "OCI_LAUNCH_FAILED",
                    {
                        "operation": "create_instances",
                        "provider": "oci",
                        "request_id": request_id,
                        "template_id": template_id,
                        "launched_instance_ids": instance_ids,
                        "rollback_requested_instance_ids": rollback_ids,
                        "rollback_errors": rollback_errors,
                        "fallback_attempted": fallback_attempted_any,
                        "unresolved_launch": unresolved_launch,
                        "region": self._region,
                    },
                )
        elif self._use_mock_mode(operation):
            # Only explicit mock/dry-run requests simulate workers.
            instance_ids = [f"ocid1.instance.oc1..mock{idx + 1}" for idx in range(requested)]
            instances = [
                {
                    "instance_id": instance_id,
                    "resource_id": instance_id,
                    "instance_type": normalized_template.get("shape"),
                    "image_id": normalized_template.get("image_id"),
                    "provider_data": {
                        "provider": "oci",
                        "provider_api": "OCICompute",
                        "compartment_id": normalized_template.get("compartment_id"),
                        "subnet_id": normalized_template.get("subnet_id"),
                        "compute_cluster_id": normalized_template.get("compute_cluster_id"),
                        "capacity_type": normalized_template.get("capacity_type"),
                        "pricing_estimate": pricing_estimate,
                    },
                }
                for instance_id in instance_ids
            ]
            effective_capacity_types = [normalized_template.get("capacity_type", "ondemand")] * len(
                instance_ids
            )
        else:
            return ProviderResult.error_result(
                "OCI CLI is not available; install oci-cli or run in explicit dry-run/mock mode",
                "OCI_CLI_UNAVAILABLE",
                {"operation": "create_instances", "provider": "oci"},
            )

        self._logger.info(
            "OCI create_instances prepared request_id=%s template_id=%s count=%s region=%s",
            request_id,
            template_id,
            requested,
            self._region,
        )
        return ProviderResult.success_result(
            {
                "request_id": request_id,
                "status": "accepted",
                "provider_api": "OCICompute",
                "region": self._region,
                "instance_ids": instance_ids,
                "resource_ids": instance_ids,
                "instances": instances,
                "pricing_estimate": pricing_estimate,
                "capacity_type": normalized_template.get("capacity_type", "ondemand"),
                "effective_capacity_type": effective_capacity_types[0]
                if effective_capacity_types
                else normalized_template.get("capacity_type", "ondemand"),
                "effective_capacity_types": effective_capacity_types,
                "fallback_attempted": fallback_attempted_any,
            },
            {
                "operation": "create_instances",
                "provider": "oci",
                "requires_async_polling": self._use_live_cli(operation),
            },
        )

    def terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        machine_ids = params.get("machine_ids") or params.get("instance_ids") or []
        if not isinstance(machine_ids, list) or not machine_ids:
            return ProviderResult.error_result(
                "machine_ids or instance_ids is required for terminate_instances",
                "MISSING_MACHINE_IDS",
            )

        outcomes: list[dict[str, Any]] = []
        if self._use_live_cli(operation):
            for machine_id in machine_ids:
                try:
                    self._validate_resource_regions([machine_id])
                    self._run_oci(
                        [
                            "compute",
                            "instance",
                            "terminate",
                            "--instance-id",
                            machine_id,
                            "--force",
                        ]
                    )
                    outcomes.append({"machine_id": machine_id, "status": "accepted"})
                except OCICommandError as exc:
                    if exc.status == 404 and exc.code == "NotAuthorizedOrNotFound":
                        # OCI intentionally cannot distinguish missing from inaccessible.
                        # Do not report termination without confirmation.
                        outcomes.append(
                            {
                                "machine_id": machine_id,
                                "status": "not_found_or_not_authorized",
                                "verified_terminated": False,
                            }
                        )
                    else:
                        outcomes.append(
                            {"machine_id": machine_id, "status": "failed", "error": str(exc)}
                        )
                except (RuntimeError, ValueError) as exc:
                    outcomes.append(
                        {"machine_id": machine_id, "status": "failed", "error": str(exc)}
                    )
        elif self._use_mock_mode(operation):
            outcomes = [
                {"machine_id": machine_id, "status": "accepted"} for machine_id in machine_ids
            ]
        else:
            return ProviderResult.error_result(
                "OCI CLI is not available; install oci-cli or run in explicit dry-run/mock mode",
                "OCI_CLI_UNAVAILABLE",
                {"operation": "terminate_instances", "provider": "oci"},
            )
        data = {
            "status": "accepted",
            "results": outcomes,
            "terminated_machine_ids": [
                item["machine_id"] for item in outcomes if item["status"] == "accepted"
            ],
            "provider_api": "OCICompute",
        }
        metadata = {
            "operation": "terminate_instances",
            "provider": "oci",
            "results": outcomes,
            "provider_data": {
                # Successful IDs mean OCI accepted termination; status polling
                # must still confirm that the instances reach TERMINATED.
                "successful_instance_ids": data["terminated_machine_ids"],
                "failed_instance_ids": [
                    item["machine_id"] for item in outcomes if item["status"] != "accepted"
                ],
            },
        }
        if any(item["status"] == "failed" for item in outcomes):
            data["status"] = "failed"
            return ProviderResult(
                success=False,
                data=data,
                error_code="OCI_TERMINATE_FAILED",
                error_message="One or more OCI instances could not be terminated",
                metadata=metadata,
            )
        if any(item["status"] == "not_found_or_not_authorized" for item in outcomes):
            data["status"] = "unconfirmed"
            return ProviderResult(
                success=False,
                data=data,
                error_code="OCI_TERMINATION_UNCONFIRMED",
                error_message="OCI could not confirm termination; verify resource access and state",
                metadata=metadata,
            )
        return ProviderResult.success_result(data, metadata)

    def _get_instance_network(self, machine_id: str) -> dict[str, Any]:
        """Map the primary VNIC into ORB's worker network fields."""
        response = self._run_oci(
            ["compute", "instance", "list-vnics", "--instance-id", machine_id, "--all"]
        )
        vnics = response.get("data", [])
        if not isinstance(vnics, list) or not vnics:
            return {}
        primary = next((vnic for vnic in vnics if vnic.get("is-primary")), vnics[0])
        network = {
            "private_ip": primary.get("private-ip"),
            "public_ip": primary.get("public-ip"),
            "subnet_id": primary.get("subnet-id"),
            "security_group_ids": primary.get("nsg-ids") or [],
        }
        if primary.get("hostname-label") and primary.get("subnet-id"):
            subnet = self._run_oci(
                ["network", "subnet", "get", "--subnet-id", primary["subnet-id"]]
            ).get("data", {})
            domain = subnet.get("subnet-domain-name")
            if domain:
                network["private_dns_name"] = f"{primary['hostname-label']}.{domain}"
        return network

    def get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        machine_ids = params.get("machine_ids") or params.get("instance_ids") or []
        if not isinstance(machine_ids, list) or not machine_ids:
            return ProviderResult.error_result(
                "machine_ids is required for get_instance_status",
                "MISSING_MACHINE_IDS",
            )

        # getattr: direct status calls need not carry a request aggregate.
        request_type = getattr(params.get("request"), "request_type", None)
        # getattr: adapters may pass an enum or a string request type.
        is_return = getattr(request_type, "value", request_type) == "return"
        instances: list[dict[str, Any]] = []
        if self._use_live_cli(operation):
            try:
                for machine_id in machine_ids:
                    self._validate_resource_regions([machine_id])
                    try:
                        response = self._run_oci(
                            ["compute", "instance", "get", "--instance-id", machine_id]
                        )
                        data = response.get("data", {})
                    except OCICommandError as exc:
                        if not (
                            is_return
                            and exc.status == 404
                            and exc.code == "NotAuthorizedOrNotFound"
                        ):
                            raise
                        data = {
                            "id": machine_id,
                            "lifecycle-state": "UNKNOWN",
                            "orb_unverified_absence": True,
                        }
                    state = str(data.get("lifecycle-state", "unknown")).upper()
                    status = {
                        "RUNNING": "running",
                        "PROVISIONING": "pending",
                        "STARTING": "pending",
                        "STOPPING": "stopping",
                        "STOPPED": "stopped",
                        "TERMINATING": "shutting-down",
                        "TERMINATED": "terminated",
                    }.get(state, "unknown")
                    network = self._get_instance_network(machine_id) if state == "RUNNING" else {}
                    cluster_id = data.get("compute-cluster-id")
                    requested_cluster = (data.get("freeform-tags") or {}).get(
                        "orb_compute_cluster_id"
                    )
                    if state == "RUNNING" and requested_cluster and not cluster_id:
                        # GetInstance does not currently expose computeClusterId.
                        # Verify membership through the supported ListInstances filter.
                        members = self._run_oci(
                            [
                                "compute",
                                "instance",
                                "list",
                                "--compartment-id",
                                data["compartment-id"],
                                "--compute-cluster-id",
                                requested_cluster,
                                "--all",
                            ]
                        ).get("data", [])
                        if not any(member.get("id") == machine_id for member in members):
                            raise RuntimeError(
                                "Instance is not a member of its requested compute cluster"
                            )
                        cluster_id = requested_cluster
                    instances.append(
                        {
                            "instance_id": machine_id,
                            "resource_id": machine_id,
                            "status": status,
                            "instance_type": data.get("shape"),
                            "image_id": data.get("image-id")
                            or (data.get("source-details") or {}).get("image-id"),
                            "launch_time": data.get("time-created"),
                            "tags": data.get("freeform-tags") or {},
                            **network,
                            "provider_type": "oci",
                            "provider_api": "OCICompute",
                            "provider_data": {
                                "lifecycle_state": state,
                                "compute_cluster_id": cluster_id,
                                **(
                                    {"termination_verified": False}
                                    if data.get("orb_unverified_absence")
                                    else {}
                                ),
                            },
                        }
                    )
            except Exception as exc:
                self._logger.error("OCI live status check failed: %s", exc)
                return ProviderResult.error_result(
                    f"OCI live status check failed: {exc}",
                    "OCI_STATUS_FAILED",
                    {
                        "operation": "get_instance_status",
                        "provider": "oci",
                        "machine_ids": machine_ids,
                    },
                )
        elif self._use_mock_mode(operation):
            instances = [
                {
                    "instance_id": machine_id,
                    "resource_id": machine_id,
                    "status": "unknown",
                    "provider_type": "oci",
                    "provider_api": "OCICompute",
                }
                for machine_id in machine_ids
            ]
        else:
            return ProviderResult.error_result(
                "OCI CLI is not available; install oci-cli or run in explicit dry-run/mock mode",
                "OCI_CLI_UNAVAILABLE",
                {"operation": "get_instance_status", "provider": "oci"},
            )
        target = params.get("requested_count")
        if target is None:
            # getattr: direct status calls omit the optional request aggregate.
            target = getattr(params.get("request"), "requested_count", len(machine_ids))
        running = sum(instance["status"] == "running" for instance in instances)
        failed = sum(
            instance["status"] in {"stopped", "terminated", "failed"} for instance in instances
        )
        pending = max(int(target) - running - failed, 0)
        fulfilment_state: FulfilmentState = "in_progress"
        if running >= target:
            fulfilment_state = "fulfilled"
        elif failed and not pending:
            fulfilment_state = "partial" if running else "failed"
        fulfilment = ProviderFulfilment(
            state=fulfilment_state,
            message=f"OCI: {running}/{target} instances running",
            target_units=target,
            fulfilled_units=running,
            running_count=running,
            pending_count=pending,
            failed_count=failed,
        )
        if is_return:
            terminated = sum(instance["status"] == "terminated" for instance in instances)
            fulfilment = ProviderFulfilment(
                state="fulfilled" if terminated >= target else "in_progress",
                message=f"OCI: {terminated}/{target} instances terminated",
                target_units=target,
                fulfilled_units=terminated,
                running_count=running,
                pending_count=max(int(target) - terminated, 0),
                failed_count=0,
            )
        return ProviderResult.success_result(
            {"instances": instances},
            {
                "operation": "get_instance_status",
                "provider": "oci",
                "provider_fulfilment": fulfilment,
            },
        )

    async def describe_resource_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        resource_ids = params.get("resource_ids") or []
        if not isinstance(resource_ids, list) or not resource_ids:
            return ProviderResult.error_result(
                "resource_ids is required for describe_resource_instances",
                "MISSING_RESOURCE_IDS",
            )

        return await run_blocking_operation(
            self.get_instance_status,
            ProviderOperation(
                operation_type=operation.operation_type,
                parameters={**params, "instance_ids": resource_ids},
                context=operation.context,
            ),
        )
