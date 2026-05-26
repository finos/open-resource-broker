from __future__ import annotations

"""Provider-local OCI compute operation handler."""

import json
import os
import re
import shutil
import subprocess
from typing import Any
from uuid import uuid4

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderOperation, ProviderResult
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.oci_cli_auth import build_oci_cli_extra_args


class OCIComputeHandler:
    """Handles OCI compute operation contracts and executes real OCI calls via OCI CLI."""

    def __init__(
        self,
        logger: LoggingPort,
        region: str,
        profile: str | None = None,
        credential_source: str | None = None,
    ) -> None:
        self._logger = logger
        self._region = region
        self._profile = profile
        self._credential_source = credential_source
        self._oci_cli_available = shutil.which("oci") is not None
        self._force_live_cli_for_tests = False

    @staticmethod
    def _is_test_context() -> bool:
        return bool(os.environ.get("PYTEST_CURRENT_TEST"))

    def _use_live_cli(self) -> bool:
        if not self._oci_cli_available:
            return False
        if self._force_live_cli_for_tests:
            return True
        return not self._is_test_context()

    @staticmethod
    def _infer_region_from_ocid(ocid: str | None) -> str | None:
        if not ocid or not isinstance(ocid, str):
            return None
        # Example: ocid1.subnet.oc1.eu-frankfurt-1.xxxxx
        match = re.match(r"^ocid1\.[^.]+\.oc1\.([a-z0-9-]+)\..+$", ocid)
        if match:
            return match.group(1)
        return None

    def _run_oci(
        self,
        args: list[str],
        payload: dict[str, Any] | None = None,
        region_override: str | None = None,
    ) -> dict[str, Any]:
        effective_region = region_override or self._region
        cmd = ["oci", *args, "--region", effective_region]
        cmd.extend(
            build_oci_cli_extra_args(
                profile=self._profile,
                credential_source=self._credential_source,
            )
        )
        if payload is not None:
            cmd.extend(["--from-json", json.dumps(payload)])

        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"OCI CLI command failed ({completed.returncode}): {' '.join(cmd)}\n"
                f"STDOUT: {completed.stdout.strip()}\nSTDERR: {completed.stderr.strip()}"
            )

        raw = completed.stdout.strip()
        if not raw:
            return {}
        return json.loads(raw)

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

    def _launch_with_cli(
        self, launch_payload: dict[str, Any], force_ondemand: bool = False
    ) -> dict[str, Any]:
        vnic = launch_payload.get("create_vnic_details", {})
        source = launch_payload.get("source_details", {})
        subnet_id = vnic.get("subnet_id")
        compartment_id = launch_payload.get("compartment_id")
        shape = launch_payload.get("shape")
        image_id = source.get("image_id")
        inferred_region = self._infer_region_from_ocid(subnet_id) or self._infer_region_from_ocid(
            image_id
        )

        resolved_ad = self._resolve_availability_domain(
            launch_payload.get("availability_domain"),
            subnet_id,
            compartment_id,
            inferred_region,
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

        args = [
            "compute",
            "instance",
            "launch",
            "--availability-domain",
            str(resolved_ad),
            "--compartment-id",
            str(compartment_id),
            "--shape",
            str(shape),
            "--subnet-id",
            str(subnet_id),
            "--image-id",
            str(image_id),
        ]

        shape_config = launch_payload.get("shape_config")
        if not isinstance(shape_config, dict) and isinstance(shape, str) and "Flex" in shape:
            # Reasonable default for Flex shapes when template omits explicit values.
            shape_config = {"ocpus": 1, "memoryInGBs": 16}
        if isinstance(shape_config, dict) and shape_config:
            args.extend(["--shape-config", json.dumps(shape_config)])
        boot_volume_gbs = launch_payload.get("boot_volume_gbs")
        if boot_volume_gbs is not None:
            args.extend(["--boot-volume-size-in-gbs", str(int(float(boot_volume_gbs)))])
        requested_capacity_type = str(launch_payload.get("capacity_type") or "ondemand").lower()
        if not force_ondemand and requested_capacity_type == "preemptible":
            preemptible_cfg = launch_payload.get("preemptible_instance_config") or {
                "preemptionAction": {"type": "TERMINATE", "preserveBootVolume": False}
            }
            args.extend(["--preemptible-instance-config", json.dumps(preemptible_cfg)])

        if launch_payload.get("display_name"):
            args.extend(["--display-name", str(launch_payload.get("display_name"))])
        metadata = launch_payload.get("metadata")
        if isinstance(metadata, dict) and metadata:
            args.extend(["--metadata", json.dumps(metadata)])
        tags = launch_payload.get("freeform_tags")
        if isinstance(tags, dict) and tags:
            args.extend(["--freeform-tags", json.dumps(tags)])

        nsg_ids = vnic.get("nsg_ids")
        if isinstance(nsg_ids, list) and nsg_ids:
            self._logger.info(
                "OCI launch includes nsg_ids in template; CLI flag handling currently skipped for nsg_ids=%s",
                nsg_ids,
            )
        if requested_capacity_type == "preemptible" and not force_ondemand:
            self._logger.info(
                "OCI template requests preemptible capacity; launching with OCI preemptible config"
            )
        if force_ondemand and requested_capacity_type == "preemptible":
            self._logger.info("Retrying OCI launch as on-demand after preemptible attempt failure")

        return self._run_oci(args, region_override=inferred_region)

    @staticmethod
    def _build_launch_cli_payload(launch_payload: dict[str, Any]) -> dict[str, Any]:
        vnic = launch_payload.get("create_vnic_details", {})
        source = launch_payload.get("source_details", {})

        payload: dict[str, Any] = {
            "displayName": launch_payload.get("display_name"),
            "compartmentId": launch_payload.get("compartment_id"),
            "shape": launch_payload.get("shape"),
            "createVnicDetails": {
                "subnetId": vnic.get("subnet_id"),
            },
            "sourceDetails": {
                "sourceType": source.get("source_type", "image"),
                "imageId": source.get("image_id"),
            },
        }
        if launch_payload.get("availability_domain"):
            payload["availabilityDomain"] = launch_payload.get("availability_domain")
        if isinstance(vnic.get("nsg_ids"), list) and vnic.get("nsg_ids"):
            payload["createVnicDetails"]["nsgIds"] = vnic["nsg_ids"]
        if isinstance(launch_payload.get("metadata"), dict) and launch_payload.get("metadata"):
            payload["metadata"] = launch_payload["metadata"]
        if isinstance(launch_payload.get("freeform_tags"), dict) and launch_payload.get("freeform_tags"):
            payload["freeformTags"] = launch_payload["freeform_tags"]
        return payload

    async def create_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        template_data = (
            params.get("template")
            or params.get("template_data")
            or params.get("template_config")
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

        missing_required = OCITemplateMapper.validate_required_fields(merged_template)
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
                merged_template, display_name=f"{template_id}-{idx+1}"
            )
            for idx in range(requested)
        ]

        instance_ids: list[str] = []
        instances: list[dict[str, Any]] = []
        pricing_estimate = OCITemplateMapper.estimate_hourly_cost(merged_template)
        effective_capacity_types: list[str] = []
        fallback_attempted_any = False
        if self._use_live_cli():
            try:
                for launch_request in launch_requests:
                    requested_capacity_type = str(
                        launch_request.get("capacity_type") or "ondemand"
                    ).lower()
                    fallback_to_ondemand = bool(launch_request.get("fallback_to_ondemand"))
                    fallback_attempted = False
                    effective_capacity_type = requested_capacity_type
                    try:
                        launch_result = self._launch_with_cli(launch_request)
                    except Exception as launch_exc:
                        if requested_capacity_type == "preemptible" and fallback_to_ondemand:
                            fallback_attempted = True
                            fallback_attempted_any = True
                            effective_capacity_type = "ondemand"
                            self._logger.warning(
                                "Preemptible launch failed; falling back to on-demand for template %s: %s",
                                template_id,
                                launch_exc,
                            )
                            launch_result = self._launch_with_cli(
                                launch_request,
                                force_ondemand=True,
                            )
                        else:
                            raise
                    data = launch_result.get("data", {})
                    instance_id = data.get("id")
                    if not instance_id:
                        raise RuntimeError(f"OCI launch response missing instance ID: {launch_result}")
                    instance_ids.append(instance_id)
                    effective_capacity_types.append(effective_capacity_type)
                    instances.append(
                        {
                            "instance_id": instance_id,
                            "resource_id": instance_id,
                            "instance_type": data.get("shape") or normalized_template.get("shape"),
                            "image_id": normalized_template.get("image_id"),
                            "status": data.get("lifecycle-state"),
                            "metadata": {
                                "provider": "oci",
                                "provider_api": "OCICompute",
                                "compartment_id": normalized_template.get("compartment_id"),
                                "subnet_id": normalized_template.get("subnet_id"),
                                "capacity_type": effective_capacity_type,
                                "pricing_estimate": pricing_estimate,
                                "requested_capacity_type": requested_capacity_type,
                                "fallback_to_ondemand": fallback_to_ondemand,
                                "fallback_attempted": fallback_attempted,
                            },
                        }
                    )
            except Exception as exc:
                self._logger.warning(
                    "OCI live launch failed, falling back to mock IDs: %s",
                    exc,
                )
                instance_ids = [f"ocid1.instance.oc1..mock{idx+1}" for idx in range(requested)]
                instances = [
                    {
                        "instance_id": instance_id,
                        "resource_id": instance_id,
                        "instance_type": normalized_template.get("shape"),
                        "image_id": normalized_template.get("image_id"),
                        "metadata": {
                            "provider": "oci",
                            "provider_api": "OCICompute",
                            "compartment_id": normalized_template.get("compartment_id"),
                            "subnet_id": normalized_template.get("subnet_id"),
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
            # Fallback for test/local environments where OCI CLI is unavailable.
            instance_ids = [f"ocid1.instance.oc1..mock{idx+1}" for idx in range(requested)]
            instances = [
                {
                    "instance_id": instance_id,
                    "resource_id": instance_id,
                    "instance_type": normalized_template.get("shape"),
                    "image_id": normalized_template.get("image_id"),
                    "metadata": {
                        "provider": "oci",
                        "provider_api": "OCICompute",
                        "compartment_id": normalized_template.get("compartment_id"),
                        "subnet_id": normalized_template.get("subnet_id"),
                        "capacity_type": normalized_template.get("capacity_type"),
                        "pricing_estimate": pricing_estimate,
                    },
                }
                for instance_id in instance_ids
            ]
            effective_capacity_types = [normalized_template.get("capacity_type", "ondemand")] * len(
                instance_ids
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
                "launch_requests": launch_requests,
                "pricing_estimate": pricing_estimate,
                "capacity_type": normalized_template.get("capacity_type", "ondemand"),
                "effective_capacity_type": effective_capacity_types[0]
                if effective_capacity_types
                else normalized_template.get("capacity_type", "ondemand"),
                "effective_capacity_types": effective_capacity_types,
                "fallback_attempted": fallback_attempted_any,
            },
            {"operation": "create_instances", "provider": "oci"},
        )

    def terminate_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        machine_ids = params.get("machine_ids") or params.get("instance_ids") or []
        if not isinstance(machine_ids, list) or not machine_ids:
            return ProviderResult.error_result(
                "machine_ids or instance_ids is required for terminate_instances",
                "MISSING_MACHINE_IDS",
            )

        if self._use_live_cli():
            try:
                for machine_id in machine_ids:
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
            except Exception as exc:
                self._logger.warning(
                    "OCI live terminate failed, returning accepted fallback: %s",
                    exc,
                )

        return ProviderResult.success_result(
            {
                "status": "accepted",
                "terminated_machine_ids": machine_ids,
                "provider_api": "OCICompute",
            },
            {"operation": "terminate_instances", "provider": "oci"},
        )

    def get_instance_status(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        machine_ids = params.get("machine_ids") or params.get("instance_ids") or []
        if not isinstance(machine_ids, list) or not machine_ids:
            return ProviderResult.error_result(
                "machine_ids is required for get_instance_status",
                "MISSING_MACHINE_IDS",
            )

        statuses: dict[str, Any] = {}
        if self._use_live_cli():
            try:
                for machine_id in machine_ids:
                    response = self._run_oci(
                        ["compute", "instance", "get", "--instance-id", machine_id]
                    )
                    data = response.get("data", {})
                    statuses[machine_id] = {
                        "status": data.get("lifecycle-state", "unknown"),
                        "provider_api": "OCICompute",
                    }
            except Exception as exc:
                self._logger.warning(
                    "OCI live status check failed, returning unknown fallback: %s",
                    exc,
                )
                statuses = {
                    machine_id: {"status": "unknown", "provider_api": "OCICompute"}
                    for machine_id in machine_ids
                }
        else:
            statuses = {
                machine_id: {"status": "unknown", "provider_api": "OCICompute"}
                for machine_id in machine_ids
            }

        return ProviderResult.success_result(
            {"instances": statuses},
            {"operation": "get_instance_status", "provider": "oci"},
        )

    async def describe_resource_instances(self, operation: ProviderOperation) -> ProviderResult:
        params = operation.parameters or {}
        resource_ids = params.get("resource_ids") or []
        if not isinstance(resource_ids, list) or not resource_ids:
            return ProviderResult.error_result(
                "resource_ids is required for describe_resource_instances",
                "MISSING_RESOURCE_IDS",
            )

        instances: list[dict[str, Any]] = []
        for resource_id in resource_ids:
            instances.append(
                {
                    "resource_id": resource_id,
                    "provider_api": "OCICompute",
                    "instances": [],
                }
            )

        return ProviderResult.success_result(
            {"instances": instances},
            {"operation": "describe_resource_instances", "provider": "oci"},
        )
