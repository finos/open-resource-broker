# AWS EC2 Instances (RunInstances) Provider

The RunInstances handler provisions individual EC2 instances through the [EC2 RunInstances API](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_RunInstances.html). It is the simplest of the AWS provisioning types: one API call launches every instance in the request, and the call returns all instance IDs synchronously.

Use it when you want predictable, immediate provisioning of a single instance type. Use [EC2 Fleet](ec2-fleet.md) or [Spot Fleet](spot-fleet.md) instead when you need multiple instance types, capacity diversification, or weighted capacity.

## Quick start

```bash
pip install "orb-py[aws]"
orb init
```

### 1. Create a template

```json
{
  "template_id": "web-worker",
  "name": "Web Worker",
  "description": "On-demand workers for the web tier",
  "provider_api": "RunInstances",
  "image_id": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64",
  "machine_types": {"t3.medium": 1},
  "subnet_ids": ["subnet-0f984e48e6e899311"],
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "price_type": "ondemand",
  "max_machines": 100,
  "tags": {"Environment": "dev"}
}
```

### 2. Request instances

```bash
orb machines request web-worker 5
```

### 3. Check status

```bash
orb requests status <request-id>
```

### 4. Return instances

```bash
orb machines return i-0abc1234def567890 i-0def5678abc123456
```

## Template configuration

The handler uses `provider_api: "RunInstances"`. Every request creates (or reuses) a launch template, then calls `run_instances` with `MinCount: 1` and `MaxCount: <requested count>`.

