# Supported SLURM APIs

## What ORB Uses

### Primary: Power Save Hooks

| Interface | Direction | Purpose |
|-----------|-----------|---------|
| `ResumeProgram` | SLURM → ORB | SLURM calls this to power up nodes. ORB provisions cloud instances. |
| `SuspendProgram` | SLURM → ORB | SLURM calls this to power down nodes. ORB terminates instances. |

These are the **only required** SLURM integration points. Everything else is optional.

### Optional: slurmrestd REST API (Monitoring Only)

Used for health checks and status reporting. ORB connects to slurmrestd as a **read-only client**.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/slurm/v0.0.44/diag` | GET | Health check — verify cluster is responsive |
| `/slurm/v0.0.44/nodes` | GET | List all nodes with current states |
| `/slurm/v0.0.44/node/{name}` | GET | Get details for a specific node |
| `/slurm/v0.0.44/partitions` | GET | List all partitions |
| `/slurm/v0.0.44/partition/{name}` | GET | Get details for a specific partition |

**Authentication:** JWT token via `X-SLURM-USER-TOKEN` header (if cluster requires it).

### Optional: CLI Fallback

When slurmrestd is not available, ORB falls back to CLI commands for the same information:

| CLI Command | Equivalent REST Endpoint | Purpose |
|-------------|--------------------------|---------|
| `scontrol ping` | `/diag` | Health check |
| `sinfo -N -h -o "%N %T %P %c %m"` | `/nodes` | List nodes with states |
| `scontrol show node {name}` | `/node/{name}` | Node details |
| `sinfo -h -o "%P %a %l %D %C"` | `/partitions` | List partitions |
| `scontrol show partition {name}` | `/partition/{name}` | Partition details |

## What ORB Does NOT Use

ORB is a **resource provider**, not a job scheduler. The following SLURM interfaces are intentionally NOT integrated:

| Interface | Reason for Exclusion |
|-----------|---------------------|
| `sbatch` | Job submission — not ORB's responsibility |
| `srun` | Interactive job launch — not ORB's responsibility |
| `squeue` | Job queue inspection — ORB doesn't manage jobs |
| `scancel` | Job cancellation — ORB doesn't manage jobs |
| `sacctmgr` | Account management — outside ORB's scope |
| `salloc` | Resource allocation — SLURM handles this |
| `sprio` | Job priority — SLURM handles this |
| Job completion callbacks | ORB doesn't track individual jobs |
| Prolog/Epilog scripts | Node setup/teardown managed by SLURM |

## Architecture Boundary

```
┌─────────────────────────────────────────────────────┐
│                    SLURM Domain                       │
│                                                      │
│  Jobs: sbatch, srun, squeue, scancel (NOT ORB)       │
│  Scheduling: priority, fairshare, limits (NOT ORB)   │
│  Accounting: sacctmgr, sreport (NOT ORB)             │
│                                                      │
│  ┌──────────────────────────────────────────────┐    │
│  │         Power Management Interface           │    │
│  │    ResumeProgram / SuspendProgram            │    │
│  └──────────────────────┬───────────────────────┘    │
└─────────────────────────┼────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────┐
│                     ORB Domain                        │
│                                                      │
│  Resource Provisioning: EC2Fleet, ASG, RunInstances   │
│  Machine Lifecycle: launch, monitor, terminate        │
│  Template Management: instance types, networking      │
│  Health Monitoring: slurmrestd/CLI (read-only)        │
│                                                      │
└─────────────────────────────────────────────────────┘
```
