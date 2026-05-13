# IAM Permissions Guide

This guide covers the AWS IAM permissions required to run Open Resource Broker (ORB). Permissions are grouped by handler type and feature area so you can build a least-privilege policy for your deployment.

## Required permissions by handler

### EC2Fleet

Used when `provider_api` is `EC2Fleet`.

```
ec2:CreateFleet
ec2:DeleteFleets
ec2:DescribeFleets
ec2:DescribeFleetInstances
ec2:DescribeInstances
ec2:TerminateInstances
```

### SpotFleet

Used when `provider_api` is `SpotFleet`.

```
ec2:RequestSpotFleet
ec2:CancelSpotFleetRequests
ec2:DescribeSpotFleetRequests
ec2:DescribeSpotFleetInstances
ec2:DescribeInstances
ec2:TerminateInstances
sts:GetCallerIdentity
```

`sts:GetCallerIdentity` is needed to resolve the `AWSServiceRoleForEC2SpotFleet` ARN when a short role name or cross-service role alias is configured.

### ASG (Auto Scaling Group)

Used when `provider_api` is `ASG`.

```
autoscaling:CreateAutoScalingGroup
autoscaling:UpdateAutoScalingGroup
autoscaling:DeleteAutoScalingGroup
autoscaling:DescribeAutoScalingGroups
autoscaling:DescribeAutoScalingInstances
autoscaling:CreateOrUpdateTags
autoscaling:SetDesiredCapacity
ec2:DescribeInstances
ec2:TerminateInstances
```

### RunInstances

Used when `provider_api` is `RunInstances`.

```
ec2:RunInstances
ec2:DescribeInstances
ec2:TerminateInstances
```

## Launch template management

All handler types create and manage EC2 launch templates. These permissions are always required.

```
ec2:CreateLaunchTemplate
ec2:CreateLaunchTemplateVersion
ec2:DescribeLaunchTemplates
ec2:DescribeLaunchTemplateVersions
ec2:DeleteLaunchTemplate
```

`ec2:DeleteLaunchTemplate` is only exercised when cleanup is enabled (`cleanup.enabled: true` and `cleanup.delete_launch_template: true` in provider config). It can be omitted if you disable launch template cleanup.

## Tagging (optional)

ORB tags all resources it creates with `orb:` prefixed keys (`orb:managed-by`, `orb:request-id`, `orb:template-id`, `orb:provider-api`, `orb:created-at`). The tagging call uses:

```
ec2:CreateTags
```

For ASG resources, tagging uses the Auto Scaling API instead:

```
autoscaling:CreateOrUpdateTags
```

### What happens when tagging permissions are absent

Tagging is non-fatal by default. The `on_tag_failure` setting in the provider `tagging` config block controls the behaviour:

- `warn` (default) — ORB logs a warning and the provisioning request continues normally. Resources are created but will not carry `orb:` tags.
- `fail` — ORB raises an error and the request fails.

Because the default is `warn`, missing `ec2:CreateTags` permission will not prevent instance provisioning. You will see log lines like:

```
WARNING Failed to tag resources [...]: An error occurred (UnauthorizedOperation) ...
```

To change the behaviour:

```json
{
  "provider": {
    "providers": [{
      "name": "aws",
      "config": {
        "tagging": {
          "on_tag_failure": "fail"
        }
      }
    }]
  }
}
```

## Launch template describe / version-create

When a template pins an existing `launch_template_id`, ORB attempts to describe it and, if override fields are also set, attempts to create a new launch template version.

`Describe` is best-effort: IAM denials (`UnauthorizedOperation`, `AccessDenied`, `AccessDeniedException`) and `InvalidLaunchTemplateId.NotFound` warn-and-pass-through the operator-supplied id/version as-is. Any other error propagates.

`CreateLaunchTemplateVersion` failure handling is controlled by `launch_template.on_update_failure`:

- `warn_on_iam_denial` (default) — fall back to the existing LT only on IAM-denial codes from `CreateLaunchTemplateVersion`; any other error propagates. Lets a minimal-permission role omit `ec2:CreateLaunchTemplateVersion` while still surfacing genuine API failures.
- `warn` — log a warning and fall back to the existing LT on any `CreateLaunchTemplateVersion` ClientError. ORB-internal errors and AWS errors from other operations (e.g. tagging) propagate.
- `fail` — propagate any failure; request fails.

This makes `ec2:DescribeLaunchTemplates*` and `ec2:CreateLaunchTemplateVersion` optional in the runtime role under the default mode.

## AMI resolution via SSM (conditional)

When a template's `image_id` is an SSM parameter path (e.g. `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64`) rather than an `ami-*` id, ORB resolves it via SSM at request time:

```
ssm:GetParameters
```

This is required when:

- A template inherits the default `image_id` from `aws_defaults.json` (the AL2023 SSM parameter).
- A template explicitly sets `image_id` to any `/aws/service/...` path.

