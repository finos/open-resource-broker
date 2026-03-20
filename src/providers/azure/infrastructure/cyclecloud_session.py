"""CycleCloud infrastructure session context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests


@dataclass(frozen=True)
class CycleCloudSessionContext:
    """Resolved CycleCloud HTTP session plus ORB-specific connection metadata."""

    session: requests.Session
    base_url: str
    auth_mode: Optional[str]
    credential_path: Optional[str]
