"""Transparent OCI pricing estimate service."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class OCIPricingService:
    """Builds transparent static pricing estimates for OCI templates."""

    DEFAULT_PRICING: dict[str, Any] = {
        "currency": "USD",
        "storage_gb_hourly": 0.00006,
        "shape_family_rates": {
            "VM.Standard.E6.Flex": {"ocpu_hourly": 0.04, "memory_gb_hourly": 0.004},
            "VM.Standard.E5.Flex": {"ocpu_hourly": 0.04, "memory_gb_hourly": 0.004},
            "VM.Standard3.Flex": {"ocpu_hourly": 0.043, "memory_gb_hourly": 0.0043},
        },
        "preemptible_discount": 0.5,
    }

    RATE_SOURCE_STATIC = "oci_static_reference_rates"
    RATE_SOURCE_OVERRIDE = "template_pricing_override"

    @classmethod
    def estimate_hourly_cost(cls, template: dict[str, Any]) -> dict[str, Any]:
        """Estimate hourly OCI cost using transparent static rates and template overrides."""
        from orb.providers.oci.mapping import OCITemplateMapper

        normalized = OCITemplateMapper.normalize_template_fields(template)
        pricing_cfg, has_overrides = cls._pricing_config(normalized.get("pricing") or {})
        warnings: list[str] = []

        shape_family = normalized.get("shape_family")
        shape_rates = {}
        if shape_family:
            shape_rates = (pricing_cfg.get("shape_family_rates") or {}).get(shape_family, {})
        if not shape_family:
            warnings.append("No OCI shape was provided; compute pricing is unknown.")
        elif not shape_rates:
            warnings.append(
                f"No OCI reference rate is configured for shape {shape_family}; compute pricing is unknown."
            )

        ocpu_rate = cls._to_float(shape_rates.get("ocpu_hourly")) if shape_rates else None
        memory_rate = cls._to_float(shape_rates.get("memory_gb_hourly")) if shape_rates else None
        storage_rate = cls._to_float(pricing_cfg.get("storage_gb_hourly"))

        ocpus = cls._to_float(normalized.get("ocpus")) or 0.0
        memory_gbs = cls._to_float(normalized.get("memory_gbs")) or 0.0
        storage_gbs = cls._to_float(normalized.get("boot_volume_gbs")) or 0.0

        storage_hourly = round(storage_gbs * storage_rate, 6) if storage_rate is not None else None
        compute_hourly: float | None
        total_hourly: float | None
        if ocpu_rate is None or memory_rate is None:
            compute_hourly = None
            total_hourly = None
        else:
            compute_hourly = (ocpus * ocpu_rate) + (memory_gbs * memory_rate)
            total_hourly = compute_hourly + (storage_hourly or 0.0)

        capacity_type = normalized.get("capacity_type")
        preemptible_discount = None
        if capacity_type == "preemptible":
            preemptible_discount = cls._to_float(pricing_cfg.get("preemptible_discount"))
            if preemptible_discount is None:
                warnings.append("No OCI preemptible discount is configured.")
            else:
                preemptible_discount = max(0.0, min(1.0, preemptible_discount))
                if compute_hourly is not None:
                    compute_hourly *= preemptible_discount
                if total_hourly is not None:
                    total_hourly *= preemptible_discount

        confidence = "unknown" if total_hourly is None else "medium"
        return {
            "currency": pricing_cfg.get("currency", "USD"),
            "capacity_type": capacity_type,
            "shape_family": shape_family,
            "compute_hourly": cls._round_or_none(compute_hourly),
            "storage_hourly": storage_hourly,
            "total_hourly": cls._round_or_none(total_hourly),
            "estimated": True,
            "confidence": confidence,
            "warnings": warnings,
            "inputs": {
                "ocpus": ocpus,
                "memory_gbs": memory_gbs,
                "storage_gbs": storage_gbs,
            },
            "rates": {
                "ocpu_hourly": ocpu_rate,
                "memory_gb_hourly": memory_rate,
                "storage_gb_hourly": storage_rate,
                "preemptible_discount": preemptible_discount,
            },
            "rate_source": cls.RATE_SOURCE_OVERRIDE if has_overrides else cls.RATE_SOURCE_STATIC,
        }

    @classmethod
    def _pricing_config(cls, pricing: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        cfg = deepcopy(cls.DEFAULT_PRICING)
        if not isinstance(pricing, dict) or not pricing:
            return cfg, False

        overrides = pricing.get("overrides") if isinstance(pricing.get("overrides"), dict) else pricing
        if not isinstance(overrides, dict) or not overrides:
            return cfg, False

        for key, value in overrides.items():
            if key == "shape_family_rates" and isinstance(value, dict):
                shape_rates = cfg.setdefault("shape_family_rates", {})
                for shape, rates in value.items():
                    if isinstance(rates, dict):
                        shape_rates.setdefault(shape, {}).update(rates)
            else:
                cfg[key] = value
        return cfg, True

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _round_or_none(value: float | None) -> float | None:
        if value is None:
            return None
        return round(value, 6)
