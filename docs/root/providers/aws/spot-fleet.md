# AWS Spot Fleet Provider

> **AWS discourages Spot Fleet for new work.** In [Work with Spot Fleet](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/work-with-spot-fleets.html), AWS states: "We strongly discourage using Spot Fleet because it uses a legacy API with no planned investment. If you want to manage your instance lifecycle, use EC2 Fleet instead. If you don't want to manage your instance lifecycle, use an Auto Scaling group instead. Use Spot Fleet only if you need console support for a use case where you would use EC2 Fleet."
>
> ORB continues to support Spot Fleet for existing deployments, and this handler is maintained. For new templates, prefer [EC2 Fleet](ec2-fleet.md) — it covers spot provisioning with the same allocation strategies plus `instant` synchronous fulfilment — or [Auto Scaling Groups](asg.md) if you want AWS to maintain capacity for you. See [Migrating to EC2 Fleet](#migrating-to-ec2-fleet) below.

The Spot Fleet handler provisions capacity through the [EC2 Spot Fleet API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_RequestSpotFleet.html). A Spot Fleet request asks EC2 for a target capacity and lets it choose instance types and availability zones according to an allocation strategy, which is how you get spot pricing without pinning yourself to a single capacity pool.

Use it for interruption-tolerant work: batch processing, CI, and simulation. Spot Fleet's one remaining advantage over EC2 Fleet is its per-override `Priority` and `SpotPrice` semantics, which have no direct EC2 Fleet equivalent.

## Quick start

```bash
pip install "orb-py[aws]"
orb init
```

### 1. Create the service-linked role

Spot Fleet needs a service-linked role so EC2 can launch and terminate instances on your behalf. Create it once per account:

```bash
aws iam create-service-linked-role --aws-service-name spotfleet.amazonaws.com
```

### 2. Create a template

```json
{
  "template_id": "batch-spot",
  "name": "Batch Spot Fleet",
  "description": "Diversified spot capacity for batch work",
  "provider_api": "SpotFleet",
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "machine_types": {"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7a8b9"],
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "spot",
  "allocation_strategy": "capacityOptimized",
  "fleet_type": "request",
  "fleet_role": "arn:aws:iam::123456789012:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet",
  "max_price": 0.10,
  "max_machines": 100,
  "tags": {"Environment": "dev"}
}
```

### 3. Request capacity

```bash
orb machines request batch-spot 10
```

### 4. Check status

```bash
orb requests status <request-id>
```

### 5. Return capacity

```bash
orb machines return i-0abc1234def567890 i-0def5678abc123456
```

## Template configuration

The handler uses `provider_api: "SpotFleet"`. Each request creates a launch template, then calls `request_spot_fleet` with a `SpotFleetRequestConfig`.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `provider_api` | Yes | `string` | Must be `"SpotFleet"` |
| `fleet_role` | Yes | `string` | IAM fleet role ARN. Validated before the fleet is created — see [Fleet role](#fleet-role). |
| `image_id` | Yes | `string` | AMI id or SSM parameter path. |
| `fleet_type` | No | `string` | `"request"` (default) or `"maintain"`. See [Fleet types](#fleet-types). |
| `machine_types` | No | `object` | Instance type → weighted capacity. Expanded across subnets into overrides. |
| `subnet_ids` | No | `list[string]` | Target subnets. Each is combined with each instance type. |
| `security_group_ids` | No | `list[string]` | Security groups. |
| `price_type` | No | `string` | `"spot"`, `"ondemand"`, or `"heterogeneous"`. Validated — any other value is rejected. |
| `percent_on_demand` | No | `integer` | On-demand share. Required when `price_type` is `"heterogeneous"`. |
| `allocation_strategy` | No | `string` | Spot allocation strategy, camelCase on the wire. |
| `max_price` | No | `float` | Maximum spot price per hour. Must be greater than zero. |
| `spot_fleet_request_expiry` | No | `integer` | **Minutes** until the request expires, converted to `ValidUntil`. |
| `abis_instance_requirements` | No | `object` | Attribute-based instance selection, replacing instance-type overrides. |
| `max_machines` | No | `integer` | Maximum capacity this template may provision. |
| `context` | No | `string` | Passed through as the fleet `Context` field. |
| `tags` | No | `object` | Tags applied to the `spot-fleet-request` resource. |
| `provider_api_spec` | No | `object` | Native `RequestSpotFleet` config. See [Native spec](#native-spec). |

### Fleet role

Unlike the other AWS handlers, Spot Fleet will not provision without a fleet role, and the role is validated up front. `fleet_role` may be set on the template or inherited from the provider's `config.fleet_role`. In a multi-provider setup the role is read from the provider instance serving the request; with a single unnamed provider, ORB uses that provider's role.

Accepted forms:

| Value | Handling |
|---|---|
| `arn:aws:iam::<account>:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet` | Used as-is. |
| `arn:aws:iam::<account>:role/aws-ec2-spot-fleet-tagging-role` | Used as-is. |
| `AWSServiceRoleForEC2SpotFleet` or `AmazonEC2SpotFleetTaggingRole` | Expanded to the full service-linked role ARN using `sts:GetCallerIdentity` for the account id. |
| An EC2 Fleet service-linked role ARN (`ec2fleet.amazonaws.com/AWSServiceRoleForEC2Fleet`) | Converted to the equivalent Spot Fleet role. |
| Any other ARN | Treated as a custom role and verified with `iam:GetRole`. A lookup failure fails the request. |

An ARN that mentions `AWSServiceRoleForEC2SpotFleet` but does not match the expected pattern is rejected with an explicit error rather than being passed to AWS.

### Fleet types

| `fleet_type` | Behaviour |
|---|---|
| `request` (default) | One-time request. EC2 fills the target capacity and does not replace interrupted instances. |
| `maintain` | EC2 keeps the fleet at target capacity, replacing interrupted or unhealthy instances. Sets `ReplaceUnhealthyInstances` and `TerminateInstancesWithExpiration`. |

Spot Fleet has no `instant` type — that is [EC2 Fleet](ec2-fleet.md#fleet-types) only. Both Spot Fleet types return only a request id from the create call; instances arrive later.

### Allocation strategies

Spot Fleet uses camelCase on the wire, so template values pass through nearly unchanged. ORB still normalises hyphenated and snake_case input:

| Template value | Sent to AWS |
|---|---|
| `lowestPrice` (default) | `lowestPrice` |
| `capacityOptimized` | `capacityOptimized` |
| `capacityOptimizedPrioritized` | `capacityOptimizedPrioritized` |
| `priceCapacityOptimized` | `priceCapacityOptimized` |
| `diversified` | `diversified` |

`prioritized` is not a Spot Fleet spot strategy and falls back to `lowestPrice`.

### Overrides, priority, and per-override price

`machine_types` and `subnet_ids` are expanded into the cartesian product of instance type and subnet. Spot Fleet overrides additionally carry:

- **`Priority`** — a 1-based index in `machine_types` order, used by the `capacityOptimizedPrioritized` strategy.
- **`SpotPrice`** — `max_price` repeated per override, when set.

So `{"t3.medium": 2, "t3.large": 2}` across two subnets yields four overrides, with `t3.medium` at priority 1 and `t3.large` at priority 2 in each subnet.

### Pricing mix

`TargetCapacity` is always the requested count. The on-demand portion is derived:

| Configuration | `OnDemandTargetCapacity` |
|---|---|
| `price_type: "spot"` | Not set — all capacity is spot. |
| `price_type: "ondemand"` | The full requested count. |
| `price_type: "heterogeneous"` with `percent_on_demand: N` | `max(1, requested * N / 100)` — at least one instance when a non-zero percentage is requested. |

`percent_on_demand` is required for `heterogeneous` and the request is rejected without it. If `machine_types_ondemand` is set, `machine_types` must be set too, and every on-demand weight must be a positive integer.

### Request expiry

`spot_fleet_request_expiry` is in **minutes**, not seconds. It becomes `ValidUntil` at `now + N minutes`:

```json
{
  "spot_fleet_request_expiry": 30
}
```

The shipped provider default is 30 minutes. Combined with `maintain` and `TerminateInstancesWithExpiration`, instances are terminated when the request expires — so set this deliberately for long-running work.

### Attribute-based instance selection

`abis_instance_requirements` replaces the instance-type overrides with `InstanceRequirements`, one override per subnet:

```json
{
  "provider_api": "SpotFleet",
  "abis_instance_requirements": {
    "vcpu_count": {"min": 2, "max": 8},
    "memory_mib": {"min": 4096, "max": 32768},
    "local_storage": "required"
  }
}
```

When ABIS is present, `machine_types` is ignored and no `Priority` is emitted. See [Attribute-Based Instance Selection](../../user_guide/abis.md).

### Native spec

A `provider_api_spec` is rendered and used as the `SpotFleetRequestConfig`. ORB patches the launch template id and version into every entry of `LaunchSpecifications`, and injects ABIS `InstanceRequirements` into `LaunchTemplateConfigs[0].Overrides` when the spec does not already carry them. See the [Native Spec Reference](../../api/native-spec-reference.md).

## Machine data

Instances carry the Spot Fleet request id as `resource_id`:

```json
{
  "instance_id": "i-0abc1234def567890",
  "resource_id": "sfr-73fbd2ce-aa30-494c-8788-1cee4ffcbc12",
  "status": "running",
  "private_ip": "10.0.1.42",
  "instance_type": "t3.large",
  "provider_api": "SpotFleet",
  "provider_data": {
    "cloud_host_id": "i-0abc1234def567890",
    "vcpus": 2,
    "availability_zone": "us-east-1a",
    "region": "us-east-1"
  }
}
```

## Fulfilment semantics

Spot Fleet uses capacity-unit semantics, identical to EC2 Fleet's maintain and request types: `FulfilledCapacity >= TargetCapacity` with nothing pending and nothing failed.

| Condition | State |
|---|---|
| Fulfilled capacity meets target, nothing pending, nothing failed | `fulfilled` |
| Instances failed, none running, none pending | `failed` |
| Anything else | `in_progress` |

Two intermediate cases are reported distinctly while no instances are visible yet: capacity allocated but instances not yet enumerable, and the fleet still waiting for capacity. Both are `in_progress`.

Because capacity is measured in weighted units, a fleet with `{"t3.xlarge": 4}` can fulfil a 4-unit request with a single instance.

For multi-fleet requests, all fleets must be fulfilled for the request to be fulfilled; any failure makes it failed; any partial makes it partial, and that aggregate is only terminal when every partial contributor is itself terminal.

## Release behaviour

Releases follow the shared fleet release flow, with the branch determined by fleet type:

1. **Fetch fleet details** with `describe_spot_fleet_requests` when the caller did not supply them.
2. **Compute the decision** from fleet type, current target capacity, and the summed `WeightedCapacity` of the instances being returned. Using weighted units rather than a count prevents AWS from refilling capacity that was decremented by the wrong amount.
3. **Reduce capacity first for `maintain`** fleets, so EC2 does not launch replacements for instances about to be terminated. `request` fleets skip this — they never replace.
4. **Terminate the instances.**
5. **Cancel the fleet when empty.** For `request` fleets ORB re-checks that no instances remain outside the batch before cancelling, guarding against eventual-consistency lag. For `maintain` fleets a full return cancels directly; a weighted-capacity edge case where arithmetic says partial but the fleet is physically empty triggers a capacity-zero call before cancellation.
6. **Clean up the launch template** ORB created for the request.

Cancelling a request with no instances named cancels the whole fleet and terminates whatever it holds.

When a `maintain` fleet shrinks, ORB passes `ExcessCapacityTerminationPolicy: NoTermination` to `modify_spot_fleet_request`, and reads the current target again immediately before lowering it. The returned instances are terminated explicitly, and only those, so the fleet does not terminate an extra instance and then launch a replacement. As for EC2 Fleet, this narrows rather than closes the window in which two returns on one fleet overwrite each other. A fleet that is already cancelled is not modified or cancelled again by a later return.

## Cancelling a request

Cancelling a request that is still being fulfilled calls `cancel_spot_fleet_requests` with `TerminateInstances: true`, which cancels pending requests and terminates the instances already launched (`cancelled_terminating`). A request AWS reports as not cancellable counts as stopped only when a describe confirms it is cancelled. Instances still attached afterwards are found with the `aws:ec2spot:fleet-request-id` tag and returned with the recorded machines.

## IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RequestSpotFleet",
        "ec2:CancelSpotFleetRequests",
        "ec2:ModifySpotFleetRequest",
        "ec2:DescribeSpotFleetRequests",
        "ec2:DescribeSpotFleetInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    }
  ]
}
```

`sts:GetCallerIdentity` resolves short role names to full ARNs. `iam:GetRole` is additionally needed when `fleet_role` is a custom role, since validation calls it. `iam:PassRole` on the fleet role is required for EC2 to assume it. Launch template, tagging, and SSM permissions are shared across handlers — see the [IAM Permissions Guide](../../deployment/iam-permissions.md).

## Limitations

- **Fleet role is mandatory** and validated before provisioning; a bad role fails the request rather than the AWS call.
- **No `instant` fleet type** — provisioning is always asynchronous, so the create call never returns instance ids.
- **Expiry is in minutes** and defaults to 30 in the shipped config; combined with `maintain`, expiry terminates instances.
- **Spot interruptions are not surfaced as a distinct state** — an interrupted instance appears as a capacity shortfall.
- **`prioritized` allocation is unsupported** and silently falls back to `lowestPrice`.
- **AWS considers the underlying API legacy** with no planned investment — see the note at the top of this page.

## Migrating to EC2 Fleet

Most Spot Fleet templates convert to [EC2 Fleet](ec2-fleet.md) by changing `provider_api` and adjusting four fields. ORB's template model is shared across both handlers, so `image_id`, `machine_types`, `subnet_ids`, `security_group_ids`, `tags`, and `abis_instance_requirements` carry over unchanged.

| Spot Fleet | EC2 Fleet | Notes |
|---|---|---|
| `provider_api: "SpotFleet"` | `provider_api: "EC2Fleet"` | |
| `fleet_role` (required) | Not used | EC2 Fleet uses the EC2 Spot service-linked role in the account rather than a per-template role. Drop the field. |
| `fleet_type: "request"` / `"maintain"` | Same, plus `"instant"` | Behaviour is equivalent for the two shared types. `instant` is new and returns instance ids synchronously. |
| `allocation_strategy: "capacityOptimized"` | `allocation_strategy: "capacityOptimized"` | Keep the camelCase value; ORB converts it to the hyphenated form EC2 Fleet expects. |
| `max_price` (per-override `SpotPrice`) | `max_price` (`MaxTotalPrice`) | **Semantics change.** A per-instance price cap becomes a total fleet spend cap. Recheck the value. |
| `spot_fleet_request_expiry` | Not supported | EC2 Fleet has no ORB-exposed expiry field. Use a native spec `ValidUntil` if you need one. |
| Per-override `Priority` | Not supported | Only relevant with `capacityOptimizedPrioritized`. Keep Spot Fleet if you depend on it. |

The `max_price` change is the one to look at closely: `0.10` as a per-instance cap is not the same constraint as `0.10` as a total fleet cap, and a total cap set too low will throttle the whole fleet.

Worked example — the Spot Fleet template from [Quick start](#quick-start) as an EC2 Fleet template:

```json
{
  "template_id": "batch-spot",
  "name": "Batch Spot Fleet",
  "description": "Diversified spot capacity for batch work",
  "provider_api": "EC2Fleet",
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "machine_types": {"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7a8b9"],
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "spot",
  "allocation_strategy": "capacityOptimized",
  "max_machines": 100,
  "metadata": {"fleet_type": "request"},
  "tags": {"Environment": "dev"}
}
```

`fleet_role` and `spot_fleet_request_expiry` are gone, and `max_price` is omitted rather than carried over at its old value. Validate the new template before switching traffic to it:

```bash
orb templates validate --file batch-spot-ec2fleet.json
orb machines request batch-spot-ec2fleet 1
```

Existing Spot Fleet requests are unaffected by adding an EC2 Fleet template — the two can run side by side while you migrate.

## Related

- [EC2 Fleet](ec2-fleet.md) — newer fleet API with instant provisioning and richer pricing mixes
- [Auto Scaling Groups](asg.md) — health-checked, self-replacing capacity
- [EC2 Instances (RunInstances)](run-instances.md) — synchronous single-type launches
- [IAM Permissions Guide](../../deployment/iam-permissions.md) — service-linked role setup
