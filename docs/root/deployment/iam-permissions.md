# IAM Permissions Guide

This guide covers the AWS IAM permissions required to run Open Resource Broker (ORB). Permissions are grouped by handler type and feature area so you can build a least-privilege policy for your deployment.

For OCI, see [broker instance-principal permissions](oci-integration.md#broker-instance-principal-setup).

## Required permissions by handler

### EC2Fleet

Used when `provider_api` is `EC2Fleet`.

```
ec2:CreateFleet
ec2:DeleteFleets
ec2:ModifyFleet
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
ec2:ModifySpotFleetRequest
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
autoscaling:TerminateInstanceInAutoScalingGroup
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
ec2:DeleteLaunchTemplateVersions
```

`ec2:DeleteLaunchTemplate` and `ec2:DeleteLaunchTemplateVersions` are only exercised when cleanup is enabled (`cleanup.enabled: true` and `cleanup.delete_launch_template: true` in provider config). They can be omitted if you disable launch template cleanup. Before deleting a template, cleanup also calls `ec2:DescribeInstances` to confirm no live instance still uses it; if that call fails the template is kept.

`ec2:DeleteLaunchTemplateVersions` removes the versions ORB adds to a launch template you provide when a template sets overrides. Once the last instance of the request is returned, ORB deletes only the version it created for that request: it must carry the description ORB wrote for the request and must not be the default version. Versions you created, and the template itself, are never touched. Without the permission the versions stay in place and a warning is logged.

### Cleaning up unused ORB launch templates

Whole-resource cancels that cannot read the resource's instances keep the ORB-created launch template so a template that is still in use is never deleted. `orb providers exec cleanup-orphaned-resources --provider-name <aws-instance>` is an explicit maintenance command that removes such leftovers. It deletes a template only when it carries the `orb:managed-by` tag, is older than `min_age_hours` (default 24, minimum 1 unless `force` is set; pass them as `--param min_age_hours=N`), has no pending, running, stopping or stopped instance, and is not referenced by an Auto Scaling group, EC2 Fleet or Spot Fleet request that is still active. It is also gated by `cleanup.enabled` and `cleanup.delete_launch_template`. Use `--dry-run` to list what would be deleted.

Every template is judged before any is deleted, so a failed lookup ends the command with nothing deleted. A delete that fails part-way is reported with the templates deleted and failed. The sweep covers the account and region of the selected provider instance, one region per provider instance; because `orb:managed-by` is the same for every ORB installation, it also covers unused templates of other installations in that account and region. It needs these read permissions in addition to the ones above:

```
autoscaling:DescribeAutoScalingGroups
ec2:DescribeFleets
ec2:DescribeSpotFleetRequests
```

To limit the delete permissions to what ORB creates, `ec2:DeleteLaunchTemplate` can carry a tag condition, which also bounds the sweep:

```json
{
  "Effect": "Allow",
  "Action": "ec2:DeleteLaunchTemplate",
  "Resource": "*",
  "Condition": {"StringEquals": {"aws:ResourceTag/orb:managed-by": "open-resource-broker"}}
}
```

`ec2:DeleteLaunchTemplateVersions` cannot be limited this way for the versions ORB adds to a template you provide: that template is yours and is not tagged by ORB, so grant it on the templates you use with overrides, or accept the warning logged when it is denied.

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
ssm:GetParameter
```

This is required when:

- A template inherits the default `image_id` from `aws_defaults.json` (the AL2023 SSM parameter).
- A template explicitly sets `image_id` to any `/aws/service/...` path.

It is **not** required when every template either pins a literal `ami-*` id, or pins a `launch_template_id` whose stored data already carries a resolved AMI.

Without `ssm:GetParameter`, the AMI resolution step fails before any EC2 call, surfacing as an opaque resolution error rather than as a missing-IAM error. Either grant the permission or override `image_id` per template.

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
        "ec2:ModifyFleet",
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
        "ec2:ModifySpotFleetRequest",
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
        "autoscaling:TerminateInstanceInAutoScalingGroup",
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
        "ec2:DeleteLaunchTemplate",
        "ec2:DeleteLaunchTemplateVersions"
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
        "ssm:GetParameter"
      ],
      "Resource": "arn:aws:ssm:*::parameter/aws/service/*"
    }
  ]
}
```

`SSMForAMIResolution` can be omitted if no template ever uses an SSM-style `image_id` (see [AMI resolution via SSM](#ami-resolution-via-ssm-conditional)). The resource scopes access to the AWS-published public parameters under `/aws/service/`.

- **Partition:** the ARN uses the `aws` partition. In `aws-cn` and `aws-us-gov` use `arn:aws-cn:ssm:*::parameter/aws/service/*` or `arn:aws-us-gov:ssm:*::parameter/aws/service/*` (or `arn:${AWS::Partition}:...` in CloudFormation).
- **Your own parameters:** ORB reads SSM parameters with decryption enabled. If a template's `image_id` points at a parameter in your own account, widen the resource to that parameter's ARN (`arn:aws:ssm:<region>:<account-id>:parameter/<path>`), and for a `SecureString` parameter also allow `kms:Decrypt` on its key.

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
        "ec2:ModifyFleet",
        "ec2:DescribeFleets",
        "ec2:DescribeFleetInstances",
        "ec2:DescribeInstances",
        "ec2:TerminateInstances",
        "ec2:CreateLaunchTemplate",
        "ec2:CreateLaunchTemplateVersion",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeLaunchTemplateVersions",
        "ec2:DeleteLaunchTemplate",
        "ec2:DeleteLaunchTemplateVersions"
      ],
      "Resource": "*"
    }
  ]
}
```

Add `ssm:GetParameter` if any template uses an SSM-style `image_id` (including the default `image_id` shipped in `aws_defaults.json`).

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
