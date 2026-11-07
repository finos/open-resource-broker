# Moto structural limits (what moto cannot test)

The AWS provider is tested at two levels:

- **moto (mocked)** — `tests/providers/aws/mocked/` and `tests/providers/aws/unit/`.
  Fast, deterministic, run in CI on every change.
- **live (onaws)** — `tests/providers/aws/live/`. Slow (~45 min/run), run against
  a real AWS account. See [System Tests (onaws)](system-tests-onaws.md).

Moto is a simulator, not AWS. Several behaviours that ORB depends on in production
either behave differently under moto or are absent entirely. Any scenario that
turns on one of the limits below is **live-only**: a moto test for it would either
be impossible to write or would pass against behaviour that AWS does not actually
exhibit, giving false confidence.

This page enumerates the five structural limits found while pairing every live
test with its moto counterpart. For each: what real AWS does, what moto does, and
where the behaviour is (or must be) covered.

## L1 — SpotFleet never fulfils

**AWS:** After `request_spot_fleet`, the fleet transitions to `active` and
asynchronously launches instances until `FulfilledCapacity` reaches
`TargetCapacity`. `describe_spot_fleet_instances` then returns the active
instances.

**Moto:** The request is recorded and reports `active`, but moto does not launch
backing instances to fulfil the target. `FulfilledCapacity` does not converge and
`describe_spot_fleet_instances` stays empty.

**Consequence:** The "wait for SpotFleet to fulfil, then read active instances"
polling loop and any weighted-capacity fulfilment arithmetic cannot be exercised
end-to-end under moto.

**Coverage:** Live — `tests/providers/aws/live/test_onaws.py` (SpotFleet
scenarios). The weighted-capacity cancellation arithmetic that this fulfilment
gap otherwise hides is regression-covered at unit level in
`tests/providers/aws/unit/infrastructure/handlers/test_fleet_release_weighted_capacity.py`.

## L2 — No `cancelled_running` state

**AWS:** Cancelling a SpotFleet *without* terminating instances moves the request
to `cancelled_running` — cancelled, but instances still alive — before it later
settles to `cancelled_terminated`. ORB distinguishes these to decide whether a
follow-up terminate is required.

**Moto:** Moto has no `cancelled_running` transition. A cancel goes straight to a
terminal `cancelled` state, so the intermediate window ORB reacts to never
appears.

**Consequence:** Logic branching on `cancelled_running` versus
`cancelled_terminated` cannot be reached under moto.

**Coverage:** Live — `tests/providers/aws/live/test_multi_spot_fleet_termination.py`.

## L3 — Synchronous `describe_instances` (no eventual consistency)

**AWS:** State changes are eventually consistent. Immediately after a terminate,
`describe_instances` can still report `running`, and a fleet just emptied can
still report stale instances for a short window. ORB polls to tolerate this.

**Moto:** State changes are applied synchronously and are visible on the very next
call. There is no stale-read window.

**Consequence:** Bugs that only surface under eventual consistency — e.g. ORB
stamping a return request `complete` while AWS still reports the instance
`running`, or cancelling a fleet based on a stale empty `describe` — are invisible
under moto because the reads are always immediately correct.

**Coverage:** Live — `tests/providers/aws/live/test_sdk_onaws.py` and
`test_cleanup_e2e_onaws.py`. At moto level the *post-return terminal state* is
asserted directly (`tests/providers/aws/mocked/test_sdk_onmoto.py`,
`test_return_flow.py::test_instances_terminated_in_aws_after_return`) so at least
the final state — not the consistency window — is guarded.

## L4 — No fleet `modifying` transition

**AWS:** Changing an EC2 Fleet's `TotalTargetCapacity` (scale-down on partial
return) moves the fleet through a `modifying` state before returning to `active`.
ORB tolerates `modifying` as a non-terminal state while polling.

**Moto:** Capacity changes are applied instantly; the fleet stays `active` (or
jumps to a terminal deleted state). The `modifying` transition never occurs.

