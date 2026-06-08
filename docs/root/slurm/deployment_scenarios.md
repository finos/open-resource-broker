# Deployment Scenarios

## 1. Co-located on slurmctld Node (Simplest)

ORB runs directly on the SLURM controller node. The ResumeProgram/SuspendProgram scripts invoke the ORB CLI locally.

```
┌─────────────────────────────────────┐
│         slurmctld node              │
│                                     │
│  slurmctld ──→ resumeProgram.sh     │
│                    │                │
│                    ▼                │
│              orb CLI (local)        │
│                    │                │
│                    ▼                │
│             AWS / Cloud API         │
└─────────────────────────────────────┘
```

**Pros:** Simplest setup, no network dependencies.
**Cons:** ORB shares resources with slurmctld.

## 2. Separate Management Node (API Mode)

ORB runs on a separate node with its REST API exposed. The SLURM scripts use `curl` to call the ORB API.

```
┌──────────────────┐       ┌──────────────────┐
│  slurmctld node  │       │   ORB API node   │
│                  │       │                  │
│ resumeProgram.sh │──────▶│  orb serve       │
│  (curl to API)   │       │    :8000         │
│                  │       │       │          │
└──────────────────┘       │       ▼          │
                           │  AWS / Cloud API  │
                           └──────────────────┘
```

**Configuration:**
```bash
export SLURM_ORB_MODE=api
export SLURM_ORB_API_URL=http://orb-manager:8000
```

**Pros:** Separation of concerns, ORB can serve multiple clusters.
**Cons:** Network dependency, additional infrastructure.

## 3. Containerized ORB with SLURM Access

ORB runs in a container (Docker/Podman) with access to cloud credentials and the SLURM controller.

```
┌──────────────────┐       ┌─────────────────────┐
│  slurmctld node  │       │   Container Host    │
│                  │       │  ┌───────────────┐  │
│ resumeProgram.sh │──────▶│  │   ORB         │  │
│  (curl to API)   │       │  │   container   │  │
│                  │       │  │   :8000       │  │
└──────────────────┘       │  └───────┬───────┘  │
                           │          ▼          │
                           │    AWS / Cloud API   │
                           └─────────────────────┘
```

**Pros:** Isolated dependencies, easy upgrades, reproducible.
**Cons:** Container networking complexity.

## 4. Multi-Cluster Setup

A single ORB instance manages cloud resources for multiple SLURM clusters, each with its own partition-to-template mapping.

```
┌─────────────┐
│  Cluster A  │──┐
│  slurmctld  │  │     ┌──────────────────┐
└─────────────┘  ├────▶│   ORB API        │
┌─────────────┐  │     │   (shared)       │
│  Cluster B  │──┘     │       │          │
│  slurmctld  │        │       ▼          │
└─────────────┘        │  AWS / Cloud API  │
                       └──────────────────┘
```

**Configuration:** Use different ORB provider instances per cluster, differentiated by `provider_name` in the template configuration.

**Pros:** Centralized resource management, single point of cloud credential management.
**Cons:** Single point of failure, more complex configuration.
