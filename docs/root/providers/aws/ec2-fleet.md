# AWS EC2 Fleet Provider

The EC2 Fleet handler provisions capacity through the [EC2 CreateFleet API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateFleet.html). It is the most flexible of the AWS provisioning types: one fleet can mix on-demand and spot capacity, span many instance types and availability zones, and be either synchronous or continuously reconciled depending on the fleet type.

This is the default `provider_api` in the shipped configuration. Use [Spot Fleet](spot-fleet.md) when you need its per-override priority semantics, [ASG](asg.md) when you want a long-lived health-checked group, or [RunInstances](run-instances.md) for the simplest possible launch.

## Quick start

```bash
pip install "orb-py[aws]"
orb init
```

### 1. Create a template

```json
{
  "template_id": "compute-fleet",
  "name": "Compute Fleet",
  "description": "Mixed on-demand and spot capacity",
  "provider_api": "EC2Fleet",
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "machine_types": {"c5.xlarge": 2, "r5.xlarge": 3},
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7a8b9"],
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "heterogeneous",
  "percent_on_demand": 30,
  "allocation_strategy": "capacityOptimized",
  "max_price": 0.10,
  "max_machines": 100,
  "metadata": {"fleet_type": "request"},
  "tags": {"Environment": "prod"}
}
```

### 2. Request capacity

```bash
orb machines request compute-fleet 10
```

### 3. Check status

```bash
orb requests status <request-id>
```

### 4. Return capacity

```bash
orb machines return i-0abc1234def567890 i-0def5678abc123456
```

## Template configuration

