"""Cell registry: which (provider, api) pairs the suite drives, which scenarios
apply to them, and which are strict-exempt.

Follow-up work grows ``CELLS`` and shrinks ``EXEMPT``.
Local command: ``uv run pytest tests/providers/lifecycle -n 2 --dist=loadscope``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Cell:
    provider: str
    api: str
    template_id: str
    variant: str = ""

    @property
    def name(self) -> str:
        return f"{self.provider}-{self.api}{('-' + self.variant) if self.variant else ''}"


AWS_RUN_INSTANCES = Cell("aws", "RunInstances", "RunInstances-OnDemand")
AWS_RUN_INSTANCES_SPOT = Cell("aws", "RunInstances", "RunInstances-Spot", "spot")
AWS_EC2_FLEET_INSTANT = Cell("aws", "EC2Fleet", "EC2Fleet-Instant-OnDemand", "instant")
K8S_POD = Cell("k8s", "Pod", "k8s-pod-example")
K8S_DEPLOYMENT = Cell("k8s", "Deployment", "k8s-deployment-example")

CELLS: dict[tuple[str, str], Cell] = {
    ("aws", "RunInstances"): AWS_RUN_INSTANCES,
    ("aws", "RunInstances-spot"): AWS_RUN_INSTANCES_SPOT,
    ("aws", "EC2Fleet-instant"): AWS_EC2_FLEET_INSTANT,
    ("k8s", "Pod"): K8S_POD,
    ("k8s", "Deployment"): K8S_DEPLOYMENT,
}

AWS_CELLS = [AWS_RUN_INSTANCES, AWS_RUN_INSTANCES_SPOT, AWS_EC2_FLEET_INSTANT]
K8S_CELLS = [K8S_POD, K8S_DEPLOYMENT]

# Scenario groups an applicability row refers to.
SCENARIOS = (
    "acquire_complete",
    "delayed_pending",
    "deadline_status",
    "deadline_release",
    "deadline_capacity",
    "abandoned_pending",
    "cancel",
    "return_lag",
    "post_terminal_drift",
    "capacity_shortfall",
    "return_lifecycle",
    "blast_radius",
    "dry_run",
)

RUN = None
_NO_CAPACITY = (
    "no modifiable target capacity: RunInstances has none; an instant fleet cannot be "
    "modified, cancel deletes it instead"
)

APPLICABILITY: dict[Cell, dict[str, Optional[str]]] = {
    AWS_RUN_INSTANCES: {s: RUN for s in SCENARIOS} | {"deadline_capacity": _NO_CAPACITY},
    AWS_RUN_INSTANCES_SPOT: {s: RUN for s in SCENARIOS} | {"deadline_capacity": _NO_CAPACITY},
    AWS_EC2_FLEET_INSTANT: {s: RUN for s in SCENARIOS} | {"deadline_capacity": _NO_CAPACITY},
    K8S_POD: {s: RUN for s in SCENARIOS}
    | {
        "deadline_capacity": "no controller target",
        "capacity_shortfall": "a pod either exists or does not; there is no partial capacity",
    },
    K8S_DEPLOYMENT: {s: RUN for s in SCENARIOS},
}

# Strict-exempt: (provider, api) -> the gap. An exempt entry whose scenario passes
# fails the suite, so the entry has to be removed as soon as the cell is covered.
EXEMPT: dict[tuple[str, str], str] = {
    ("aws", "ASG"): "no fleet/ASG lifecycle fake yet",
    ("aws", "EC2Fleet-maintain"): "no fleet/ASG lifecycle fake yet",
    ("aws", "EC2Fleet-request"): "no fleet/ASG lifecycle fake yet",
    ("aws", "SpotFleet"): "no fleet/ASG lifecycle fake yet",
    ("aws", "MicroVM"): "no lifecycle fake yet",
    ("k8s", "StatefulSet"): "no lifecycle fake yet",
    ("k8s", "Job"): (
        "no lifecycle fake yet; a finished Job pod has no running state or stable ip, "
        "so the running and address half of the completion check does not apply"
    ),
    ("azure", "*"): "provider not covered yet",
    ("gcp", "*"): "provider not covered yet",
    ("oci", "*"): "provider not covered yet; terminations are confirmed by an operator",
}
