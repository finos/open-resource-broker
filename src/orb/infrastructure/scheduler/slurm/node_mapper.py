"""SLURM node name ↔ ORB machine ID bidirectional mapping."""

import json
import re
import threading
from pathlib import Path

_NODE_NAME_RE = re.compile(r"^[a-zA-Z0-9\-\[\],]+$")
_MACHINE_ID_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


class SlurmNodeMapper:
    """Bidirectional mapping between SLURM node names and ORB machine IDs.

    Thread-safe via a threading.Lock on all read/write operations.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._node_to_machine: dict[str, str] = {}
        self._machine_to_node: dict[str, str] = {}

    @staticmethod
    def _validate_node_name(node_name: str) -> None:
        if not node_name or not _NODE_NAME_RE.match(node_name):
            raise ValueError(
                f"Invalid node name '{node_name}': must contain only alphanumeric, hyphens, brackets, commas"
            )

    @staticmethod
    def _validate_machine_id(machine_id: str) -> None:
        if not machine_id or not _MACHINE_ID_RE.match(machine_id):
            raise ValueError(
                f"Invalid machine ID '{machine_id}': must contain only alphanumeric and hyphens"
            )

    def register_mapping(self, node_name: str, machine_id: str) -> None:
        """Store a node_name ↔ machine_id mapping."""
        self._validate_node_name(node_name)
        self._validate_machine_id(machine_id)
        with self._lock:
            self._node_to_machine[node_name] = machine_id
            self._machine_to_node[machine_id] = node_name

    def get_machine_id(self, node_name: str) -> str | None:
        """Look up machine_id for a node name."""
        with self._lock:
            return self._node_to_machine.get(node_name)

    def get_node_name(self, machine_id: str) -> str | None:
        """Reverse lookup: machine_id → node_name."""
        with self._lock:
            return self._machine_to_node.get(machine_id)

    def remove_mapping(self, node_name: str) -> None:
        """Remove a mapping by node name."""
        with self._lock:
            machine_id = self._node_to_machine.pop(node_name, None)
            if machine_id:
                self._machine_to_node.pop(machine_id, None)

    def get_all_mappings(self) -> dict[str, str]:
        """Return all node_name → machine_id mappings."""
        with self._lock:
            return dict(self._node_to_machine)

    def save(self, path: str | Path) -> None:
        """Save mappings to a JSON file."""
        with self._lock:
            data = dict(self._node_to_machine)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, indent=2))

    def load(self, path: str | Path) -> None:
        """Load mappings from a JSON file."""
        p = Path(path)
        if not p.exists():
            return
        data: dict[str, str] = json.loads(p.read_text())
        with self._lock:
            self._node_to_machine = data
            self._machine_to_node = {v: k for k, v in data.items()}

    @classmethod
    def from_file(cls, path: str | Path) -> "SlurmNodeMapper":
        """Construct a SlurmNodeMapper pre-loaded from a JSON file."""
        mapper = cls()
        mapper.load(path)
        return mapper

    @staticmethod
    def expand_node_range(node_spec: str) -> list[str]:
        """Expand SLURM hostlist format to individual node names.

        Examples:
            "compute-[001-003]" → ["compute-001", "compute-002", "compute-003"]
            "node-[1,3,5-7]"   → ["node-1", "node-3", "node-5", "node-6", "node-7"]
            "compute-001"      → ["compute-001"]
            "node1 node2"      → ["node1", "node2"]
        """
        results: list[str] = []
        # Split on spaces first for space-separated lists
        for token in node_spec.strip().split():
            match = re.match(r"^(.+?)\[(.+)]$", token)
            if not match:
                results.append(token)
                continue
            prefix = match.group(1)
            range_spec = match.group(2)
            for part in range_spec.split(","):
                if "-" in part:
                    start_s, end_s = part.split("-", 1)
                    width = len(start_s)
                    for i in range(int(start_s), int(end_s) + 1):
                        results.append(f"{prefix}{str(i).zfill(width)}")
                else:
                    results.append(f"{prefix}{part}")
        return results