### Top-level fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `provider_api` | Yes | `string` | Must be `"RunInstances"` |
| `image_id` | Yes | `string` | AMI id (`ami-*`) or an SSM parameter path (`/aws/service/...`). SSM paths are resolved at request time and need `ssm:GetParameter`. |
| `machine_types` | No | `object` | Instance-type map. **Only the first key is used** — see [Single instance type](#single-instance-type). |
| `subnet_ids` | No | `list[string]` | Target subnets. Only one subnet is used per request; see [Networking](#networking). |
| `security_group_ids` | No | `list[string]` | Security groups to attach. |
| `price_type` | No | `string` | `"ondemand"` (default) or `"spot"`. |
| `max_price` | No | `float` | Maximum spot price per hour. Only applied when `price_type` is `"spot"`. |
| `max_machines` | No | `integer` | Maximum instances this template may provision (default: 1). |
| `machine_ssh_key` | No | `string` | EC2 key pair name. |
| `machine_bootstrap` | No | `string` | User data script. |
| `machine_role` | No | `string` | IAM instance profile name. |
| `machine_disk_size_gb` | No | `integer` | Root volume size in GB. |
| `machine_disk_type` | No | `string` | Root volume type (default `gp3`). |
| `iops` | No | `integer` | Provisioned IOPS for `io1`/`io2`/`gp3`. |
| `launch_template_id` | No | `string` | Pin an existing launch template instead of letting ORB create one. |
| `tags` | No | `object` | Tags applied to instances alongside ORB's own `orb:` tags. |
| `provider_api_spec` | No | `object` | Native `RunInstances` parameters, merged over the generated call. See [Native spec](#native-spec). |

`max_machines` accepts the older name `max_instances`, and `image_id`, `vm_type`, `key_name`, `user_data`, `volume_type`, and `instance_profile` still deserialize as aliases for their `machine_*` equivalents. The alias path logs a deprecation warning.

### Single instance type

RunInstances launches one instance type per call. When `machine_types` holds several entries, the handler takes the **first key** and ignores the rest along with their weights:

```json
{
  "machine_types": {"t3.medium": 1, "t3.large": 2}
}
```

This provisions `t3.medium` only. Weighted capacity has no meaning here — each instance counts as exactly one unit. If you want ORB to spread a request across instance types, use [EC2 Fleet](ec2-fleet.md), [Spot Fleet](spot-fleet.md), or [ASG](asg.md).

Attribute-based instance selection (`abis_instance_requirements`) is likewise not supported by this handler, because `RunInstances` has no `InstanceRequirements` parameter. See [Attribute-Based Instance Selection](../../user_guide/abis.md) for the handlers that do support it.

### Networking

Networking normally lives in the launch template ORB creates from `subnet_ids` and `security_group_ids`. When you pin an existing `launch_template_id` **and** also set `subnet_ids` or `security_group_ids`, ORB inspects the launch template first to avoid an `InvalidParameterCombination` from AWS:

| Launch template state | Behaviour |
|---|---|
| Already defines `NetworkInterfaces`, `SubnetId`, or `SecurityGroupIds` | Request fails with a validation error telling you to remove the conflict from one side or the other. |
| Defines no networking | ORB injects `SubnetId` (single subnet only) and `SecurityGroupIds` at the API level. |
| Cannot be described (IAM denial) | ORB logs a warning and assumes the launch template owns networking, injecting nothing. |

Because the API-level injection sets `SubnetId` (singular), only one subnet is applied. Supply a single-element `subnet_ids` list when you pin a launch template, or let the launch template own networking entirely.

### Spot instances

Setting `price_type: "spot"` adds `InstanceMarketOptions: {"MarketType": "spot"}` to the call, and `max_price` becomes `SpotOptions.MaxPrice`:

```json
{
  "template_id": "batch-spot",
  "provider_api": "RunInstances",
  "machine_types": {"t3.medium": 1},
  "price_type": "spot",
  "max_price": 0.10
}
```

`allocation_strategy` is accepted but has little effect: RunInstances has no allocation-strategy parameter, so every strategy maps to the `one-time` spot request type. Use a fleet handler if you need real allocation strategies.

Note that the shipped handler defaults in `aws_defaults.json` declare `RunInstances` with `supports_spot: false`. Spot works when configured on a template as above, but on-demand is the intended path for this handler.

### Native spec

When a `provider_api_spec` (or `provider_api_spec_file`) is present, ORB renders it and merges it over the generated parameters. `LaunchTemplate.LaunchTemplateId` and `LaunchTemplate.Version` are always overwritten with the launch template ORB resolved for the request, and `MinCount`/`MaxCount` are filled in when the spec omits them. See the [Native Spec Reference](../../api/native-spec-reference.md) for available template variables.

## Machine data

Instances are reported in ORB's standard machine shape:

```json
{
  "instance_id": "i-0abc1234def567890",
  "resource_id": "r-0123456789abcdef0",
  "status": "running",
  "private_ip": "10.0.1.42",
  "public_ip": null,
  "launch_time": "2026-07-13T10:00:00+00:00",
  "instance_type": "t3.medium",
  "image_id": "ami-0abcdef1234567890",
  "subnet_id": "subnet-0f984e48e6e899311",
  "security_group_ids": ["sg-0528dfedb6d763b16"],
  "vpc_id": "vpc-0123456789abcdef0",
  "provider_api": "RunInstances",
  "provider_data": {
    "cloud_host_id": "i-0abc1234def567890",
    "vcpus": 2,
    "availability_zone": "us-east-1a",
    "region": "us-east-1"
  }
}
```

`resource_id` is the EC2 **reservation id** returned by `run_instances`. It is the handle ORB uses to rediscover instances if the stored instance-id list is ever lost.

## Fulfilment semantics

RunInstances is synchronous — one instance equals one capacity unit — so the create call is the final answer and ORB sets `requires_async_polling: false`.

| Condition | State |
|---|---|
| `running >= requested` and no failures | `fulfilled` |
| Some instances still `pending`/`starting` | `in_progress` |
| Every instance failed | `failed` |
| Some running, none pending, short of target | `partial` (final — the shortfall will never be filled) |
| No instance ids yet | `in_progress` |

The `partial` verdict is marked final because a synchronous API will not produce more capacity later. Contrast this with [maintain-type fleets](ec2-fleet.md#fleet-types), which keep reconciling toward the target.

## Release behaviour

`orb machines return` terminates the named instances directly with `terminate_instances`. There is no fleet-level capacity to adjust and no group record to delete, so a partial return simply terminates that subset and leaves the rest running.

Cancelling a request before instances are assigned looks up the reservation id, terminates whatever it finds, and treats "no instances" as a successful no-op. In both paths, once the request's capacity reaches zero, the launch template ORB created for it is cleaned up (subject to the `cleanup` provider config).

## IAM permissions

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances"
      ],
      "Resource": "*"
    }
  ]
}
```

Launch template management (`ec2:CreateLaunchTemplate`, `ec2:CreateLaunchTemplateVersion`, `ec2:DescribeLaunchTemplates`, `ec2:DescribeLaunchTemplateVersions`, `ec2:DeleteLaunchTemplate`), tagging (`ec2:CreateTags`), and SSM AMI resolution (`ssm:GetParameter`) are shared across all AWS handlers. See the [IAM Permissions Guide](../../deployment/iam-permissions.md) for which of those are optional and how failures are handled.

## Limitations

- **One instance type per template** — extra `machine_types` entries are ignored.
- **No weighted capacity** — every instance is one unit.
- **No ABIS** — `RunInstances` has no `InstanceRequirements` parameter.
- **No allocation strategies** — all strategies collapse to a `one-time` spot request.
- **One subnet when injecting networking** — only relevant when pinning a launch template that owns no networking.
- **No capacity reconciliation** — a short launch stays short; nothing replaces failed or interrupted instances.

## Related

- [EC2 Fleet](ec2-fleet.md) — multiple instance types, mixed pricing, three fulfilment modes
- [Spot Fleet](spot-fleet.md) — spot-focused diversification
- [Auto Scaling Groups](asg.md) — health-checked, self-replacing capacity
- [Template Management](../../user_guide/templates.md) — full template field reference
