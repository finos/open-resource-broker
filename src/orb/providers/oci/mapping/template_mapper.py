from __future__ import annotations

"""Template-to-OCI launch payload mapping helpers."""

from math import isfinite
from typing import Any

from orb.providers.oci.services import OCIPricingService


class OCITemplateMapper:
    """Maps provider-agnostic template fields to OCI launch payload fields."""

    REQUIRED_FIELDS = ("template_id", "image_id", "shape", "subnet_id", "compartment_id")
    CAPACITY_TYPES = {"ondemand", "preemptible"}
    # DenseIO is Flex in fixed NVMe-backed increments, not arbitrary CPU/RAM.
    DENSE_FLEX_SIZES: dict[str, dict[float, float]] = {
        "VM.DenseIO.E4.Flex": {8: 128, 16: 256, 32: 512},
        "VM.DenseIO.E5.Flex": {ocpus: ocpus * 12 for ocpus in range(8, 49, 8)},
        "VM.DenseIO.E6.Ax.Flex": {ocpus: ocpus * 12 for ocpus in range(8, 49, 8)},
    }

    @staticmethod
    def _shape_family(shape: str | None) -> str | None:
        if not isinstance(shape, str) or not shape:
            return None
        return shape

    @staticmethod
    def _shape_form_factor(shape: str | None) -> str | None:
        if not isinstance(shape, str) or not shape:
            return None
        if shape.startswith("BM."):
            return "BM"
        if shape.startswith("VM."):
            return "VM"
        return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off", ""}:
                return False
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise ValueError("OCI boolean options must be true or false")

    @classmethod
    def normalize_template_fields(cls, template: dict[str, Any]) -> dict[str, Any]:
        configuration = template.get("configuration")
        if not isinstance(configuration, dict):
            configuration = {}
        provider_config = template.get("provider_config")
        if not isinstance(provider_config, dict):
            provider_config = {}
        configuration = {**provider_config, **configuration}
        metadata = template.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        pricing = template.get("pricing") or provider_config.get("pricing")
        if not isinstance(pricing, dict):
            pricing = {}

        def _get(*keys: str) -> Any:
            for key in keys:
                if template.get(key) is not None:
                    return template.get(key)
            for key in keys:
                if configuration.get(key) is not None:
                    return configuration.get(key)
            for key in keys:
                if metadata.get(key) is not None:
                    return metadata.get(key)
            return None

        subnet_ids = _get("subnet_ids", "subnetIds") or []
        security_group_ids = _get("security_group_ids", "securityGroupIds", "nsg_ids") or []
        machine_types = _get("machine_types") or {}
        inferred_shape = None
        if isinstance(machine_types, dict) and machine_types:
            inferred_shape = next(iter(machine_types.keys()))
        shape = _get("shape", "machine_type", "instance_type", "instanceType") or inferred_shape
        form_factor = _get("form_factor", "formFactor")
        shape_form_factor = cls._shape_form_factor(shape)
        shape_config = _get("shape_config", "shapeConfig") or {}
        if not isinstance(shape_config, dict):
            shape_config = {}

        raw_ocpus = _get("ocpus", "ocpu", "shape_ocpus")
        if raw_ocpus is None:
            raw_ocpus = shape_config.get("ocpus", shape_config.get("OCPUs"))
        raw_memory = _get("memory_gbs", "memoryInGBs", "memory_gb", "memory")
        if raw_memory is None:
            raw_memory = shape_config.get("memoryInGBs", shape_config.get("memory_gbs"))
        ocpus = cls._to_float(raw_ocpus)
        memory_gbs = cls._to_float(raw_memory)
        boot_volume_gbs = cls._to_float(
            _get("boot_volume_gbs", "machine_disk_size_gb", "storage_gbs", "bootVolumeSizeInGBs")
        )

        effective_shape_config = dict(shape_config)
        effective_shape_config.pop("OCPUs", None)
        effective_shape_config.pop("memory_gbs", None)
        if raw_ocpus is not None:
            effective_shape_config["ocpus"] = ocpus if ocpus is not None else raw_ocpus
        if raw_memory is not None:
            effective_shape_config["memoryInGBs"] = (
                memory_gbs if memory_gbs is not None else raw_memory
            )

        capacity_type = str(_get("capacity_type", "capacityType") or "ondemand").lower()
        fallback_to_ondemand = cls._to_bool(
            _get("fallback_to_ondemand", "fallbackToOndemand", "fallback_to_on_demand") or False
        )
        preemptible_preserve_boot_volume = cls._to_bool(
            _get(
                "preemptible_preserve_boot_volume",
                "preemptiblePreserveBootVolume",
                "preserve_boot_volume_on_preemption",
            )
            or False
        )

        shape_family = cls._shape_family(shape)
        cpu_vendor = _get("cpu_vendor", "cpuVendor")
        generation = _get("generation")

        return {
            "template_id": _get("template_id", "templateId"),
            "image_id": _get("machine_image", "image_id", "imageId"),
            "shape": shape,
            "shape_family": shape_family,
            "shape_config": effective_shape_config or None,
            "subnet_id": _get("subnet_id", "subnetId") or (subnet_ids[0] if subnet_ids else None),
            "compartment_id": _get("compartment_id", "compartmentId"),
            "availability_domain": _get("availability_domain", "availabilityDomain"),
            "compute_cluster_id": _get("compute_cluster_id", "computeClusterId"),
            "agent_config": _get("agent_config", "agentConfig"),
            "nsg_ids": security_group_ids,
            "user_data": _get("machine_bootstrap", "user_data", "userData"),
            "ssh_authorized_keys": _get("ssh_authorized_keys", "sshAuthorizedKeys")
            or metadata.get("ssh_authorized_keys"),
            "tags": _get("tags") or {},
            "form_factor": form_factor or shape_form_factor,
            "shape_form_factor": shape_form_factor,
            "cpu_vendor": cpu_vendor,
            "generation": generation,
            "ocpus": ocpus,
            "memory_gbs": memory_gbs,
            "boot_volume_gbs": boot_volume_gbs,
            "capacity_type": capacity_type,
            "fallback_to_ondemand": fallback_to_ondemand,
            "preemptible_preserve_boot_volume": preemptible_preserve_boot_volume,
            "pricing": pricing,
        }

    @classmethod
    def validate_required_fields(cls, template: dict[str, Any]) -> list[str]:
        normalized = cls.normalize_template_fields(template)
        missing = [field for field in cls.REQUIRED_FIELDS if not normalized.get(field)]

        if normalized.get("capacity_type") not in cls.CAPACITY_TYPES:
            missing.append("capacity_type(valid: ondemand|preemptible)")
        if normalized.get("agent_config") is not None and not isinstance(
            normalized["agent_config"], dict
        ):
            missing.append("agent_config(must be an object)")

        shape_form_factor = normalized.get("shape_form_factor")
        requested_form_factor = normalized.get("form_factor")
        if (
            isinstance(requested_form_factor, str)
            and shape_form_factor
            and requested_form_factor.upper() != shape_form_factor.upper()
        ):
            missing.append("form_factor(shape mismatch)")

        # BM shapes are fixed-size and do not accept Flex sizing overrides.
        is_bm = str(shape_form_factor or "").upper() == "BM"
        if is_bm and (
            normalized.get("ocpus") is not None
            or normalized.get("memory_gbs") is not None
            or normalized.get("shape_config")
        ):
            missing.append("bm_shape_does_not_support_flex_sizing")

        shape_config = normalized.get("shape_config") or {}
        for field in ("ocpus", "memoryInGBs"):
            if field in shape_config:
                value = cls._to_float(shape_config[field])
                if value is None or not isfinite(value) or value <= 0:
                    missing.append(f"shape_config.{field}(must be finite and positive)")

        dense_sizes = cls.DENSE_FLEX_SIZES.get(str(normalized.get("shape") or ""))
        if dense_sizes is not None:
            ocpus = normalized.get("ocpus")
            expected_memory = dense_sizes.get(ocpus) if ocpus is not None else None
            if expected_memory is None or normalized.get("memory_gbs") != expected_memory:
                missing.append("dense_flex_requires_supported_ocpu_memory_pair")

        if normalized.get("compute_cluster_id"):
            if not is_bm:
                missing.append("compute_cluster_requires_bare_metal_shape")
            if not normalized.get("availability_domain"):
                missing.append("availability_domain(required for compute_cluster_id)")
            if normalized.get("capacity_type") != "ondemand":
                missing.append("compute_cluster_requires_ondemand_capacity")

        return missing

    @classmethod
    def estimate_hourly_cost(cls, template: dict[str, Any]) -> dict[str, Any]:
        """Compatibility wrapper; pricing logic lives in OCIPricingService."""
        return OCIPricingService.estimate_hourly_cost(template)

    @classmethod
    def build_launch_payload(cls, template: dict[str, Any], display_name: str) -> dict[str, Any]:
        normalized = cls.normalize_template_fields(template)

        payload: dict[str, Any] = {
            "display_name": display_name,
            "compartment_id": normalized["compartment_id"],
            "availability_domain": normalized.get("availability_domain"),
            "shape": normalized["shape"],
            "shape_config": normalized.get("shape_config"),
            "create_vnic_details": {
                "subnet_id": normalized["subnet_id"],
                "nsg_ids": normalized.get("nsg_ids") or [],
            },
            "source_details": {
                "source_type": "image",
                "image_id": normalized["image_id"],
            },
            "metadata": {},
            "freeform_tags": dict(normalized.get("tags") or {}),
            "capacity_type": normalized.get("capacity_type"),
            "boot_volume_gbs": normalized.get("boot_volume_gbs"),
            "pricing_estimate": OCIPricingService.estimate_hourly_cost(template),
            "fallback_to_ondemand": normalized.get("fallback_to_ondemand", False),
        }
        if normalized.get("capacity_type") == "preemptible":
            payload["preemptible_instance_config"] = {
                "preemptionAction": {
                    "type": "TERMINATE",
                    "preserveBootVolume": normalized.get("preemptible_preserve_boot_volume", False),
                }
            }
        if normalized.get("agent_config"):
            payload["agent_config"] = normalized["agent_config"]

        if normalized.get("compute_cluster_id"):
            payload["compute_cluster_id"] = normalized["compute_cluster_id"]
            payload["freeform_tags"] = {
                **payload["freeform_tags"],
                "orb_compute_cluster_id": normalized["compute_cluster_id"],
            }

        if normalized.get("user_data"):
            payload["metadata"]["user_data"] = normalized["user_data"]
        if normalized.get("ssh_authorized_keys"):
            payload["metadata"]["ssh_authorized_keys"] = normalized["ssh_authorized_keys"]

        if not payload["metadata"]:
            payload.pop("metadata")
        if not payload.get("shape_config"):
            payload.pop("shape_config")
        if not payload["create_vnic_details"]["nsg_ids"]:
            payload["create_vnic_details"].pop("nsg_ids")
        if not payload.get("availability_domain"):
            payload.pop("availability_domain")
        if payload.get("capacity_type") not in cls.CAPACITY_TYPES:
            payload["capacity_type"] = "ondemand"
            payload.pop("preemptible_instance_config", None)

        return payload
