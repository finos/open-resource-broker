# Daemon Mode Design

## Overview

Today Open Resource Broker (ORB) acts only when asked: a CLI call, a REST
request, or an MCP tool call. Daemon mode adds an optional, long-running
process that watches an external demand signal (for example the depth of an SQS
queue, pending Kubernetes pods, or the waiting applications of a Spark
Standalone master) and adjusts capacity by calling the same acquire and return
orchestrators the API uses.

This document defines the process model, the extension interfaces, the
single-actor guarantees, the scale-down behaviour, the configuration schema and
the delivery plan. Daemon mode is opt-in and disabled by default.
Kubernetes chart behaviour described here is the contract for the chart.

## Goals

- Scale capacity up and down automatically from a pluggable demand signal.
- Reuse the existing acquire and return orchestrators. The daemon never talks to
  a provider directly.
- Be safe to run: dry run by default, hard capacity ceilings, no scaling on
  stale or missing data, no action on machines the daemon did not create.
- Let third parties add demand sources without changing ORB core.
- Run as a single process on bare metal or as its own Deployment in Kubernetes,
  with a clear path to multiple replicas.

## Non-goals

- Replacing the REST API, CLI or MCP server. They remain request-driven and are
  unchanged.
- Co-hosting the daemon inside the API process.
- A general workflow or job scheduler. The daemon only converts a demand number
  into a machine count.
