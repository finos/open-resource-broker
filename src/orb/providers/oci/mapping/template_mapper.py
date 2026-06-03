from __future__ import annotations

"""Template-to-OCI launch payload mapping helpers."""

from typing import Any


class OCITemplateMapper:
    """Maps provider-agnostic template fields to OCI launch payload fields."""

    REQUIRED_FIELDS = ("template_id", "image_id", "shape", "subnet_id", "compartment_id")
    CAPACITY_TYPES = {"ondemand", "preemptible"}
    # OCI pricing is region-stable in this integration. Values are reference defaults
    # and can be overridden per template under pricing.overrides.
    DEFAULT_PRICING = {
        "currency": "USD",
        "storage_gb_hourly": 0.00006,
        "shape_family_rates": {
            "VM.Standard.E6.Flex": {"ocpu_hourly": 0.04, "memory_gb_hourly": 0.004},
            "VM.Standard.E5.Flex": {"ocpu_hourly": 0.04, "memory_gb_hourly": 0.004},
            "VM.Standard3.Flex": {"ocpu_hourly": 0.043, "memory_gb_hourly": 0.0043},
        },
        "preemptible_discount": 0.5,
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

    @classmethod
    def normalize_template_fields(cls, template: dict[str, Any]) -> dict[str, Any]:
        configuration = template.get("configuration")
        if not isinstance(configuration, dict):
            configuration = {}
        metadata = template.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        pricing = template.get("pricing")
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
        security_group_ids = (
            _get("security_group_ids", "securityGroupIds", "nsg_ids")
            or []
        )
        machine_types = _get("machine_types") or {}
        inferred_shape = None
        if isinstance(machine_types, dict) and machine_types:
            inferred_shape = next(iter(machine_types.keys()))
        shape = _get("shape", "instance_type", "instanceType") or inferred_shape
        form_factor = _get("form_factor", "formFactor")
        shape_form_factor = cls._shape_form_factor(shape)
        shape_config = _get("shape_config", "shapeConfig") or {}
        if not isinstance(shape_config, dict):
            shape_config = {}

        ocpus = cls._to_float(
            _get("ocpus", "ocpu", "shape_ocpus")
            or shape_config.get("ocpus")
            or shape_config.get("OCPUs")
        )
        memory_gbs = cls._to_float(
            _get("memory_gbs", "memoryInGBs", "memory_gb", "memory")
            or shape_config.get("memoryInGBs")
            or shape_config.get("memory_gbs")
        )
        boot_volume_gbs = cls._to_float(
            _get("storage_gbs", "boot_volume_gbs", "bootVolumeSizeInGBs")
        )

        effective_shape_config = dict(shape_config)
        if ocpus is not None:
            effective_shape_config["ocpus"] = ocpus
        if memory_gbs is not None:
            effective_shape_config["memoryInGBs"] = memory_gbs

        capacity_type = str(_get("capacity_type", "capacityType") or "ondemand").lower()
        fallback_to_ondemand = bool(
            _get("fallback_to_ondemand", "fallbackToOndemand", "fallback_to_on_demand") or False
        )
        preemptible_preserve_boot_volume = bool(
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
            "image_id": _get("image_id", "imageId"),
            "shape": shape,
            "shape_family": shape_family,
            "shape_config": effective_shape_config or None,
            "subnet_id": _get("subnet_id", "subnetId")
            or (subnet_ids[0] if subnet_ids else None),
            "compartment_id": _get("compartment_id", "compartmentId"),
            "availability_domain": _get("availability_domain", "availabilityDomain"),
            "nsg_ids": security_group_ids,
            "user_data": _get("user_data", "userData"),
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

        return missing

    @classmethod
    def estimate_hourly_cost(cls, template: dict[str, Any]) -> dict[str, Any]:
        normalized = cls.normalize_template_fields(template)
        pricing_cfg = dict(cls.DEFAULT_PRICING)
        pricing_overrides = normalized.get("pricing") or {}
        if isinstance(pricing_overrides, dict):
            if isinstance(pricing_overrides.get("overrides"), dict):
                pricing_cfg.update(pricing_overrides["overrides"])
            else:
                pricing_cfg.update(pricing_overrides)

        shape_family = normalized.get("shape_family")
        shape_rates = (
            (pricing_cfg.get("shape_family_rates") or {}).get(shape_family, {})
            if shape_family
            else {}
        )
        ocpu_rate = cls._to_float(shape_rates.get("ocpu_hourly")) or 0.0
        memory_rate = cls._to_float(shape_rates.get("memory_gb_hourly")) or 0.0
        storage_rate = cls._to_float(pricing_cfg.get("storage_gb_hourly")) or 0.0

        ocpus = cls._to_float(normalized.get("ocpus")) or 0.0
        memory_gbs = cls._to_float(normalized.get("memory_gbs")) or 0.0
        storage_gbs = cls._to_float(normalized.get("boot_volume_gbs")) or 0.0

        compute_hourly = (ocpus * ocpu_rate) + (memory_gbs * memory_rate)
        storage_hourly = storage_gbs * storage_rate
        total_hourly = compute_hourly + storage_hourly

        capacity_type = normalized.get("capacity_type")
        discount = 1.0
        if capacity_type == "preemptible":
            discount_value = cls._to_float(pricing_cfg.get("preemptible_discount"))
            if discount_value is not None:
                discount = max(0.0, min(1.0, discount_value))
                total_hourly = total_hourly * discount
                compute_hourly = compute_hourly * discount

        return {
            "currency": pricing_cfg.get("currency", "USD"),
            "capacity_type": capacity_type,
            "shape_family": shape_family,
            "compute_hourly": round(compute_hourly, 6),
            "storage_hourly": round(storage_hourly, 6),
            "total_hourly": round(total_hourly, 6),
            "inputs": {
                "ocpus": ocpus,
                "memory_gbs": memory_gbs,
                "storage_gbs": storage_gbs,
            },
            "rates": {
                "ocpu_hourly": ocpu_rate,
                "memory_gb_hourly": memory_rate,
                "storage_gb_hourly": storage_rate,
                "preemptible_discount": discount if capacity_type == "preemptible" else None,
            },
            "rate_source": "oci_global_reference_rates",
        }

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
            "freeform_tags": normalized.get("tags") or {},
            "capacity_type": normalized.get("capacity_type"),
            "boot_volume_gbs": normalized.get("boot_volume_gbs"),
            "pricing_estimate": cls.estimate_hourly_cost(template),
            "fallback_to_ondemand": normalized.get("fallback_to_ondemand", False),
        }
        if normalized.get("capacity_type") == "preemptible":
            payload["preemptible_instance_config"] = {
                "preemptionAction": {
                    "type": "TERMINATE",
                    "preserveBootVolume": normalized.get("preemptible_preserve_boot_volume", False),
                }
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
