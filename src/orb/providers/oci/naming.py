"""OCI provider instance naming."""

from __future__ import annotations

import re
from typing import Any


def generate_oci_provider_name(config: dict[str, Any]) -> str:
    profile = config.get("profile") or "instance-profile"
    region = config.get("region", "us-phoenix-1")
    sanitized_profile = re.sub(r"[^a-zA-Z0-9_-]", "-", profile)
    return f"oci_{sanitized_profile}_{region}"