The handler uses `provider_api: "EC2Fleet"`. Each request creates a launch template, then calls `create_fleet`.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `provider_api` | Yes | `string` | Must be `"EC2Fleet"` |
| `image_id` | Yes | `string` | AMI id or SSM parameter path. |
| `fleet_type` | Yes | `string` | `"instant"`, `"request"`, or `"maintain"`. Defaults to `request` when unset; see [Fleet types](#fleet-types). |
| `machine_types` | No | `object` | Instance type → weighted capacity. Expanded across subnets into overrides. |
| `machine_types_ondemand` | No | `object` | Separate instance-type map for the on-demand portion of a heterogeneous fleet. |
| `subnet_ids` | No | `list[string]` | Target subnets, combined with each instance type. |
| `security_group_ids` | No | `list[string]` | Security groups. |
| `price_type` | No | `string` | `"ondemand"` (default), `"spot"`, or `"heterogeneous"`. |
| `percent_on_demand` | No | `integer` | 0–100. On-demand share for heterogeneous fleets. |
| `allocation_strategy` | No | `string` | Spot allocation strategy. Converted to the hyphenated EC2 Fleet form. |
| `allocation_strategy_on_demand` | No | `string` | On-demand allocation strategy, used for heterogeneous fleets. |
| `max_price` | No | `float` | Becomes `SpotOptions.MaxTotalPrice` — a total spend cap, not a per-instance price. |
| `abis_instance_requirements` | No | `object` | Attribute-based instance selection, replacing instance-type overrides. |
| `max_machines` | No | `integer` | Maximum capacity this template may provision. |
| `context` | No | `string` | Passed through as the fleet `Context` field. |
| `tags` | No | `object` | Tags applied to the fleet, and to instances for `instant` fleets. |
| `provider_api_spec` | No | `object` | Native `CreateFleet` parameters. See [Native spec](#native-spec). |

`fleet_type` can be set as a top-level field, in `metadata.fleet_type`, or in `metadata.providerConfig.fleet_type`. An unrecognised value is ignored with a debug log and the default applies.

### Fleet types

Fleet type is the most consequential choice on an EC2 Fleet template, because it changes both provisioning behaviour and how ORB judges fulfilment.

| `fleet_type` | Provisioning | Replaces lost capacity | Fulfilment semantics |
|---|---|---|---|
| `instant` | Synchronous — instance ids come back in the `create_fleet` response | No | Count-based, like [RunInstances](run-instances.md#fulfilment-semantics) |
| `request` | Asynchronous — one-time attempt to reach target capacity | No | Capacity-unit based |
| `maintain` | Asynchronous — EC2 keeps the fleet at target capacity | Yes | Capacity-unit based |

`maintain` also sets `ReplaceUnhealthyInstances` and `ExcessCapacityTerminationPolicy: termination`.

An `instant` fleet returns instance ids immediately, but those instances are still `pending`, so ORB sets `requires_async_polling: true` and keeps polling until they are running — the create call is not the final verdict. `request` and `maintain` fleets return only a fleet id, which *is* the final synchronous answer, so `requires_async_polling` is `false` for them and instance arrival is purely a polling concern.

Note that the shipped `aws_defaults.json` declares `default_fleet_type: instant` for the handler, while a template with no fleet type at all falls back to `request` during template validation.

### Pricing

`TotalTargetCapacity` is always the requested count. The rest depends on `price_type`:

| `price_type` | Generated `TargetCapacitySpecification` and options |
|---|---|
| `ondemand` | `DefaultTargetCapacityType: on-demand`. |
| `spot` | `DefaultTargetCapacityType: spot`, plus `SpotOptions.AllocationStrategy` when a strategy is set and `SpotOptions.MaxTotalPrice` when `max_price` is set. |
| `heterogeneous` | `OnDemandTargetCapacity: requested * percent_on_demand / 100` (floored), `SpotTargetCapacity` for the remainder, and `DefaultTargetCapacityType: on-demand`. Both `SpotOptions` and `OnDemandOptions` allocation strategies apply. |

`max_price` maps to `MaxTotalPrice`, which caps total spend for the fleet rather than the price of any one instance. This differs from [Spot Fleet](spot-fleet.md), where `max_price` becomes a per-override `SpotPrice`.

Because the on-demand count is floored, a 10-instance request at 5% on-demand yields zero on-demand capacity. Set `percent_on_demand` high enough to round up to at least one instance if you need guaranteed on-demand capacity.

### Allocation strategies

EC2 Fleet uses the hyphenated wire format. ORB accepts camelCase, hyphenated, or snake_case:

| Template value | Sent to AWS |
|---|---|
| `lowestPrice` | `lowest-price` |
| `capacityOptimized` | `capacity-optimized` |
| `capacityOptimizedPrioritized` | `capacity-optimized-prioritized` |
| `priceCapacityOptimized` | `price-capacity-optimized` |
| `prioritized` | `prioritized` |
| `diversified` | `diversified` |

When no strategy is set, spot and heterogeneous templates default to `price-capacity-optimized` and on-demand templates default to `lowest-price`. `allocation_strategy_on_demand` follows the same conversion and falls back to the spot strategy when unset.

### Overrides

`machine_types` and `subnet_ids` expand into the cartesian product of instance type and subnet, each override carrying `InstanceType` and `WeightedCapacity`. Unlike Spot Fleet, EC2 Fleet overrides carry no `Priority` or per-override price.

Weighted capacity means target capacity is measured in units, not instances: a fleet offering `{"c5.xlarge": 2, "r5.xlarge": 3}` can fill a 6-unit request with three `c5.xlarge`, two `r5.xlarge`, or a mix.

### Attribute-based instance selection

`abis_instance_requirements` replaces the instance-type overrides with `InstanceRequirements`, one override per subnet:

```json
{
  "provider_api": "EC2Fleet",
  "abis_instance_requirements": {
    "vcpu_count": {"min": 4, "max": 16},
    "memory_mib": {"min": 8192, "max": 65536},
    "cpu_manufacturers": ["intel", "amd"],
    "allowed_instance_types": ["m6i.*", "c7g.*"]
  }
}
```

When ABIS is present, `machine_types` is ignored. See [Attribute-Based Instance Selection](../../user_guide/abis.md).

### Native spec

A `provider_api_spec` is rendered and used as the `create_fleet` payload. ORB patches the launch template id and version into `LaunchTemplateConfigs[0].LaunchTemplateSpecification`, and injects ABIS `InstanceRequirements` into that entry's `Overrides` when the spec does not already carry them. See the [Native Spec Reference](../../api/native-spec-reference.md).

## Machine data

Instances carry the fleet id as `resource_id`:

```json
{
  "instance_id": "i-0abc1234def567890",
  "resource_id": "fleet-12a34b56-7c89-0def-1234-56789abcdef0",
  "status": "running",
  "private_ip": "10.0.1.42",
  "instance_type": "c5.xlarge",
  "provider_api": "EC2Fleet",
  "provider_data": {
    "cloud_host_id": "i-0abc1234def567890",
    "vcpus": 4,
    "availability_zone": "us-east-1a",
    "region": "us-east-1"
  }
}
```

For `instant` fleets ORB records the instance ids from the create response and reuses them on later polls. For `request` and `maintain` fleets it calls `describe_fleet_instances` to enumerate active instances.

Per-fleet target capacity is read from the AWS response (`TargetCapacitySpecification.TotalTargetCapacity`) rather than the ORB request total. This matters when a request is split across several fleets: comparing each fleet's running count to the whole-request total would make a fully-running N-way split look 1/N fulfilled per fleet and produce a wrong partial verdict.

## Fulfilment semantics

### Instant fleets

Count-based, matching RunInstances:

| Condition | State |
|---|---|
| `running >= requested` and no failures | `fulfilled` |
| Some instances pending | `in_progress` |
| Some running, none pending, short of target | `partial` (final, with a capacity diagnostic) |
| No instances yet | `in_progress` |
| Instances present and all failed | `failed` |

The `partial` verdict is final because an instant fleet is a synchronous, one-shot allocation — the missing capacity will never arrive.

### Request and maintain fleets

Capacity-unit based, shared with [Spot Fleet](spot-fleet.md#fulfilment-semantics):

| Condition | State |
|---|---|
| `FulfilledCapacity >= TargetCapacity`, nothing pending, nothing failed | `fulfilled` |
| Instances failed, none running, none pending | `failed` |
| Anything else | `in_progress` |

### Fleet errors and diagnostics

`create_fleet` can return per-override errors alongside a fleet id. ORB normalises them and classifies them into a structured diagnostic so the request records *why* capacity fell short — capacity, authorization, validation, and so on — rather than just that it did.

If the response carries errors **and** no instances, the request fails with a summary of every error. If it carries errors but some instances launched, ORB treats it as partial success and logs a warning. The error codes `InsufficientInstanceCapacity`, `SpotMaxPriceTooLow`, and `MaxSpotInstanceCountExceeded` additionally set `capacity_constrained` on the request's provider data.

### Multi-fleet aggregation

When one request spans several fleets, the combined verdict is resolved in priority order:

1. All fulfilled → `fulfilled`
2. Any in progress → `in_progress`
3. Any failed → `failed`
4. Any partial → `partial`
5. Otherwise → `in_progress`

`in_progress` is checked before `partial` deliberately: a request must not flip to a terminal partial while another fleet is still booting. That classification is only valid once every fleet has reached a terminal verdict. The aggregate partial is itself only final when every partial contributor is final.

## Release behaviour

Releases follow the shared fleet release flow:

1. **Group instances by fleet**, from the request's resource mapping when available, otherwise by reading the `aws:ec2:fleet-id` tag from `describe_instances` and falling back to a fleet search. Instances belonging to no fleet are terminated directly.
2. **Fetch fleet details** with `describe_fleets` when not supplied.
3. **Compute the decision** from fleet type, target capacity, and the summed `WeightedCapacity` of the returned instances.
4. **Reduce capacity first for `maintain`** fleets via `modify_fleet`, so EC2 does not launch replacements for instances about to be terminated.
5. **Terminate the instances.**
6. **Delete the fleet when empty.** `request` fleets re-check that no instances remain outside the batch before deletion, guarding against eventual-consistency lag. `maintain` fleets delete directly on a full return, with a capacity-zero call first in the weighted-capacity edge case.
7. **Clean up the launch template.**

Instant fleets are a special case: AWS deletes the fleet record itself, so there is no capacity to modify. ORB still attempts an explicit delete and swallows the error if the record is already gone. Because the record may be missing, ORB recovers the request id for launch template cleanup from the `orb:request-id` instance tag — which is why instant fleets tag instances with `orb:fleet-type: instant` at creation.

If any fleet in a multi-fleet return fails to release, the whole return raises after the other fleets are processed.

## IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFleet",
        "ec2:ModifyFleet",
        "ec2:DeleteFleets",
        "ec2:DescribeFleets",
        "ec2:DescribeFleetInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

`ec2:ModifyFleet` is only exercised for `maintain` fleets during capacity reduction. Spot capacity additionally requires the EC2 Spot service-linked role in the account. Launch template, tagging, and SSM permissions are shared across handlers — see the [IAM Permissions Guide](../../deployment/iam-permissions.md).

## Limitations

- **`max_price` is a total cap**, not a per-instance price — different from Spot Fleet.
- **On-demand count is floored**, so a small `percent_on_demand` on a small request yields no on-demand capacity.
- **No per-override priority** — use [Spot Fleet](spot-fleet.md) if you need `Priority` with `capacityOptimizedPrioritized`.
- **Instant fleet records are deleted by AWS**, so ORB depends on instance tags to recover request context for cleanup.
- **Request-type fleets do not replace lost capacity** — only `maintain` reconciles.

## Related

- [Spot Fleet](spot-fleet.md) — per-override priority and price, spot-focused
- [Auto Scaling Groups](asg.md) — health-checked, self-replacing capacity
- [EC2 Instances (RunInstances)](run-instances.md) — synchronous single-type launches
- [Native Spec Reference](../../api/native-spec-reference.md) — submitting raw `CreateFleet` parameters