**Consequence:** The `modifying` branch of fleet-state handling cannot be reached
under moto, and partial-return scale-down cannot be observed mid-flight.

**Coverage:** Live — `tests/providers/aws/live/test_multi_ec2_fleet_termination.py`.
The partial-return arithmetic itself (new target capacity computation) is unit
covered in `test_fleet_release_weighted_capacity.py` and
`tests/providers/aws/mocked/test_partial_return.py`.

## L5 — Instant ASG-instance state transitions

**AWS:** Scaling an Auto Scaling Group down (or deleting it) terminates instances
asynchronously; they pass through `shutting-down` before `terminated`, and the ASG
briefly reports instances in `Terminating` lifecycle state.

**Moto:** ASG capacity changes and deletions are reflected instantly — instances
disappear or flip to `terminated` with no intermediate lifecycle state.

**Consequence:** Any logic that observes the `Terminating` / `shutting-down`
window during ASG teardown cannot be exercised under moto.

**Coverage:** Live — `tests/providers/aws/live/test_multi_asg_termination.py`.

## Live/moto pairing

Every live test file under `tests/providers/aws/live/` is paired here with the
moto (or unit) tests that cover the same behaviour, or with the reason no moto
counterpart exists. Paths in the moto column are relative to
`tests/providers/aws/`. When you add a live test file, add a row; the check in
`tests/unit/test_moto_live_pairing_matrix.py` fails until you do.

| Live test file | Moto counterpart | Not covered under moto |
|----------------|------------------|------------------------|
| `test_onaws.py` | `mocked/test_hf_contract.py`, `mocked/test_partial_return.py`, `mocked/test_cli_onmoto.py` | SpotFleet fulfilment (L1); partial-return scale-down mid-flight (L4) |
| `test_rest_api_onaws.py` | `mocked/test_rest_api_onmoto.py`, `mocked/test_rest_real_container_flows.py` | SpotFleet fulfilment (L1); post-return consistency window (L3) |
| `test_sdk_onaws.py` | `mocked/test_sdk_onmoto.py` | Post-return consistency window (L3) |
| `test_cleanup_e2e_onaws.py` | `mocked/test_cleanup_e2e.py` | Stale reads after fleet or ASG deletion (L3) |
| `test_multi_asg_termination.py` | `mocked/test_multi_resource_onmoto.py` | ASG `Terminating` / `shutting-down` window (L5) |
| `test_multi_ec2_fleet_termination.py` | `mocked/test_cleanup_e2e.py`, `mocked/test_partial_return.py` | Fleet `modifying` transition (L4); moto tests cover one fleet per return, not two |
| `test_multi_spot_fleet_termination.py` | `mocked/test_cleanup_e2e.py`, `mocked/test_partial_return.py` | Fulfilment (L1) and `cancelled_running` (L2); moto tests cover one fleet per return, not two |
| `test_multi_resource_termination.py` | `mocked/test_multi_resource_onmoto.py` | SpotFleet and EC2 Fleet members of a mixed batch (L1, L4); moto batches cover RunInstances and ASG |
| `test_capacity_helpers_regex.py` | none | Not a live test: it unit-tests the live suite's own helpers and runs without AWS |

## Summary

| Limit | AWS behaviour | Moto behaviour | Live coverage |
|-------|---------------|----------------|---------------|
| L1 | SpotFleet fulfils asynchronously | Never fulfils | `test_onaws.py` (SpotFleet) |
| L2 | `cancelled_running` before terminal | No such state | `test_multi_spot_fleet_termination.py` |
| L3 | Eventually consistent reads | Synchronous reads | `test_sdk_onaws.py`, `test_cleanup_e2e_onaws.py` |
| L4 | Fleet `modifying` on capacity change | Instant, no `modifying` | `test_multi_ec2_fleet_termination.py` |
| L5 | Async ASG-instance teardown | Instant teardown | `test_multi_asg_termination.py` |

When adding a moto test, check this list first: if the scenario depends on any of
L1–L5, it belongs in the live suite (or a unit test that isolates the pure logic),
not in a moto test that would pass against unrealistic simulator behaviour.
