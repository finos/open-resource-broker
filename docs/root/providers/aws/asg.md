# AWS Auto Scaling Group Provider

The ASG handler provisions capacity through an [Auto Scaling Group](https://docs.aws.amazon.com/autoscaling/ec2/userguide/auto-scaling-groups.html). Each ORB request creates a dedicated ASG, sized to the requested count, which AWS then keeps at that size — replacing instances that fail EC2 health checks.

Use it when you want AWS to maintain capacity for you across availability zones. Use [EC2 Fleet](ec2-fleet.md) or [Spot Fleet](spot-fleet.md) when you want fleet-style provisioning without a long-lived scaling group, or [RunInstances](run-instances.md) for one-shot launches.

## Quick start

```bash
pip install "orb-py[aws]"
orb init
```

### 1. Create a template

```json
{
  "template_id": "service-tier",
  "name": "Service Tier ASG",
  "description": "Long-running workers with health-checked replacement",
  "provider_api": "ASG",
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "machine_types": {"t3.medium": 2, "t3.xlarge": 4},
  "subnet_ids": ["subnet-0f984e48e6e899311", "subnet-1a2b3c4d5e6f7a8b9"],
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "ondemand",
  "max_machines": 100,
  "tags": {"Environment": "prod"}
}
```

### 2. Request capacity

```bash
orb machines request service-tier 6
```

With the weights above, six capacity units is satisfied by three `t3.medium` (2 each), or one `t3.medium` plus one `t3.xlarge`, or any other combination summing to 6 — see [Weighted capacity](#weighted-capacity).

### 3. Check status

```bash
orb requests status <request-id>
```

### 4. Return capacity

```bash
orb machines return i-0abc1234def567890 i-0def5678abc123456
```

## Template configuration

The handler uses `provider_api: "ASG"`. Each request creates a launch template, then calls `create_auto_scaling_group` with a name of the form `<asg prefix><request-id>`, where the prefix comes from `resource.prefixes.asg` and is empty unless you configure one.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `provider_api` | Yes | `string` | Must be `"ASG"` |
| `image_id` | Yes | `string` | AMI id or SSM parameter path. |
| `machine_types` | No | `object` | Instance type → weighted capacity. Drives the `MixedInstancesPolicy` overrides. |
| `subnet_ids` | No | `list[string]` | Becomes `VPCZoneIdentifier` (comma-joined). Supply subnets in several AZs for multi-AZ placement. |
| `security_group_ids` | No | `list[string]` | Security groups, applied via the launch template. |
| `price_type` | No | `string` | `"ondemand"` (default), `"spot"`, or `"heterogeneous"`. |
| `percent_on_demand` | No | `integer` | 0–100. On-demand share of capacity above the base. Forces a `MixedInstancesPolicy`. |
| `allocation_strategy` | No | `string` | Spot allocation strategy. Converted to the ASG hyphenated form. |
| `max_price` | No | `float` | Maximum spot price per hour. |
| `abis_instance_requirements` | No | `object` | Attribute-based instance selection. Takes precedence over `machine_types`. |
| `max_machines` | No | `integer` | Maximum capacity this template may provision. |
| `launch_template_id` | No | `string` | Pin an existing launch template. |
| `context` | No | `string` | Passed through as the ASG `Context` field. |
| `tags` | No | `object` | Tags applied to the ASG with `PropagateAtLaunch: true`. |
| `provider_api_spec` | No | `object` | Native `CreateAutoScalingGroup` parameters. See [Native spec](#native-spec). |

### Group sizing

ORB derives the group's bounds from the requested count rather than exposing them as template fields:

| ASG field | Value |
|---|---|
| `MinSize` | `0` |
| `MaxSize` | `requested_count * 2` |
| `DesiredCapacity` | `requested_count` |
| `DefaultCooldown` | `300` |
| `HealthCheckType` | `EC2` |
| `HealthCheckGracePeriod` | `300` |
| `NewInstancesProtectedFromScaleIn` | `true` |

`MinSize: 0` lets ORB scale the group to empty on return instead of fighting a floor. `MaxSize` is set to twice the request so AWS has headroom to replace unhealthy instances. Scale-in protection is on so AWS does not reclaim instances ORB has handed out to a scheduler.

Override any of these with a [native spec](#native-spec) if your workload needs different bounds, a different health check type, or ELB health checks.

### Weighted capacity

Values in `machine_types` become `WeightedCapacity` on each `MixedInstancesPolicy` override. `DesiredCapacity` is then measured in those units, not in instances:

```json
{
  "machine_types": {"t3.medium": 2, "t3.xlarge": 4}
}
```

A request for 8 units can be satisfied by four `t3.medium`, two `t3.xlarge`, or one of each plus one more `t3.medium`. AWS picks the mix. This is why the fulfilment check sums weights rather than counting instances — see [Fulfilment semantics](#fulfilment-semantics).

Weights of `0` or omitted values leave `WeightedCapacity` unset, which AWS treats as 1.

### Pricing and mixed instances

The shape of the generated config depends on pricing:

| Configuration | Generated config |
|---|---|
| `price_type: "ondemand"`, no `percent_on_demand` | `MixedInstancesPolicy` with instance-type overrides (or a plain `LaunchTemplate` when neither `machine_types` nor ABIS is set). No `InstancesDistribution`. |
| `price_type: "spot"` | `InstancesDistribution` with `OnDemandPercentageAboveBaseCapacity: 0` — all capacity above the base is spot. |
| `price_type: "heterogeneous"` or any `percent_on_demand` | `InstancesDistribution` with `OnDemandPercentageAboveBaseCapacity: <percent_on_demand>`. |

`OnDemandBaseCapacity` is always `0`, so the percentage governs the whole group. The `InstancesDistribution` carries `allocation_strategy` as `SpotAllocationStrategy` and `allocation_strategy_on_demand` as `OnDemandAllocationStrategy`.

Example 30% on-demand, 70% spot:

```json
{
  "template_id": "mixed-tier",
  "provider_api": "ASG",
  "machine_types": {"t3.medium": 2, "t3.large": 2, "t3.xlarge": 4},
  "price_type": "heterogeneous",
  "percent_on_demand": 30,
  "allocation_strategy": "lowestPrice"
}
```

### Allocation strategies

ASG uses the hyphenated wire format. ORB accepts camelCase, hyphenated, or snake_case in templates and converts on the way out:

| Template value | Sent to AWS |
|---|---|
| `lowestPrice` | `lowest-price` |
| `capacityOptimized` | `capacity-optimized` |
| `capacityOptimizedPrioritized` | `capacity-optimized-prioritized` |
| `priceCapacityOptimized` | `price-capacity-optimized` |
| `diversified` | `diversified` |
| `prioritized` (on-demand pools only) | `prioritized` |

`allocation_strategy` is the strategy for spot pools and `allocation_strategy_on_demand` the strategy for on-demand pools. Their defaults come from the AWS provider defaults (`provider_defaults.aws.template_defaults`): `priceCapacityOptimized` for spot pools and `lowestPrice` for on-demand pools, whatever the template's `price_type`. A provider instance's `template_defaults` and the template itself override them; there is no per-request override.

`prioritized` applies only to on-demand pools. A template that sets an invalid or unknown strategy is rejected and reported by `templates show` and `templates validate`. A template that sets `prioritized`, or `lowestPrice` with `price_type: ondemand`, in `allocation_strategy` is read as its on-demand strategy, with a deprecation warning; move the value to `allocation_strategy_on_demand`.

### Attribute-based instance selection

Setting `abis_instance_requirements` replaces the instance-type overrides with a single `InstanceRequirements` override, letting AWS choose any matching type:

```json
{
  "provider_api": "ASG",
  "abis_instance_requirements": {
    "vcpu_count": {"min": 2, "max": 8},
    "memory_mib": {"min": 4096, "max": 32768},
    "cpu_manufacturers": ["intel", "amd"]
  }
}
```

When ABIS is present, `machine_types` is ignored. The requirements are also injected into a native spec that omits them. See [Attribute-Based Instance Selection](../../user_guide/abis.md).

### Native spec

A `provider_api_spec` is rendered and merged over the generated config. ORB always enforces `AutoScalingGroupName`, defaults `NewInstancesProtectedFromScaleIn` to `true`, and patches the launch template id and version into either `MixedInstancesPolicy.LaunchTemplate.LaunchTemplateSpecification` or the top-level `LaunchTemplate`, whichever the spec uses. See the [Native Spec Reference](../../api/native-spec-reference.md).

## Machine data

Instances carry the ASG name as `resource_id`:

```json
{
  "instance_id": "i-0abc1234def567890",
  "resource_id": "req-abc123def456",
  "status": "running",
  "private_ip": "10.0.1.42",
  "instance_type": "t3.medium",
  "provider_api": "ASG",
  "provider_data": {
    "cloud_host_id": "i-0abc1234def567890",
    "vcpus": 2,
    "availability_zone": "us-east-1a",
    "region": "us-east-1"
  }
}
```

Instances in `Terminating`, `Terminated`, `Detaching`, and `Detached` lifecycle states are excluded from the reported set.

## Fulfilment semantics

The check is capacity-based, not instance-based: ORB sums `WeightedCapacity` across `InService` instances and compares that to `DesiredCapacity`.

| Condition | State |
|---|---|
| In-service weighted capacity `>= DesiredCapacity`, nothing pending, nothing failed | `fulfilled` |
| Instances in a failure state, none in service, none pending | `failed` |
| Anything else | `in_progress` |

Instances in `Pending`, `Pending:Wait`, and `Pending:Proceed` count as pending. Unweighted groups contribute 1 unit per in-service instance, so the same rule covers both cases.

Because ASG keeps reconciling toward `DesiredCapacity`, this handler has no terminal `partial` state — a shortfall stays `in_progress` while AWS keeps trying. `requires_async_polling` is `true`: the create call returns only the group name, and instances arrive later.

When one request spans several ASGs, all groups must be fulfilled for the request to be fulfilled; any failure makes it failed, any partial makes it partial, and otherwise it stays in progress.

## Release behaviour

Returning instances from an ASG is more involved than terminating them, because AWS would otherwise replace them immediately.

1. **Group the instances.** ORB maps each instance to its ASG, from the request's stored resource mapping when available, otherwise via `describe_auto_scaling_instances`. Instances that belong to no ASG are terminated directly.
2. **Filter to current members.** Only instances in `InService` or `Standby` are processed. This is an idempotency guard: an instance already detached or terminated had its capacity decremented on the first call, and counting it again would double-decrement.
3. **Terminate weight-aware.** Each instance is terminated with `terminate_instance_in_auto_scaling_group` and `ShouldDecrementDesiredCapacity: true`. This decrements `DesiredCapacity` by the instance's `WeightedCapacity` rather than by 1, which is symmetric with how weighted groups scale up. `detach_instances` would decrement by count and drift the group's capacity.
4. **Clamp `MinSize`.** The group is re-described to read live `DesiredCapacity`, and `MinSize` is lowered if it now exceeds it.
5. **Delete when empty.** `DesiredCapacity` of zero means the group is empty, so ORB deletes the ASG (`ForceDelete: true`) and cleans up the launch template.

## Cancelling a request

Cancelling a request that is still being fulfilled scales the group to `MinSize: 0` and `DesiredCapacity: 0`, which narrows the window in which an in-flight scale-out launches instances, and then force-deletes it. The scale-down is best effort because the force delete follows. A group that is absent or already `Delete in progress` counts as stopped and is not touched again. A group whose lifecycle hooks hold instances in `Terminating:Wait` stays in `Delete in progress` until the hook heartbeat times out, and those instances can keep billing until then. Instances still attached afterwards are found with the `aws:autoscaling:groupName` tag and returned with the recorded machines.

A later return of instances that belonged to a group that is being deleted or is gone terminates them directly and does not detach them from the group.

A separate pre-termination path (`reduce_capacity`) lowers `DesiredCapacity` and `MinSize` before instances are terminated by other means. Every failure in that path is warning-only, so a capacity-reduction hiccup never blocks a return.

## IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "autoscaling:CreateAutoScalingGroup",
        "autoscaling:UpdateAutoScalingGroup",
        "autoscaling:DeleteAutoScalingGroup",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:DescribeAutoScalingInstances",
        "autoscaling:CreateOrUpdateTags",
        "autoscaling:SetDesiredCapacity",
        "autoscaling:TerminateInstanceInAutoScalingGroup",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

ASG tagging uses `autoscaling:CreateOrUpdateTags` rather than `ec2:CreateTags`. Tag failures are non-fatal: the group is created and the request proceeds without `orb:` tags. Launch template and SSM permissions are shared with the other handlers — see the [IAM Permissions Guide](../../deployment/iam-permissions.md).

## Limitations

- **Group bounds are derived, not configured** — `MinSize`, `MaxSize`, cooldown, and health check settings come from the requested count unless you supply a native spec.
- **EC2 health checks only** by default; ELB health checks need a native spec.
- **No scaling policies or lifecycle hooks** are created. ORB reads `instance_protection` and `lifecycle_hooks` attributes if a template carries them, but they are not part of the template schema.
- **No terminal partial state** — an under-filled group reports `in_progress` while AWS keeps reconciling, so a request for capacity that cannot be satisfied will not settle on its own.
- **One ASG per request** — ORB does not reuse an existing group across requests.

## Related

- [EC2 Fleet](ec2-fleet.md) — fleet provisioning with instant, request, and maintain modes
- [Spot Fleet](spot-fleet.md) — spot-focused diversification
- [EC2 Instances (RunInstances)](run-instances.md) — synchronous single-type launches
- [Attribute-Based Instance Selection](../../user_guide/abis.md) — describing requirements instead of instance types
