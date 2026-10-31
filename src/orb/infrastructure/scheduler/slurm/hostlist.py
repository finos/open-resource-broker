"""SLURM hostlist expansion."""

import re


def expand_node_range(node_spec: str) -> list[str]:
    """Expand SLURM hostlist format to individual node names.

    Examples:
        "compute-[001-003]" → ["compute-001", "compute-002", "compute-003"]
        "node-[1,3,5-7]"   → ["node-1", "node-3", "node-5", "node-6", "node-7"]
        "compute-001"      → ["compute-001"]
        "node1 node2"      → ["node1", "node2"]
    """
    results: list[str] = []
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
