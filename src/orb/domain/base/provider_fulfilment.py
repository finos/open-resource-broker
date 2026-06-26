"""Provider fulfilment contract value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

FulfilmentState = Literal["fulfilled", "in_progress", "partial", "failed"]


@dataclass(frozen=True)
class ProviderFulfilment:
    """Provider-computed verdict on whether an acquire request is fulfilled."""

    state: FulfilmentState
    message: str
    target_units: int | None = None
    fulfilled_units: int | None = None
    running_count: int | None = None
    pending_count: int | None = None
    failed_count: int | None = None


@dataclass(frozen=True)
class CheckHostsStatusResult:
    """Combined status details and provider fulfilment verdict."""

    instances: list[Mapping[str, Any]]
    fulfilment: ProviderFulfilment
