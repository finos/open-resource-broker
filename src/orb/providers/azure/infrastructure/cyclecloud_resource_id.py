"""Canonical lifecycle identifier for CycleCloud node requests."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit


@dataclass(frozen=True)
class CycleCloudResourceId:
    """Identify one CycleCloud create-nodes request within its cluster."""

    cluster_name: str
    request_id: str

    def __post_init__(self) -> None:
        if not self.cluster_name:
            raise ValueError("CycleCloud resource ID requires a cluster name")
        if not self.request_id:
            raise ValueError("CycleCloud resource ID requires a request ID")

    def __str__(self) -> str:
        cluster_name = quote(self.cluster_name, safe="-._~")
        request_id = quote(self.request_id, safe="-._~")
        return f"cyclecloud://{cluster_name}/requests/{request_id}"

    @classmethod
    def parse(cls, value: str) -> CycleCloudResourceId:
        """Parse and validate a canonical CycleCloud resource identifier."""
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError(f"Invalid CycleCloud resource ID: {value!r}") from exc

        path_parts = parsed.path.removeprefix("/").split("/")
        if (
            parsed.scheme != "cyclecloud"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or len(path_parts) != 2
            or path_parts[0] != "requests"
            or not path_parts[1]
        ):
            raise ValueError(f"Invalid CycleCloud resource ID: {value!r}")

        resource_id = cls(
            cluster_name=unquote(parsed.netloc),
            request_id=unquote(path_parts[1]),
        )
        if str(resource_id) != value:
            raise ValueError(f"Non-canonical CycleCloud resource ID: {value!r}")
        return resource_id


@dataclass(frozen=True)
class CycleCloudMachineId:
    """Identify one CycleCloud node and the create request that owns it."""

    resource_id: CycleCloudResourceId
    node_id: str

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("CycleCloud machine ID requires a node ID")

    def __str__(self) -> str:
        node_id = quote(self.node_id, safe="-._~")
        return f"{self.resource_id}/nodes/{node_id}"

    @classmethod
    def parse(cls, value: str) -> CycleCloudMachineId:
        """Parse and validate a canonical CycleCloud machine identifier."""
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise ValueError(f"Invalid CycleCloud machine ID: {value!r}") from exc

        path_parts = parsed.path.removeprefix("/").split("/")
        if (
            parsed.scheme != "cyclecloud"
            or not parsed.netloc
            or parsed.query
            or parsed.fragment
            or len(path_parts) != 4
            or path_parts[0] != "requests"
            or not path_parts[1]
            or path_parts[2] != "nodes"
            or not path_parts[3]
        ):
            raise ValueError(f"Invalid CycleCloud machine ID: {value!r}")

        machine_id = cls(
            resource_id=CycleCloudResourceId(
                cluster_name=unquote(parsed.netloc),
                request_id=unquote(path_parts[1]),
            ),
            node_id=unquote(path_parts[3]),
        )
        if str(machine_id) != value:
            raise ValueError(f"Non-canonical CycleCloud machine ID: {value!r}")
        return machine_id