It is **not** required when every template either pins a literal `ami-*` id, or pins a `launch_template_id` whose stored data already carries a resolved AMI.

Without `ssm:GetParameters`, the AMI resolution step fails before any EC2 call, surfacing as an opaque resolution error rather than as a missing-IAM error. Either grant the permission or override `image_id` per template.

## Failure behaviour

| Flag | Config key | Values | Default | Effect |
|---|---|---|---|---|
| `ORB_AWS_TAGGING__ON_TAG_FAILURE` | `tagging.on_tag_failure` | `warn`, `fail` | `warn` | `warn` — logs a warning and provisioning continues; resources are created without `orb:` tags. `fail` — request fails if tagging fails. |
| `ORB_AWS_LAUNCH_TEMPLATE__ON_UPDATE_FAILURE` | `launch_template.on_update_failure` | `fail`, `warn`, `warn_on_iam_denial` | `warn_on_iam_denial` | `warn_on_iam_denial` — fall back to existing LT only on IAM denials from `CreateLaunchTemplateVersion`; other errors propagate. `warn` — fall back on any `CreateLaunchTemplateVersion` ClientError; tagging and non-AWS errors propagate. `fail` — propagate any failure. |

## Example least-privilege IAM policy

The policy below covers all four handler types with tagging and launch template management. Remove the blocks for handler types you do not use.

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2FleetPermissions",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFleet",
        "ec2:DeleteFleets",
        "ec2:DescribeFleets",
        "ec2:DescribeFleetInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SpotFleetPermissions",
      "Effect": "Allow",
      "Action": [
        "ec2:RequestSpotFleet",
        "ec2:CancelSpotFleetRequests",
        "ec2:DescribeSpotFleetRequests",
        "ec2:DescribeSpotFleetInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "ASGPermissions",
      "Effect": "Allow",
      "Action": [
        "autoscaling:CreateAutoScalingGroup",
        "autoscaling:UpdateAutoScalingGroup",
        "autoscaling:DeleteAutoScalingGroup",
        "autoscaling:DescribeAutoScalingGroups",
        "autoscaling:DescribeAutoScalingInstances",
        "autoscaling:CreateOrUpdateTags",
        "autoscaling:SetDesiredCapacity"
      ],
      "Resource": "*"
    },
    {
      "Sid": "RunInstancesPermissions",
      "Effect": "Allow",
      "Action": [
        "ec2:RunInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "InstanceManagement",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:TerminateInstances"
      ],
      "Resource": "*"
    },
    {
      "Sid": "LaunchTemplateManagement",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateLaunchTemplate",
        "ec2:CreateLaunchTemplateVersion",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:DeleteLaunchTemplate"
      ],
      "Resource": "*"
    },
    {
      "Sid": "Tagging",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "STSForSpotFleetRoleResolution",
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity"
      ],
      "Resource": "*"
    },
    {
      "Sid": "SSMForAMIResolution",
      "Effect": "Allow",
      "Action": [
        "ssm:GetParameters"
      ],
      "Resource": "*"
    }
  ]
}
```

`SSMForAMIResolution` can be omitted if no template ever uses an SSM-style `image_id` (see [AMI resolution via SSM](#ami-resolution-via-ssm-conditional)).

### Minimal policy (EC2Fleet only, tagging optional)

If you only use EC2Fleet and are comfortable with the default `on_tag_failure: warn` behaviour, you can omit `ec2:CreateTags` and the policy reduces to:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ORBMinimal",
      "Effect": "Allow",
      "Action": [
        "ec2:CreateFleet",
        "ec2:DeleteFleets",
        "ec2:DescribeFleets",
        "ec2:DescribeFleetInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:CreateLaunchTemplate",
        "ec2:CreateLaunchTemplateVersion",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:DeleteLaunchTemplate"
      ],
      "Resource": "*"
    }
  ]
}
```

Add `ssm:GetParameters` if any template uses an SSM-style `image_id` (including the default `image_id` shipped in `aws_defaults.json`).

## IAM role for SpotFleet

SpotFleet requires a service-linked role so the EC2 Spot Fleet service can launch and terminate instances on your behalf. If the role does not already exist in your account, create it once:

```bash
aws iam create-service-linked-role --aws-service-name spotfleet.amazonaws.com
```

The role ARN takes the form:

```
arn:aws:iam::<account-id>:role/aws-service-role/spotfleet.amazonaws.com/AWSServiceRoleForEC2SpotFleet
```

ORB resolves short role aliases (e.g. `AWSServiceRoleForEC2SpotFleet`) to the full ARN automatically using `sts:GetCallerIdentity`.

## PassRole

If ORB assumes an IAM role to interact with AWS (via `ORB_AWS_ROLE_ARN`), the calling principal needs `iam:PassRole` on that role:

```json
{
  "Effect": "Allow",
  "Action": "iam:PassRole",
  "Resource": "arn:aws:iam::<account-id>:role/<orb-role-name>"
}
```
