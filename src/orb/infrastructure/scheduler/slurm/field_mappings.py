"""SLURM field mappings for node states, partitions, and ORB domain translation.

References:
- SLURM Node States: https://slurm.schedmd.com/sinfo.html#SECTION_NODE-STATE-CODES
- SLURM Partitions: https://slurm.schedmd.com/slurm.conf.html#SECTION_PARTITION-CONFIGURATION
- slurmrestd API: https://slurm.schedmd.com/rest_api.html
"""

# ---------------------------------------------------------------------------
# SLURM Node State → ORB Machine Status
#
# SLURM nodes have a base state plus optional flags (e.g. IDLE+DRAIN).
# This maps the BASE state to an ORB machine status. Flags are handled
# separately via SLURM_NODE_STATE_FLAGS.
# ---------------------------------------------------------------------------

SLURM_NODE_STATE_TO_ORB_MACHINE_STATUS: dict[str, str] = {
    # Active/healthy states
    "IDLE": "available",
    "ALLOCATED": "running",
    "MIXED": "running",
    "COMPLETING": "running",
    # Power management states (ORB's domain — node provisioning lifecycle)
    "POWERING_UP": "launching",
    "CONFIGURING": "launching",
    "POWERED_DOWN": "terminated",
    "POWERING_DOWN": "terminated",
    # Failure/unavailable states
    "DOWN": "failed",
    "DRAIN": "failed",
    "DRAINED": "failed",
    "ERROR": "failed",
    "FAIL": "failed",
    "NOT_RESPONDING": "failed",
    # Scheduled/future states
    "RESERVED": "pending",
    "FUTURE": "pending",
    "PLANNED": "pending",
    # Catch-all
    "UNKNOWN": "failed",
}

# ---------------------------------------------------------------------------
# ORB Machine Status → SLURM Node State (reverse, best-effort)
#
# Maps each ORB status to the single most representative SLURM state.
# Used when ORB needs to report back to SLURM (e.g. via scontrol update).
# ---------------------------------------------------------------------------

ORB_MACHINE_STATUS_TO_SLURM_NODE_STATE: dict[str, str] = {
    "available": "IDLE",
    "running": "ALLOCATED",
    "launching": "POWERING_UP",
    "pending": "FUTURE",
    "terminated": "POWERED_DOWN",
    "failed": "DOWN",
}

# ---------------------------------------------------------------------------
# SLURM Partition → ORB Template field mapping
#
# Maps slurmrestd partition response fields to ORB template domain fields.
# Note: "state" requires transformation (UP→True, DOWN/INACTIVE→False),
# not a simple value copy.
# ---------------------------------------------------------------------------

SLURM_PARTITION_TO_ORB_TEMPLATE: dict[str, str] = {
    "partition_name": "template_id",
    "max_nodes": "max_instances",
    "default_time": "timeout",
    "state": "is_active",
    "nodes": "node_list",
    "total_cpus": "vcpus",
    "total_memory": "memory_mb",
}

# ---------------------------------------------------------------------------
# ORB Template → SLURM Partition field mapping (reverse)
#
# Used when formatting ORB template data back to SLURM partition vocabulary.
# ---------------------------------------------------------------------------

ORB_TEMPLATE_TO_SLURM_PARTITION: dict[str, str] = {
    v: k for k, v in SLURM_PARTITION_TO_ORB_TEMPLATE.items()
}

# ---------------------------------------------------------------------------
# SLURM Node State Flags
#
# These flags can be combined with base states (e.g. "IDLE+DRAIN").
# When parsing node state strings, split on "+" and handle the base state
# plus any flags separately.
# Reference: https://slurm.schedmd.com/sinfo.html#SECTION_NODE-STATE-CODES
# ---------------------------------------------------------------------------

SLURM_NODE_STATE_FLAGS: list[str] = [
    "DRAIN",
    "COMPLETING",
    "NOT_RESPONDING",
    "POWERED_DOWN",
    "POWERING_UP",
    "FAIL",
    "PLANNED",
    "MAINTENANCE",
]
