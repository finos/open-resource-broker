# Feature Mapping: SLURM ↔ ORB

## Concept Mapping

| SLURM Concept | ORB Concept | Notes |
|---------------|-------------|-------|
| Partition | Template | Each SLURM partition maps to an ORB template defining instance specs |
| Node | Machine | Each SLURM elastic node maps to an ORB machine (cloud instance) |
| ResumeProgram | requestMachines | Provision N fresh instances for the given slot names (batch) |
| SuspendProgram | returnMachines | Terminate instances, clear mappings (always terminate, never stop) |
| slurmrestd | Health check | Optional monitoring of cluster state |
| slurm.conf | config.json | Configuration lives in respective config files |
| Node Name (e.g. `compute-001`) | Machine ID (e.g. `i-0abc123`) | Ephemeral mapping for current cycle only |

> **Note:** Node names are fungible capacity slots, not persistent identities.
> `compute-005` today may be backed by a completely different EC2 instance
> than `compute-005` was yesterday. Each resume cycle provisions fresh instances.

## Node State Mapping

| SLURM Node State | ORB Machine Status | Description |
|------------------|-------------------|-------------|
| IDLE | available | Node is up and ready for jobs |
| ALLOCATED | running | Node is running jobs |
| MIXED | running | Node has some CPUs allocated, some free |
| COMPLETING | running | Node is finishing jobs before becoming idle |
| POWERING_UP | launching | Node is being provisioned (ORB creating instance) |
| CONFIGURING | launching | Node is booting and configuring |
| POWERED_DOWN | terminated | Node is off / instance terminated |
| POWERING_DOWN | terminated | Node is being deprovisioned |
| DOWN | failed | Node is unavailable |
| DRAIN | failed | Node is draining (admin action) |
| DRAINED | failed | Node has finished draining |
| ERROR | failed | Node has an error |
| FAIL | failed | Node has failed |
| NOT_RESPONDING | failed | Node is not responding to health checks |
| RESERVED | pending | Node is reserved for future use |
| FUTURE | pending | Node is defined but not yet instantiated |
| PLANNED | pending | Node is planned for provisioning |

## Operation Flow

### Resume (Scale Up)

```
SLURM job queue pressure
    → slurmctld identifies needed nodes
    → slurmctld calls ResumeProgram with node list
    → resumeProgram.sh invokes ORB
    → ORB provisions cloud instances
    → ORB registers node_name ↔ machine_id mapping
    → Instance boots, joins cluster
    → SLURM transitions node: POWERED_DOWN → POWERING_UP → IDLE
```

### Suspend (Scale Down)

```
SLURM node idle for SuspendTime seconds
    → slurmctld calls SuspendProgram with node list
    → suspendProgram.sh invokes ORB
    → ORB terminates cloud instances
    → ORB removes node_name ↔ machine_id mapping
    → SLURM transitions node: IDLE → POWERING_DOWN → POWERED_DOWN
```

## Template Configuration Example

ORB template file (`slurm_aws_templates.json`):

```json
{
  "scheduler_type": "slurm",
  "templates": [
    {
      "template_id": "batch",
      "max_instances": 100,
      "machine_types": {"c5.xlarge": 1},
      "subnet_ids": ["subnet-abc123"],
      "security_group_ids": ["sg-def456"],
      "price_type": "spot",
      "allocation_strategy": "lowest_price",
      "provider_api": "EC2Fleet",
      "provider_type": "aws"
    },
    {
      "template_id": "gpu",
      "max_instances": 20,
      "machine_types": {"p3.2xlarge": 1},
      "subnet_ids": ["subnet-abc123"],
      "security_group_ids": ["sg-def456"],
      "price_type": "ondemand",
      "provider_api": "EC2Fleet",
      "provider_type": "aws"
    }
  ]
}
```
