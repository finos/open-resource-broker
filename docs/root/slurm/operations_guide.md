# SLURM Operations Guide

## Template Generation

ORB generates templates directly from your `slurm.conf` partition definitions, ensuring that provisioned instances match SLURM's expected resource specs.

### Basic Usage

```bash
orb templates generate --slurm-conf /etc/slurm/slurm.conf
```

### How It Works

1. Parses `NodeName=` lines for CPUs and RealMemory declarations
2. Parses `PartitionName=` lines to associate partitions with node specs
3. Maps each partition's CPU/memory requirements to an appropriate AWS instance type
4. Generates one template per partition with `template_id` = partition name

Example: given this `slurm.conf`:

```ini
NodeName=compute-[001-050] CPUs=4 RealMemory=16000 State=CLOUD
NodeName=gpu-[001-010] CPUs=8 RealMemory=32000 State=CLOUD

PartitionName=batch Nodes=compute-[001-050] Default=YES MaxTime=INFINITE State=UP
PartitionName=gpu Nodes=gpu-[001-010] MaxTime=INFINITE State=UP
```

ORB generates:

```json
[
  {"template_id": "batch", "machine_types": {"t3.xlarge": 1}, "provider_api": "EC2Fleet"},
  {"template_id": "gpu", "machine_types": {"t3.2xlarge": 1}, "provider_api": "EC2Fleet"}
]
```

### slurm.conf Path Resolution

The path to `slurm.conf` is resolved in this order:

1. `--slurm-conf /path` CLI flag (highest priority)
2. `scheduler.slurm.config_path` in config.json
3. `SLURM_CONF` environment variable
4. Default paths: `/etc/slurm/slurm.conf`, `/usr/local/etc/slurm.conf`, `$ORB_ROOT_DIR/slurm.conf`

### User-Specified Instance Type Preferences

For partitions where you want to control instance selection (e.g., for cost optimization or spot diversity), add preferences in `config.json`:

```json
{
  "scheduler": {
    "type": "slurm",
    "slurm": {
      "config_path": "/etc/slurm/slurm.conf",
      "partitions": {
        "batch": {
          "instance_types": ["t3.medium", "t3.large", "m5.large"],
          "allocation_strategy": "capacityOptimized"
        },
        "gpu": {
          "instance_types": ["g4dn.xlarge", "g4dn.2xlarge"],
          "allocation_strategy": "lowestPrice"
        }
      }
    }
  }
}
```

Rules:

- `instance_types` is an ordered list (first = highest priority)
- All listed types should have CPU >= partition's declared CPUs and Memory >= partition's declared RealMemory
- Partitions without a config entry fall back to auto-mapping from slurm.conf
- Multiple instance types are used as EC2 Fleet overrides for capacity diversification

Generated template output with preferences:

```json
{
  "template_id": "batch",
  "machine_types": {"t3.medium": 1, "t3.large": 1, "m5.large": 1},
  "allocation_strategy": "capacityOptimized",
  "provider_api": "EC2Fleet",
  "fleet_type": "instant"
}
```

### Validation

At generation time, ORB validates that all specified instance types meet the partition's resource requirements. If validation fails, template generation is refused:

```
ERROR: Template 'batch' validation failed:
  - Instance type 't3.micro' (1 vCPU, 1024MB) does not meet partition requirements (CPUs=2, RealMemory=3800)
Fix: Remove undersized instance types from scheduler.slurm.partitions.batch.instance_types
     or reduce partition resource requirements in slurm.conf
```

Use `--force` to bypass validation (for advanced users).

### Overwriting Existing Templates

```bash
orb templates generate --slurm-conf /etc/slurm/slurm.conf --force
```

The `--force` flag both overwrites existing template files and skips instance type validation.

---

## Per-Instance Tagging (orb:node-name)

### Mechanism

When machines are provisioned with `--nodes` (or via SLURM ResumeProgram):

1. Application layer assigns each instance a SLURM node name from the request
2. After provisioning, ORB dispatches a `TAG_INSTANCES` operation to the provider
3. Provider calls `ec2:CreateTags` with `orb:node-name=<node_name>` on each instance
4. Compute nodes read this tag on boot to start slurmd with the correct identity

### Why It Exists

SLURM requires each compute node to register with the name declared in `slurm.conf`. Since ORB provisions generic instances, the node name assignment happens post-launch via instance tags. The compute AMI's boot script reads the tag and starts `slurmd -N <name>`.

### Provider Requirements

- Must support `TAG_INSTANCES` operation type
- Must be able to apply per-instance tags after launch
- AWS: requires `ec2:CreateTags` permission on the controller's IAM role
- Compute nodes: require `ec2:DescribeTags` permission to read their own tags

### Error Handling

Tag failures are logged at ERROR level but do NOT fail the overall provisioning request (fire-and-forget). The `TAG_INSTANCES` operation returns `error_result` on failure so monitoring can alert on it.

---

## Caveats and Known Limitations

### Environment

| Issue | Detail | Mitigation |
|-------|--------|------------|
| POSIX locale | AL2023 SSM sessions default to `LANG=C` | `PYTHONUTF8=1` is set automatically in hook scripts and CLI entry point |
| Hook env isolation | systemd env vars don't propagate to ResumeProgram | All config goes in `$ORB_ROOT_DIR/slurm_hooks.env` |

### Networking

