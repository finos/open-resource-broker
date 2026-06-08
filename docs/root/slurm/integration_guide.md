# SLURM Integration Guide

## Overview

ORB acts as the **power management bridge** for SLURM elastic/cloud nodes. SLURM decides *when* to scale (based on job queue pressure), and ORB handles *how* to provision and deprovision cloud resources.

ORB integrates with SLURM's **ResumeProgram/SuspendProgram** power saving hooks — the same mechanism SLURM uses for elastic cloud bursting.

## Prerequisites

- SLURM cluster (≥ 23.02) with power saving enabled
- ORB installed on the slurmctld node (or accessible via API)
- Cloud provider credentials configured in ORB
- Elastic node definitions in `slurm.conf`

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

### 4. Environment Variables

Configure these environment variables for ORB's SLURM integration:

| Variable | Description | Default |
|----------|-------------|---------|
| `SLURM_ORB_CONFIG_DIR` | ORB configuration directory | Platform default |
| `SLURM_ORB_WORK_DIR` | ORB working directory | Platform default |
| `SLURM_ORB_LOG_DIR` | ORB log directory | `/var/log/orb` |
| `SLURM_ORB_LOG_LEVEL` | Log level | `INFO` |
| `SLURM_ORB_MODE` | `cli` or `api` | `cli` |
| `SLURM_ORB_API_URL` | ORB API URL (for api mode) | `http://localhost:8000` |
| `SLURM_ORB_RESTD_URL` | slurmrestd URL (for health checks) | Not set |
| `SLURM_ORB_JWT_TOKEN` | JWT token for slurmrestd auth | Not set |

### 5. slurmrestd Integration (Optional)

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
