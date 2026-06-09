# SLURM Integration Guide

## Overview

ORB acts as the **power management bridge** for SLURM elastic/cloud nodes. SLURM decides *when* to scale (based on job queue pressure), and ORB handles *how* to provision and deprovision cloud resources.

ORB integrates with SLURM's **ResumeProgram/SuspendProgram** power saving hooks — the same mechanism SLURM uses for elastic cloud bursting.

## Prerequisites

- SLURM cluster (≥ 23.02) with power saving enabled
- ORB installed on the slurmctld node (or accessible via API)
- Cloud provider credentials configured in ORB
- Elastic node definitions in `slurm.conf`

### Cloud Node Model (Dynamic Slots)

ORB treats SLURM cloud nodes as **fungible capacity slots**:

- `batch-[001-100]` defines 100 slots of identical shape
- Each ResumeProgram call provisions FRESH instances
- No data residency between suspend/resume cycles
- Node names are arbitrary handles — `batch-005` today may be backed
  by a completely different EC2 instance than `batch-005` yesterday
- All persistent data should live on shared storage (FSx for Lustre, EFS, NFS)
- Suspend ALWAYS terminates instances (no stop/start mode)

This matches the AWS ParallelCluster model and SchedMD's official
cloud-bursting recommendations.

## Configuration Steps

### 1. Configure ORB for SLURM

Set the scheduler type in your ORB `config.json`:

```json
{
  "scheduler": {
    "type": "slurm",
    "config_root": "/etc/orb"
  },
  "provider": {
    "providers": [
      {
        "name": "aws_prod",
        "type": "aws",
        "enabled": true,
        "default": true,
        "config": {
          "region": "us-east-1"
        }
      }
    ]
  }
}
```

### 2. Configure slurm.conf

Add power saving configuration to `slurm.conf`:

```ini
# Power saving configuration
SuspendProgram=/opt/orb/scripts/suspendProgram.sh
ResumeProgram=/opt/orb/scripts/resumeProgram.sh
SuspendTimeout=120
ResumeTimeout=300
SuspendTime=300
SuspendExcNodes=controller

# Elastic node definitions
NodeName=compute-[001-100] CPUs=4 RealMemory=8192 State=CLOUD
PartitionName=batch Nodes=compute-[001-100] Default=YES MaxTime=INFINITE State=UP
```

### 3. Install ORB Scripts

Copy (or symlink) the ORB SLURM scripts:

```bash
cp /path/to/orb/infrastructure/scheduler/slurm/scripts/resumeProgram.sh /opt/orb/scripts/
cp /path/to/orb/infrastructure/scheduler/slurm/scripts/suspendProgram.sh /opt/orb/scripts/
chmod +x /opt/orb/scripts/*.sh
```

### 4. Hook Configuration File

SLURM spawns ResumeProgram/SuspendProgram as child processes — systemd
environment variables on slurmctld do NOT propagate. The hook scripts
source a configuration file instead.

Create `${ORB_ROOT_DIR}/slurm_hooks.env` (default: `/usr/orb/slurm_hooks.env`):

```bash
# Template to provision when SLURM calls ResumeProgram
SLURM_ORB_TEMPLATE_ID=EC2Fleet-Instant-OnDemand

# Optional overrides
# SLURM_ORB_LOG_DIR=/var/log/orb
# SLURM_ORB_MODE=cli
# SLURM_ORB_API_URL=http://localhost:8000
```

The scripts source this file before reading any `SLURM_ORB_*` variables.

### 5. Environment Variables

Configure these environment variables for ORB's SLURM integration
(set them in `slurm_hooks.env` or the calling environment):

| Variable | Description | Default |
|----------|-------------|---------|
| `ORB_ROOT_DIR` | ORB installation directory (used for config discovery) | `/usr/orb` |
| `SLURM_ORB_TEMPLATE_ID` | Template ID for provisioning | `default` (warns) |
| `SLURM_ORB_LOG_DIR` | ORB log directory | `/var/log/orb` |
| `SLURM_ORB_MODE` | `cli` or `api` | `cli` |
| `SLURM_ORB_API_URL` | ORB API URL (for api mode) | `http://localhost:8000` |
| `SLURM_ORB_RESTD_URL` | slurmrestd URL (for health checks) | Not set |
| `SLURM_ORB_JWT_TOKEN` | JWT token for slurmrestd auth | Not set |

### 6. slurmrestd Integration (Optional)

If slurmrestd is running on your cluster, ORB can use it for health monitoring:

```bash
export SLURM_ORB_RESTD_URL=http://slurmctld:6820
export SLURM_ORB_JWT_TOKEN=$(scontrol token lifespan=3600)
```

This enables:
- Cluster health checks via `orb system health`
- Node state monitoring
- Partition discovery

## Verification

After configuration, verify the integration:

```bash
# Check ORB can see the scheduler
orb system status --scheduler slurm

# Test health check (if slurmrestd configured)
orb system health

# Manually trigger a resume (for testing)
/opt/orb/scripts/resumeProgram.sh "compute-001"

# Check node was provisioned
scontrol show node compute-001
```

## CLI Usage with --nodes

The `--nodes` flag lets ORB work directly with SLURM node names.

### Provisioning by Node Name

```bash
# Request 3 machines, associating them with SLURM node names
orb machines request EC2Fleet-Instant-OnDemand 3 --nodes "compute-[001-003]"
```

ORB expands the SLURM hostlist format and stores the node name on each
provisioned machine's `name` field. This enables lookup by node name later.

### Terminating by Node Name

```bash
# Terminate machines associated with specific SLURM nodes
orb machines terminate --nodes "compute-[001-003]" --force
```

This resolves node names to machine IDs via storage and terminates them.
No in-memory state is needed — node names persist across CLI invocations.

### How Node Names Persist

When machines are provisioned with `--nodes`:
1. The SLURM hostlist is expanded to individual names (e.g. `compute-001`)
2. Each provisioned machine's `name` field is set to its assigned node name
3. The name persists in machine storage (survives CLI restarts)
4. `terminate --nodes` queries storage by name to find machine IDs
5. Provider sync does NOT overwrite explicitly-set node names

## AMI/Image Requirements

The provisioned cloud instances **must** have SLURM pre-installed and configured. ORB provisions the infrastructure; the image handles SLURM membership.

**Required on the AMI/image:**

- **slurmd** installed (same SLURM version as the cluster)
- **slurm.conf** configured with correct `SlurmctldHost` pointing to your controller
- **Munge** authentication configured (shared munge key from the cluster)
- **slurmd systemd service** enabled to start on boot
- Network access to slurmctld (typically port 6817)

**Recommended:**

- cloud-init or user_data support for dynamic hostname configuration
- Log forwarding for slurmd logs
- Health check script for node self-verification

## Node Registration Flow

```
ResumeProgram (N nodes, single batch)
    │
    ▼
ORB batch provisions N fresh EC2 instances
    │
    ▼
resumeProgram.sh calls: scontrol update NodeAddr=<IP> (for each)
    │
    ▼
Instances boot → slurmd starts → registers with slurmctld
    │
    ▼
SLURM clears POWERING_UP → nodes become IDLE → jobs scheduled
```

**Timing considerations:**

| Instance Type | Typical Boot Time | Recommended ResumeTimeout |
|---------------|-------------------|--------------------------|
| General (t3, m5) | 60-90s | 300s |
| Compute (c5, c6i) | 60-90s | 300s |
| GPU (p3, g4) | 90-180s | 600s |
| Large/metal | 120-300s | 900s |