- Budget-based limiting, per-source release hooks and reload of the source list
  (see [Open items](#open-items)).

## Process model

The daemon is one asyncio process with one event loop, started with
`orb daemon run` in the foreground (suitable for containers and service
managers).

```
orb daemon run
 |- DaemonSupervisor          lifecycle, restart with backoff, shutdown ordering
 |   |- LeaderElector         multi-replica releases only (backend "none" before that)
 |   |- DemandPoller[source]  one per configured source; caches the latest reading
 |   |- Reconciler[policy]    one per source + template pair; acts only as leader
 |   |- HealthServer          /livez /readyz /metrics on daemon.health_port
```

Rules:

- Pollers run on every replica. They are read-only and keep `orb daemon status`
  accurate on followers. Only the reconciler acts, and only while it holds
  leadership.
- Pollers and reconcilers are isolated. An exception in one source's poll marks
  that source `stale`; it never stops the loop.
- A stale reading (older than `max_staleness_seconds`) makes the reconciler skip
  the cycle. The daemon never scales on missing data and never scales to zero
  because of an error.
- The reconciler is level-triggered and idempotent. Each cycle recomputes the
  desired count from the latest reading and the actual count from the machine
  list; it does not remember earlier requests. Machines that are pending or
  provisioning count as actual, so a slow provider does not cause repeated
  over-requests.

## Interfaces

```
DemandReading   { source_id, value: float, unit: str, observed_at,
                  labels: dict, raw: dict | None }

DemandSourcePort (Protocol)
    name: str
    async start(config)
    async stop()
    async read() -> DemandReading    # raises DemandSourceError(retryable: bool)

DemandSourceRegistry
    register(name, factory)
    create(name, config)
    # third-party sources register through the entry-point group
    # "orb.demand_sources"

ScalingPolicyPort (Protocol)
    decide(reading, current: CapacityState, now) -> ScalingDecision
    # pure: no I/O, testable with a simulated clock

ScalingDecision { target: int,
                  action: scale_up | scale_down | hold,
                  reason: str,
                  suppressed_by: cooldown | clamp | rate | max_instances | None }

CapacityState   { actual: int, pending: int, returning: int,
                  spot_flagged_ids: list }

LeaderElectionPort (Protocol)       # multi-replica only
    acquire() / renew() / release()
    is_leader: bool
    fencing_token: str              # opaque
```

Built-in sources ship as separate modules registered through the same entry-point
mechanism that third parties use, so the plugin path is exercised by first-party
code. The Spark source is distributed as an optional extra and is not part of
core packaging.

## Reconcile cycle

For each source and template pair, while leader:

1. Take the latest cached reading. Skip the cycle if it is missing or stale.
2. Build the capacity state from the machine list, filtered by template and by
   the ownership label `orb:managed-by=daemon:<source>`. The daemon only ever
   counts and acts on machines it created.
3. Call `policy.decide(reading, state, now)`.
4. Apply limits in order: policy `min`/`max`, `limits.max_instances`,
   `limits.max_actions_per_minute`.
5. Record the intent (see [Single-actor guarantees](#single-actor-guarantees)).
   Unless `dry_run` is set, call `AcquireMachinesOrchestrator` (input carries
   `template_id`, `count` and `client_token`) or `ReturnMachinesOrchestrator`
   (input carries the machine ids and `grace_period_seconds`). Actual state
   comes from the `ListMachines` query.
6. Append a decision record (demand, target, actual, action, reason,
   dry-run flag) and run the orphan-adoption pass.

## Single-actor guarantees

Storage-level optimistic locking prevents two writers from corrupting one
record. It does not prevent two replicas from each deciding to scale up by five.
The guarantees below address that.

### First release: single replica

The first release runs exactly one daemon: `leader_election.backend: none`,
`replicas: 1`, deployment strategy `Recreate`.

**Storage constraints**

- `sql` and `dynamodb` storage have no local data directory and need no local
  guard. This is the supported storage for the daemon in Kubernetes.
- On bare metal, one `orb daemon run` process may own its own data directory
  with `json` or `sqlite` storage.
- The daemon and `json`/`sqlite` storage are mutually exclusive in the
  Kubernetes chart. A second pod sharing the volume would either lose the lock
  race or become a second writer. Chart rendering fails for this combination.

**Single-writer guard for json and sqlite**

When the API process starts with `json` or `sqlite` storage, it takes an
exclusive, non-blocking `flock` on `<data_dir>/.orb-writer.lock`. If the lock is
held, the process exits non-zero with a clear error; there is no retry loop.
The lock lives on the data volume, so it holds across pods on the same node and
across any pods that mount the same volume. It is the enforcement mechanism.
A pidfile is not: it is host-local and sits on ephemeral storage in Kubernetes.
Network filesystems without `flock` semantics are unsupported.

The Helm chart adds a second layer: `helm template` fails when `replicaCount > 1`
with `json`/`sqlite` storage, when the daemon is enabled with `json`/`sqlite`
storage, or when `daemon.replicas > 1` without leader election configured. The
API Deployment uses `strategy: Recreate` for `json`/`sqlite`.

### Later: multiple replicas with leader election

A later release adds `LeaderElectionPort` with these backends:

| Backend | Use | Notes |
|---|---|---|
| `none` | single replica | always leader; default |
| `k8s_lease` | Kubernetes | first multi-replica backend; requires shared (`sql`/`dynamodb`) storage |
| `sql` | bare metal | lease row; follows `k8s_lease` |

**Lease timing**

| Setting | Value |
|---|---|
| `lease_seconds` | 30 |
| `renew_seconds` | 10 |
| `demote_after_seconds` | 20 |

- The holder measures expiry on its local monotonic clock, starting when it sent
  the last successful renew. It never trusts a timestamp read from the lease.
  After 20 seconds without a successful renew it demotes: it starts no new
  cycles, cancels pending cycle work before the next orchestrator call, and
  stops writing.
- Followers never compare the lease's wall-clock timestamp with their own
  clock. They record the local monotonic time at which they last saw the
  holder's renewal change, and may attempt takeover only after 30 seconds have
  passed since then. The holder therefore always demotes at least 10 seconds
  before a follower can take over, regardless of clock skew between nodes.
- A SQL lease uses the database clock for expiry, with the same numbers.
- If the lease store is unavailable, every replica demotes after 20 seconds. No
  scaling happens; the API is unaffected.

**Fencing**

Checking `is_leader` before acting is not sufficient. A leader can pause (GC,
network partition, process stop) after the check and then call the provider with
an expired lease, and neither a Kubernetes Lease nor an AWS API can verify a
fencing token. These mechanisms close the gap:

1. **Conditional intent write.** Before any provider call, the reconciler writes
   its intent (source, template, window, desired count, client token) to
   storage, conditioned on the current fencing token. A stale leader's write
   fails and the call never happens. The SQL lease backend uses a condition on
   the lease row. Kubernetes Leases have no fencing token, so with `k8s_lease`
   the stored token is the holder identity plus the Lease's transition count,
   written to a token row in shared `sql` or `dynamodb` storage in the same
   compare-and-swap that follows a successful acquire; older tokens are
   rejected, and the intent write is conditioned on that row (a transaction
   condition on DynamoDB).
2. **Idempotency at the provider** (next section).
3. **Orphan adoption** (next section).

The residual risk is bounded: even if every mechanism fails, the number of
daemon-owned machines cannot exceed `limits.max_instances`.

Followers keep polling and report `role: follower` in `orb daemon status`.

## AWS-side idempotency and orphan adoption

**Client tokens.** Each acquire carries a deterministic key:

```
client_token = sha256(source, template_id, window_start, desired_count)
window_start = floor(now / interval_seconds) * interval_seconds
```

A different `desired_count` in the same window yields a different token, so AWS
does not deduplicate those two requests. The conditional intent write and the
`max_instances` ceiling bound that case.

The token is passed as `ClientToken` to `RunInstances`, EC2 Fleet creation and
the other AWS create calls, so AWS deduplicates a repeated request from a second
replica or a retry. For providers without a client-token concept (Kubernetes
pods), deterministic object names and labels are derived from the same key so a
duplicate create returns `AlreadyExists`.

**Orphan adoption.** Every cycle, the reconciler lists machines labelled
`orb:managed-by=daemon:<source>` that have no matching ORB record. This happens
when a leader dies between the provider call and the record write, or when a
conditional write loses a race. Such machines are adopted into a record, or
returned if the policy no longer wants them.

## Scale-down

Scale-down is two-phase: mark, then return.

1. When the target is below the actual count, the reconciler selects victims and
   **marks** them as draining. Marked machines still count as actual for
   scale-up arithmetic.
2. A marked machine is **returned** only when the grace period has elapsed and
   the latest reading still supports the lower target at that moment.
3. If demand rebounds, marks are cleared and scale-up reuses draining machines
   before requesting new ones. Scale-up never waits on grace.

**Grace period.** `scale_down.grace_period_seconds` per source. The default is
the existing `request.default_grace_period` (300 seconds). The value is passed
to providers that support graceful termination.

**Time source.** `draining_since` is the storage-side timestamp of the mark
write (database time, or the write time recorded by DynamoDB). With the `none`
backend it is the process clock, which is the only clock. The grace comparison
uses the same source, so a timestamp written by one replica is never compared
with another replica's wall clock.

**Fencing.** Mark and unmark writes use the same conditional write as the intent
record, so a stale leader can neither mark nor unmark.

**Machine states.** The daemon tracks scale-down with these transitions:

- `active` to `draining`: marked by the reconciler (fenced write).
- `draining` to `active`: demand rebounded; mark cleared (fenced write).
- `draining` to `returning`: grace elapsed and the reading still supports the
  lower target; `ReturnMachinesOrchestrator` called.
- `returning` to `returned`: the provider confirms the return. A `returning`
  machine is never selected again.

**Victim selection order:** longest-marked first, then spot-flagged, then
youngest first.

**Exclusions:** machines with an active unfinished request, and machines already
returning or with a return in flight, so a return is never issued twice.

## Graceful shutdown

On `SIGTERM`, in this order:

1. Stop starting reconcile cycles and set `/readyz` to not ready.
2. Wait for the in-flight orchestrator call, up to `shutdown_timeout_seconds`
   (default 25). A provider mutation is never cancelled mid-flight.
3. Drain the domain event queue, up to `event_drain_timeout_seconds`
   (default 10).
4. Release the lease (multi-replica only; a no-op with backend `none`).
5. Stop pollers, close source clients, exit 0.

The lease is released after the event drain so a follower cannot start acting
while the old leader is still flushing events.

In Kubernetes, `terminationGracePeriodSeconds` must be at least
`shutdown_timeout_seconds + event_drain_timeout_seconds + 10` (45 with the
defaults). The chart computes the value from the configured timeouts.

After `SIGKILL`, the lease expires and a follower takes over after 30 seconds.
Because the loop is level-triggered, the next leader recomputes from storage and
runs the orphan-adoption pass, so no state is lost.

The daemon must handle `SIGTERM` through the asyncio loop's signal handler, not
default Python behaviour, and `orb server stop` style escalation (`SIGTERM`
then `SIGKILL` on the process group) must not preempt the drain.

## Health and readiness

The daemon serves a minimal listener on `daemon.health_port` (default 8081),
with no path prefix.

| Endpoint | Healthy when |
|---|---|
| `/livez` | The event loop is responsive and the supervisor heartbeat is within three times the interval. Liveness never depends on provider health. |
| `/readyz` | Config is valid, storage is reachable, and every enabled source has been read successfully within `max_staleness_seconds`. Independent of leadership, so followers stay in the Service. |
| `/metrics` | Prometheus metrics prefixed `orb_daemon_`. |

Pair `/readyz` with a generous startup probe (for example 30 failures at a 5
second period) so a slow provider credential exchange at boot does not stall a
rolling update.

## Configuration

The `daemon` block is additive and defaults to disabled. Older ORB versions that
reject unknown configuration keys will reject a config containing `daemon`.

```yaml
daemon:
  enabled: false
  dry_run: true                  # stays true until an operator changes it
  interval_seconds: 15
  max_staleness_seconds: 60
  health_port: 8081
  shutdown_timeout_seconds: 25
  event_drain_timeout_seconds: 10
  leader_election:
    backend: none                # none | k8s_lease | sql
    lease_seconds: 30
    renew_seconds: 10
    demote_after_seconds: 20
  limits:
    max_instances: 100           # hard ceiling over recorded + adopted machines
    max_actions_per_minute: 6
  sources:
    - name: sqs-jobs
      type: sqs
      config:
        queue_url: https://sqs.us-east-1.amazonaws.com/123456789012/jobs
        region: us-east-1
      template_id: batch-spot
      policy:
        type: target_tracking
        target_per_machine: 10
        min: 0
        max: 50
        cooldown_up_seconds: 60
        cooldown_down_seconds: 300
      scale_down:
        grace_period_seconds: 300   # default: request.default_grace_period
```

A Spark Standalone source follows the same shape:

```yaml
    - name: spark-batch
      type: spark_standalone
      config:
        master_url: http://spark-master:8080
      template_id: spark-worker
      policy:
        type: target_tracking
        target_per_machine: 8      # cores per worker
        min: 1
        max: 40
        cooldown_up_seconds: 60
        cooldown_down_seconds: 600
```

### Scaling-policy schema

Source entry fields:

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `name` | string | yes | | unique across sources |
| `type` | string | yes | | a registered demand-source name |
| `config` | map | yes | | validated by the source |
| `template_id` | string | yes | | must reference an existing template |
| `policy` | map | yes | | see below |
| `scale_down.grace_period_seconds` | int | no | `request.default_grace_period` | >= 0 |

Common policy fields:

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `type` | string | yes | | `target_tracking` or `step` (or a registered policy) |
| `min` | int | no | 0 | >= 0 |
| `max` | int | yes | | >= `min` |
| `cooldown_up_seconds` | int | no | 60 (proposed default) | >= 0 |
| `cooldown_down_seconds` | int | no | 300 (proposed default) | >= 0 |

`target_tracking` adds `target_per_machine` (float, required, > 0):

```
desired = clamp(ceil(reading.value / target_per_machine), min, max)
```

`step` adds `steps`, an ordered list evaluated against `reading.value`. Each
entry has exactly one of `above` or `below` (a threshold on `reading.value`)
and exactly one of `add` or `remove` (a positive integer machine count). List
`above` entries from the largest threshold down and `below` entries from the
smallest up. The first matching entry in each direction applies to the current
actual count, and the result is clamped to `min`/`max`:

```yaml
policy:
  type: step
  min: 0
  max: 50
  steps:
    - { above: 100, add: 5 }
    - { above: 20,  add: 1 }
    - { below: 10,  remove: 2 }
```

Cooldown semantics: `cooldown_up_seconds` starts at the last executed
scale-up action and suppresses further scale-up until it elapses;
`cooldown_down_seconds` does the same for scale-down marks and returns. A
suppressed decision is reported with `suppressed_by: cooldown`. Proposed: dry-run
actions do not start cooldowns.

Policy type registry: `policy.type` resolves to a `ScalingPolicyPort`
implementation through the proposed entry-point group `orb.scaling_policies`.
`target_tracking` and `step` are built in; third parties register additional
types the same way as demand sources.

Rules:

- Source credentials come from the environment or from files (`*_file`,
  `${ENV}`), never inline in the config file. SQS uses ambient AWS credentials.
- Schema validation rejects nonsensical values (zero or negative limits, `min >
  max`) at load and reload, and the previous configuration stays active.
- `SIGHUP` (or the admin `reload-config` call) re-reads configuration at a cycle
  boundary. Reloadable: `dry_run`, policy parameters, `limits`, intervals. Not
  reloadable: the source list, leader-election backend, storage and provider
  set; these need a restart.
- `leader_election.backend: k8s_lease` is rejected with `json` or `sqlite`
  storage, because the fencing token needs shared storage.

## Failure modes

| Failure | Behaviour |
|---|---|
| Demand source down or slow | Source marked stale; reconciler skips; `orb_daemon_source_stale` set. No scaling on missing data. |
| Provider call fails or partially fulfils | Next cycle sees the real actual count and retries within rate limits. Repeated failures back off exponentially and show in `status`. |
| Flapping demand | Cooldown plus mark-then-return grace; marks clear when demand rebounds. |
| Replica race | Conditional intent write, deterministic client token, orphan adoption, and the `max_instances` ceiling. |
| Lease store outage | No scaling; API unaffected. |
| Machines the daemon did not create | Never touched; selection is restricted to the ownership label. |
| Restart during scale-down | Marks are stored under the fencing condition, so the new process resumes from storage. |

## Rollback

Daemon mode is opt-in. To roll back, set `daemon.enabled: false` or stop the
process. Machines the daemon created remain ordinary ORB machines and can be
returned with the existing commands. Lease and token tables added for leader
election are additive and safe to leave in place.

## Delivery plan

Planned capability milestones.

| Milestone | Capability |
|---|---|
| S1 | Core ports and registry: `DemandSourcePort`, `DemandSourceRegistry` with the `orb.demand_sources` entry-point group, `ScalingPolicyPort`, DTOs, and a fake source for tests. |
| S2 | Reconciler with dry run: reconcile cycle wired to the orchestrators, ownership label, deterministic client tokens, orphan adoption, in-memory decision records, simulated scale-up and scale-down tests. |
| S3 | Scaling policies: step scaling, target tracking, cooldown, clamping, and the mark/grace scale-down state machine with exclusions. |
| S4 | Single-replica daemon: `daemon` configuration schema, `orb daemon run` and `orb daemon status`, the data-directory writer lock, ordered shutdown, limited config reload, health listener. |
| S5 | Leader election: `LeaderElectionPort`, `k8s_lease` backend with the timing above, fenced intent and mark writes, multi-replica failover test. SQL lease follows. |
| S6 | Limits and observability: `max_instances`, rate limiting, `orb_daemon_*` metrics, structured decision log. |
| S7 | Demand sources: SQS and Kubernetes pending pods. The Spark Standalone source is delivered with the Spark integration work. |
| S8 | Documentation: integration guide and a guide to writing a demand source. |

S5 requires optimistic locking on SQL and DynamoDB storage. S4 requires the
event-queue drain hook.

## Test strategy

- Unit: policies with a simulated clock; reconciler with a fake source and fake
  orchestrators; registry and entry-point discovery; configuration validation;
  grace state-machine table tests.
- Concurrency: two reconcilers sharing one SQL database and a fake clock; at
  most one acts per window; with leader election disabled, the capacity caps
  still hold (negative control).
- Integration: SQS against a fake or a real queue; the Kubernetes pending-pod
  source against a kind cluster in CI.
- Shutdown: a subprocess test sends `SIGTERM` and asserts exit code 0, event
  queue drained, in-flight call completed and no orphan tasks. With leader
  election, the lease is released after the drain and a follower acquires it
  within seconds.
- Architecture: new ports are registered and the daemon package imports no
  provider code.

## Open items

Deferred beyond the first release:

- DynamoDB lease backend and database advisory-lock alternatives.
- A per-source `can_release` hook so a source can veto returning a machine
  (for example, a Spark worker that is still running executors).
- Budget-based limiting.
- Co-hosting the daemon inside the API process.
- Reload of the source list without a restart.
- Ownership tracking by tags beyond the single ownership label.
- A shared secret model for Spark worker bootstrap.
