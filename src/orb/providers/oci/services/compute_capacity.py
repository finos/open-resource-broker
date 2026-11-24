"""Resolve OCI shape capacity without choosing a scheduler's CPU-count policy.

Shape facts: https://docs.oracle.com/en-us/iaas/Content/Compute/References/computeshapes.htm
Reviewed 2026-10-28. Counts describe the standard shape topology, before guest or
BIOS changes. This offline helper does not validate regional availability or all
of OCI's allocation limits, and is not yet connected to HostFactory formatting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import floor, isfinite
from typing import Any, Literal

from orb.providers.oci.mapping.template_mapper import OCITemplateMapper


@dataclass(frozen=True)
class OCIComputeCapacity:
    """Nominal hardware facts, independent of Symphony's core/thread policy."""

    shape: str
    architecture: Literal["x86_64", "aarch64"]
    ocpus: float
    physical_cores: float
    logical_cpus: float
    memory_gbs: float
    memory_mib: int


# Explicit shape identities prevent a new or misspelled family from inheriting
# another processor's topology. Only the shapes in the packaged catalog appear.
_FLEX_SHAPES = {
    "VM.Standard.E5.Flex",
    "VM.Standard.E6.Flex",
    "VM.Standard.E6.Ax.Flex",
    "VM.Standard3.Flex",
    "VM.Standard4.Ax.Flex",
    "VM.Optimized3.Flex",
    "VM.Standard.A1.Flex",
    "VM.Standard.A2.Flex",
    "VM.Standard.A4.Flex",
    "VM.Standard.A4.Ax.Flex",
    "VM.DenseIO.E5.Flex",
    "VM.DenseIO.E6.Ax.Flex",
}

# OCPUs and memory in OCI's memoryInGBs units; never infer RAM from a shape suffix.
_FIXED_CAPACITY = {
    "BM.Standard.E5.192": (192, 2304),
    "BM.Standard.E6.256": (256, 3072),
    "BM.Standard.E6.Ax.192": (192, 1536),
    "BM.Standard3.64": (64, 1024),
    "BM.Standard4.Ax.120": (120, 1152),
    "BM.Standard.A1.160": (160, 1024),
    "BM.Standard.A4.48": (48, 768),
    "BM.Standard.A4.Ax.48": (48, 768),
    "BM.DenseIO.E5.128": (128, 1536),
    "BM.DenseIO.E6.Ax.192": (192, 2304),
    "BM.Optimized3.36": (36, 512),
}

_ARM_A1 = {"VM.Standard.A1.Flex", "BM.Standard.A1.160"}
_ARM_TWO_CORES = {
    "VM.Standard.A2.Flex",
    "VM.Standard.A4.Flex",
    "VM.Standard.A4.Ax.Flex",
    "BM.Standard.A4.48",
    "BM.Standard.A4.Ax.48",
}


def _raw_field(template: Mapping[str, Any], *keys: str) -> Any:
    """Find an effective raw field before launch normalization coerces numbers.

    Keep the launch mapper's source/alias precedence, including configuration
    overriding provider_config. Raw inspection only protects against bools and
    malformed containers that normalization otherwise converts or discards.
    """
    provider = template.get("provider_config")
    configuration = template.get("configuration")
    configuration = {
        **(provider if isinstance(provider, dict) else {}),
        **(configuration if isinstance(configuration, dict) else {}),
    }
    metadata = template.get("metadata")
    for source in (template, configuration, metadata if isinstance(metadata, dict) else {}):
        for key in keys:
            value = source.get(key)
            if value is not None:
                return value
    return None


def _positive_number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{field} must be a finite positive number") from error
    if not isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return number


def resolve_compute_capacity(template: Mapping[str, Any]) -> OCIComputeCapacity | None:
    """Return known capacity from resolved launch sizing, without cloud calls.

    Missing sizing and unsupported/ambiguous shapes return ``None``. Malformed
    sizing raises ``ValueError`` instead of advertising a guessed default.
    Fractional positive inputs remain numeric facts; OCI still enforces shape
    allocation limits when launching. Existing normalizer aliases and precedence
    determine the values used, and the caller's template is never mutated.
    """
    raw_shape_config = _raw_field(template, "shape_config", "shapeConfig")
    if raw_shape_config is not None and not isinstance(raw_shape_config, dict):
        raise ValueError("shape_config must be an object")
    shape_config = raw_shape_config or {}
    raw_ocpus = _raw_field(template, "ocpus", "ocpu", "shape_ocpus")
    if raw_ocpus is None:
        raw_ocpus = shape_config.get("ocpus", shape_config.get("OCPUs"))
    raw_memory = _raw_field(template, "memory_gbs", "memoryInGBs", "memory_gb", "memory")
    if raw_memory is None:
        raw_memory = shape_config.get("memoryInGBs", shape_config.get("memory_gbs"))
    _positive_number(raw_ocpus, "ocpus")
    _positive_number(raw_memory, "memory_gbs")

    explicit_shape = _raw_field(template, "shape", "machine_type", "instance_type", "instanceType")
    machine_types = _raw_field(template, "machine_types")
    if not explicit_shape and isinstance(machine_types, dict) and len(machine_types) > 1:
        return None

    normalized = OCITemplateMapper.normalize_template_fields(dict(template))
    shape = normalized["shape"]
    if not isinstance(shape, str):
        return None
    if shape in _FIXED_CAPACITY:
        if raw_ocpus is not None or raw_memory is not None or shape_config:
            raise ValueError("Bare metal shapes do not support Flex sizing overrides")
        ocpus, memory_gbs = (float(value) for value in _FIXED_CAPACITY[shape])
    elif shape in _FLEX_SHAPES:
        ocpus = normalized["ocpus"]
        memory_gbs = normalized["memory_gbs"]
        if ocpus is None or memory_gbs is None:
            return None
        dense_sizes = OCITemplateMapper.DENSE_FLEX_SIZES.get(shape)
        if dense_sizes is not None and dense_sizes.get(ocpus) != memory_gbs:
            raise ValueError("DenseIO Flex requires a supported OCPU/memory pair")
    else:
        return None

    if shape in _ARM_A1:
        architecture: Literal["x86_64", "aarch64"] = "aarch64"
        physical_cores = logical_cpus = ocpus
    elif shape in _ARM_TWO_CORES:
        architecture = "aarch64"
        physical_cores = logical_cpus = ocpus * 2
    else:
        architecture = "x86_64"
        physical_cores = ocpus
        logical_cpus = ocpus * 2

    memory_mib_value = memory_gbs * 1024
    if not isfinite(logical_cpus) or not isfinite(memory_mib_value) or memory_mib_value < 1:
        raise ValueError("Resolved capacity exceeds finite CPU/memory reporting units")
    return OCIComputeCapacity(
        shape=shape,
        architecture=architecture,
        ocpus=ocpus,
        physical_cores=physical_cores,
        logical_cpus=logical_cpus,
        memory_gbs=memory_gbs,
        memory_mib=floor(memory_mib_value),
    )