| Requirement | Reason |
|-------------|--------|
| Security groups: controller ↔ compute all TCP | srun uses ephemeral ports for I/O forwarding |
| Controller on fixed IP/hostname | slurm.conf `SlurmctldHost` must be reachable from compute |
| DNS resolution | Compute nodes must resolve controller hostname |

### Timing

| Parameter | Recommended | Reason |
|-----------|-------------|--------|
| `ResumeTimeout` | ≥ 300s | Instance boot (~60s) + tag propagation (~5s) + tag read with retries (~30s) + slurmd registration |
| `SuspendTime` | 30-300s | How long idle nodes wait before termination; lower = faster cost savings |
| `SuspendTimeout` | 120s | Time for SuspendProgram to complete termination |
| Tag retry window | 30s (15 retries × 2s) | Built into boot script; covers EC2 tag propagation delay |

### IAM Requirements

**Controller node (slurmctld) IAM role:**

- `ec2:RunInstances`, `ec2:CreateFleet`, `ec2:TerminateInstances`
- `ec2:CreateLaunchTemplate`, `ec2:CreateLaunchTemplateVersion`
- `ec2:CreateTags` (for per-instance tagging)
- `ec2:DescribeInstances`, `ec2:DescribeFleets`
- `iam:PassRole` (to attach instance profile to compute nodes)
- `ssm:GetParameter` (if using SSM for AMI resolution)

**Compute node IAM role (instance profile):**

- `ec2:DescribeTags` (read own `orb:node-name` tag)
- `ssm:GetParameter` (if fetching munge key / slurm.conf from SSM)

The instance profile must be specified in config:
```json
"provider_defaults": {
  "aws": {
    "template_defaults": {
      "iam_instance_profile": "orb-compute-instance-profile"
    }
  }
}
```

### SLURM Resource Matching

- SLURM requires all nodes in a partition to match the declared `CPUs` and `RealMemory`
- Instances with MORE resources than declared are accepted (SLURM uses declared values for scheduling)
- Instances with FEWER resources will fail `slurmd` registration
- Generated templates should use a single instance type per partition, OR multiple types that ALL exceed the partition's declared resources
- `orb templates generate` validates this automatically

### Tag Race Condition

EC2 tags are eventually consistent. Between `CreateTags` and the instance boot script reading the tag, there is a propagation window (typically < 5s, worst case ~30s).

The recommended boot script pattern:

```bash
# Retry loop for tag propagation
for i in $(seq 1 15); do
    NODE_NAME=$(aws ec2 describe-tags --region "${REGION}" \
        --filters "Name=resource-id,Values=${INSTANCE_ID}" "Name=key,Values=orb:node-name" \
        --query 'Tags[0].Value' --output text 2>/dev/null)
    if [ -n "${NODE_NAME}" ] && [ "${NODE_NAME}" != "None" ]; then
        break
    fi
    sleep 2
done
```

---

## Compute AMI Requirements

### Base Packages

- Amazon Linux 2023 (al2023) or Ubuntu 22.04+
- SLURM 24.05.5+ compiled with `--with-systemd` for cgroup/v2 support
- Build dependencies: `systemd-devel`, `dbus-devel`, `munge-devel`
- Runtime: `munge`, `slurm-slurmd`

### Configuration Files

| File | Purpose |
|------|---------|
| `/etc/slurm/slurm.conf` | Must match controller's config (SlurmctldHost, partitions) |
| `/etc/slurm/cgroup.conf` | cgroup/v2 configuration for resource enforcement |
| `/etc/munge/munge.key` | Shared authentication key (must match controller) |

### Boot Script

The AMI must include a boot script (via cloud-init or systemd oneshot) that:

1. Reads `orb:node-name` EC2 tag (with retry loop for propagation)
2. Sets hostname to the SLURM node name
3. Optionally fetches `slurm.conf` and `munge.key` from SSM Parameter Store
4. Starts munge
5. Starts slurmd with `-N <node_name>`

Example minimal boot script:

```bash
#!/bin/bash
set -euo pipefail

INSTANCE_ID=$(curl -s http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -s http://169.254.169.254/latest/meta-data/placement/region)

# Wait for orb:node-name tag
for i in $(seq 1 15); do
    NODE_NAME=$(aws ec2 describe-tags --region "${REGION}" \
        --filters "Name=resource-id,Values=${INSTANCE_ID}" "Name=key,Values=orb:node-name" \
        --query 'Tags[0].Value' --output text 2>/dev/null)
    [ -n "${NODE_NAME}" ] && [ "${NODE_NAME}" != "None" ] && break
    sleep 2
done

if [ -z "${NODE_NAME}" ] || [ "${NODE_NAME}" = "None" ]; then
    echo "ERROR: Could not read orb:node-name tag after 30s" >&2
    exit 1
fi

# Set hostname and start SLURM
hostnamectl set-hostname "${NODE_NAME}"
systemctl start munge
slurmd -N "${NODE_NAME}"
```

### cgroup.conf Example

```ini
CgroupAutomount=yes
ConstrainCores=yes
ConstrainRAMSpace=yes
ConstrainSwapSpace=yes
```

### Verification Checklist

- [ ] `slurmd -C` reports correct CPUs/RealMemory matching slurm.conf
- [ ] munge auth succeeds: `munge -n | ssh controller unmunge`
- [ ] slurmd can reach slurmctld on port 6817
- [ ] Instance profile has `ec2:DescribeTags` permission
- [ ] Boot script handles tag propagation delay (retry loop)
- [ ] Security groups allow controller ↔ compute traffic (all TCP)
