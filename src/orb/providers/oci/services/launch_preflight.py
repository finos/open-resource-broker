"""Read-only, operation-local compatibility checks before an OCI launch batch."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any


class OCIPreflightError(ValueError):
    """A safe distinction between rejected settings and unavailable evidence."""

    def __init__(self, message: str, *, unavailable: bool = False) -> None:
        super().__init__(message)
        self.error_code = "OCI_PREFLIGHT_UNAVAILABLE" if unavailable else "OCI_PREFLIGHT_REJECTED"


class OCILaunchPreflight:
    """Check current OCI metadata without reserving capacity or changing sizing."""

    def __init__(self, run_oci: Callable[[list[str]], dict[str, Any]]) -> None:
        self._run_oci = run_oci

    def _read(self, args: list[str], label: str, expected: type) -> Any:
        try:
            response = self._run_oci(args)
        except Exception:
            # A 404 can mean either missing resources or missing IAM access.
            # Do not expose raw CLI diagnostics or claim incompatibility.
            raise OCIPreflightError(
                f"OCI launch preflight could not read {label}; verify access and retry",
                unavailable=True,
            ) from None
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, expected):
            raise OCIPreflightError(
                f"OCI launch preflight received incomplete {label} data", unavailable=True
            )
        return data

    @staticmethod
    def _number(value: Any, label: str, *, requested: bool = False) -> Decimal:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise OCIPreflightError(
                f"OCI launch preflight requires numeric {label}", unavailable=not requested
            )
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise OCIPreflightError(
                f"OCI launch preflight requires finite positive {label}",
                unavailable=not requested,
            )
        return number

    @classmethod
    def _check_bounds(
        cls,
        value: Decimal,
        limits: Any,
        lower: str,
        upper: str,
        label: str,
        *,
        required: bool = False,
    ) -> None:
        if limits is None and not required:
            return
        if not isinstance(limits, dict) or (
            required and any(limits.get(k) is None for k in (lower, upper))
        ):
            raise OCIPreflightError(
                f"OCI launch preflight received incomplete {label} limits", unavailable=True
            )
        minimum = cls._number(limits[lower], label) if limits.get(lower) is not None else None
        maximum = cls._number(limits[upper], label) if limits.get(upper) is not None else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise OCIPreflightError(
                f"OCI launch preflight received inconsistent {label} limits", unavailable=True
            )
        if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
            raise OCIPreflightError(f"Requested {label} is outside the OCI compatibility limits")

    @staticmethod
    def _ready(resource: dict[str, Any], label: str) -> None:
        state = resource.get("lifecycle-state")
        if not isinstance(state, str) or not state:
            raise OCIPreflightError(
                f"OCI launch preflight received incomplete {label} readiness", unavailable=True
            )
        if state != "AVAILABLE":
            raise OCIPreflightError(f"OCI {label} must be AVAILABLE before launching workers")

    def _availability_domain(self, payload: dict[str, Any], subnet: dict[str, Any]) -> str:
        if "availability-domain" not in subnet:
            raise OCIPreflightError(
                "OCI subnet availability domain is unreadable", unavailable=True
            )
        subnet_ad = subnet.get("availability-domain")
        if subnet_ad is not None and (not isinstance(subnet_ad, str) or not subnet_ad):
            raise OCIPreflightError(
                "OCI subnet availability domain is unreadable", unavailable=True
            )
        explicit_ad = payload.get("availability_domain")
        if explicit_ad and subnet_ad and explicit_ad != subnet_ad:
            raise OCIPreflightError("OCI subnet availability domain must match the template")
        resolved = explicit_ad or subnet_ad
        if not resolved:
            domains = self._read(
                [
                    "iam",
                    "availability-domain",
                    "list",
                    "--compartment-id",
                    payload["compartment_id"],
                ],
                "availability domains",
                list,
            )
            resolved = next(
                (
                    item.get("name")
                    for item in domains
                    if isinstance(item, dict) and item.get("name")
                ),
                None,
            )
        if not isinstance(resolved, str) or not resolved:
            raise OCIPreflightError(
                "OCI launch availability domain is unresolved", unavailable=True
            )
        return resolved

    @classmethod
    def _check_sizing(
        cls, payload: dict[str, Any], shape: dict[str, Any], image: dict[str, Any]
    ) -> None:
        flexible = shape.get("is-flexible")
        if not isinstance(flexible, bool):
            raise OCIPreflightError("OCI shape flexibility is unreadable", unavailable=True)
        sizing = payload.get("shape_config") or {}
        if not isinstance(sizing, dict):
            raise OCIPreflightError("OCI shape_config must be an object")
        if flexible:
            if not sizing and "Flex" in payload["shape"]:
                # Match the existing launcher's explicit defaults exactly.
                sizing = {"ocpus": 1, "memoryInGBs": 16}
            if sizing.get("ocpus") is None or sizing.get("memoryInGBs") is None:
                raise OCIPreflightError(
                    "OCI launch preflight requires explicit OCPUs and memory "
                    "for partial Flex sizing",
                    unavailable=True,
                )
            ocpus = cls._number(sizing["ocpus"], "OCPUs", requested=True)
            memory = cls._number(sizing["memoryInGBs"], "memory", requested=True)
        else:
            if sizing:
                raise OCIPreflightError("Fixed OCI shapes do not support Flex sizing overrides")
            ocpus = cls._number(shape.get("ocpus"), "fixed-shape OCPUs")
            memory = cls._number(shape.get("memory-in-gbs"), "fixed-shape memory")

        if flexible:
            cpu_options = shape.get("ocpu-options")
            memory_options = shape.get("memory-options")
            if cpu_options is None:
                if ocpus != cls._number(shape.get("ocpus"), "fixed-shape OCPUs"):
                    raise OCIPreflightError("Requested OCPUs differ from the fixed OCI shape size")
            else:
                cls._check_bounds(ocpus, cpu_options, "min", "max", "OCPUs", required=True)
            if memory_options is None:
                if memory != cls._number(shape.get("memory-in-gbs"), "fixed-shape memory"):
                    raise OCIPreflightError(
                        "Requested memory differs from the fixed OCI shape size"
                    )
            else:
                # These unusual g-bs keys are emitted by the OCI CLI serializer.
                cls._check_bounds(
                    memory, memory_options, "min-in-g-bs", "max-in-g-bs", "memory", required=True
                )
                cls._check_bounds(
                    memory / ocpus,
                    memory_options,
                    "min-per-ocpu-in-gbs",
                    "max-per-ocpu-in-gbs",
                    "memory per OCPU",
                )
        cls._check_bounds(ocpus, image.get("ocpu-constraints"), "min", "max", "image OCPUs")
        cls._check_bounds(
            memory, image.get("memory-constraints"), "min-in-gbs", "max-in-gbs", "image memory"
        )

    def validate(self, payload: dict[str, Any]) -> str:
        """Validate one batch's common settings and return its selected AD."""
        image_id = payload["source_details"]["image_id"]
        subnet_id = payload["create_vnic_details"]["subnet_id"]
        image = self._read(["compute", "image", "get", "--image-id", image_id], "image", dict)
        if image.get("id") != image_id:
            raise OCIPreflightError("OCI image identity is unverified", unavailable=True)
        self._ready(image, "image")
        subnet = self._read(["network", "subnet", "get", "--subnet-id", subnet_id], "subnet", dict)
        if subnet.get("id") != subnet_id:
            raise OCIPreflightError("OCI subnet identity is unverified", unavailable=True)
        self._ready(subnet, "subnet")
        ad = self._availability_domain(payload, subnet)
        shapes = self._read(
            [
                "compute",
                "shape",
                "list",
                "--compartment-id",
                payload["compartment_id"],
                "--availability-domain",
                ad,
                "--image-id",
                image_id,
                "--shape",
                payload["shape"],
                "--all",
            ],
            "image-compatible shapes",
            list,
        )
        if any(
            not isinstance(item, dict) or not isinstance(item.get("shape"), str) for item in shapes
        ):
            raise OCIPreflightError("OCI shape listing is unreadable", unavailable=True)
        shape = next((item for item in shapes if item["shape"] == payload["shape"]), None)
        if shape is None:
            raise OCIPreflightError(
                "OCI shape is not offered for the selected compartment, "
                "image and availability domain"
            )
        compatibility = self._read(
            [
                "compute",
                "image-shape-compatibility-entry",
                "get",
                "--image-id",
                image_id,
                "--shape-name",
                payload["shape"],
            ],
            "image sizing compatibility",
            dict,
        )
        if (
            compatibility.get("image-id") != image_id
            or compatibility.get("shape") != payload["shape"]
        ):
            raise OCIPreflightError(
                "OCI image sizing compatibility is unverified", unavailable=True
            )
        self._check_sizing(payload, shape, compatibility)
        return ad
