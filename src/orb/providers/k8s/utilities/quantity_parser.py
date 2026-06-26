"""Kubernetes resource-quantity parsers (CPU and memory).

Copied as new code from ``src/orb/k8s_legacy/k8sutils.py`` per the Phase B
plan — the modern provider does not import from the legacy tree, but the
quantity-string parsing logic is pure and well-tested in production so we
re-use the behaviour verbatim while shedding the legacy module dependency.

The functions accept the same string shapes the kubernetes API uses for
``resources.requests`` and ``resources.limits``:

* CPU:
    - ``"500m"``      — 500 millicores (0.5 cores)
    - ``"1"`` / ``"2.5"`` — whole cores
* Memory (suffixed):
    - Binary: ``"512Ki"``, ``"256Mi"``, ``"2Gi"``
    - Decimal: ``"500k"``, ``"500M"``, ``"2G"``
    - Bare integer: bytes

Reference:
https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#resource-units-in-kubernetes
"""

from __future__ import annotations


def parse_cpu_quantity(quantity: str | None) -> float:
    """Parse a Kubernetes CPU resource quantity string into cores.

    Args:
        quantity: A kubernetes CPU quantity string, e.g. ``"500m"`` or
            ``"1.5"``.  ``None`` and the empty string both return ``0.0``.

    Returns:
        The CPU quantity expressed in whole cores.  ``"500m"`` -> ``0.5``,
        ``"2"`` -> ``2.0``.
    """
    if not quantity:
        return 0.0
    if quantity.endswith("m"):
        return int(quantity[:-1]) / 1000  # millicores -> cores
    return float(quantity)


def parse_memory_quantity(quantity: str | None) -> int:  # noqa: PLR0911
    """Parse a Kubernetes memory resource quantity string into bytes.

    Supports both binary (``Ki``, ``Mi``, ``Gi``) and decimal (``k``, ``M``,
    ``G``) suffixes.  A bare integer string is treated as bytes.

    Args:
        quantity: A kubernetes memory quantity string, e.g. ``"512Mi"``.
            ``None`` and the empty string both return ``0``.

    Returns:
        The memory quantity expressed in bytes.
    """
    if not quantity:
        return 0
    if quantity.endswith("Ki"):
        return int(quantity[:-2]) * 1024
    if quantity.endswith("Mi"):
        return int(quantity[:-2]) * 1024 * 1024
    if quantity.endswith("Gi"):
        return int(quantity[:-2]) * 1024 * 1024 * 1024
    if quantity.endswith("k"):
        return int(quantity[:-1]) * 1000
    if quantity.endswith("M"):
        return int(quantity[:-1]) * 1000 * 1000
    if quantity.endswith("G"):
        return int(quantity[:-1]) * 1000 * 1000 * 1000
    return int(quantity)


__all__ = ["parse_cpu_quantity", "parse_memory_quantity"]
