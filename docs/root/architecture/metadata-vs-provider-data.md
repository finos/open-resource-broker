# metadata vs provider_data

**Rule:** provider adapters write to `provider_data`; the application layer writes to `metadata`. Neither field is interchangeable with the other.

## Rationale

`Machine` and `Request` aggregates each carry two open-ended dicts: `metadata` and `provider_data`. When both fields exist but their purpose is undefined, engineers reach for whichever name feels right in the moment. The result is provider-specific fields (`vcpus`, `availability_zone`, `fleet_id`) scattered across `metadata`, and application-layer annotations leaking into `provider_data`. This makes refactoring hard, pollutes the domain model with cloud concepts, and forces every reader to grep both dicts to understand what a field means.

Defining a clear ownership rule eliminates the ambiguity.

## The Rule

**`provider_data`** holds provider-specific fields. Only provider adapters write to it. Schedulers and application services may read from it selectively, with explicit knowledge of the provider. Examples: `cloud_host_id`, `launch_template_id`, `launch_template_version`, `health_checks`, `fleet_id`, `spot_price`, `availability_zone`, `region`, `vcpus`, `instance_profile`, `resource_type`.

**`metadata`** holds provider-agnostic extensibility data. Only the application layer writes to it. It must never contain provider-specific fields. Examples: correlation IDs, operator annotations, custom labels applied by the application, domain-event payloads, `dry_run`, `timeout`, `tags` (when not AWS tags), `fulfillment_config`.

### Who writes, who reads

| Layer | `provider_data` | `metadata` |
|---|---|---|
| Domain aggregate | owns the field definition; exposes `set_provider_data` / `update_metadata` | owns the field definition; exposes `update_metadata` |
| Provider adapter (`src/orb/providers/`) | **writes** | reads only |
| Application service / orchestrator | reads | **writes** |
| Infrastructure (storage, scheduler) | reads | reads |
| Interface / API / CLI | reads | reads |

## Field Reference

| Field | Correct bucket | Notes |
|---|---|---|
| `cloud_host_id` | `provider_data` | AWS EC2 instance ID (HF name) |
| `launch_template_id` | `provider_data` | EC2 launch template |
| `launch_template_version` | `provider_data` | EC2 launch template version |
| `fleet_id` | `provider_data` | EC2 Fleet or Spot Fleet ID |
| `spot_price` | `provider_data` | Current spot bid price |
| `health_checks` | `provider_data` | Provider-reported health status |
| `availability_zone` | `provider_data` | AWS AZ — currently misplaced in `metadata` |
| `vcpus` | `provider_data` | Instance vCPU count — currently misplaced in `metadata` |
| `instance_profile` | `provider_data` | IAM instance profile ARN |
| `resource_type` | `provider_data` | Provider resource kind (e.g. `asg`, `spot_fleet`) |
| `dry_run` | `metadata` | Application-layer flag; no provider meaning |
| `timeout` | `metadata` | Request timeout set by caller |
| `fulfillment_config` | `metadata` | Orchestration policy set by application |
| `fulfillment_attempts` | `metadata` | Attempt history tracked by orchestrator |
| `correlation_id` | `metadata` | Cross-service tracing identifier |

## Enforcement

`tests/unit/architecture/test_metadata_provider_data_boundary.py` enforces this rule with three tests:

- **`test_provider_data_writes_restricted_to_providers`** — asserts that every write to `.provider_data` on an object originates inside `src/orb/providers/` or inside a domain aggregate method (which owns the field definition). Writes from application, infrastructure, interface, or CLI layers fail the test.

- **`test_metadata_writes_not_in_providers`** — asserts that no provider adapter writes to aggregate `.metadata`. Providers must use `provider_data` for their own fields.

- **`test_violations_inventory_is_known`** — compares the current violation set against a frozen baseline in `tests/unit/architecture/metadata_provider_data_violations.json`. New violations fail loudly. Fixed violations also fail, so the baseline is kept tight and improvements are locked in.

## Migration Note

A separate ticket migrates legacy fields that are in the wrong bucket. Specifically, `vcpus` and `availability_zone` currently live in `metadata` (written by application-layer DTOs reading from provider results) and will move to `provider_data`. Until that migration is complete, readers must handle both locations.

## Exceptions — the `_normalize_on_read` pattern

Storage round-trips written before this rule was established may have persisted provider-specific fields inside `metadata`. Readers that encounter such records must migrate them on-read: copy the field to `provider_data`, remove it from `metadata`, and continue. This is the `_normalize_on_read` pattern. Storage writes the corrected layout going forward. Once all records have been rewritten, the on-read migration can be removed.

Code inside a function named `_normalize_on_read` is exempt from the architecture test for this reason.
