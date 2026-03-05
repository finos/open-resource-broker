#!/usr/bin/env python3

"""List and optionally terminate all AWS resources managed by ORB.

Filters by tag orb:managed-by=open-resource-broker across all resource types:
  - Auto Scaling Groups (and their instances)
  - EC2 Fleets
  - Spot Fleet Requests
  - Standalone EC2 Instances (RunInstances)
  - Launch Templates

Usage:
  python cleanup_orb_resources.py --profile myprofile --region eu-west-1
  python cleanup_orb_resources.py --profile myprofile --region eu-west-1 --terminate
  python cleanup_orb_resources.py --profile myprofile --region eu-west-1 --terminate --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "eu-west-1"
ORB_TAG_KEY = "orb:managed-by"
ORB_TAG_VALUE = "open-resource-broker"

EC2_FLEET_ACTIVE_STATES: Sequence[str] = ("submitted", "active", "modifying")
SPOT_FLEET_ACTIVE_STATES: Sequence[str] = ("submitted", "active", "modifying")
INSTANCE_ACTIVE_STATES: Sequence[str] = (
    "pending",
    "running",
    "stopping",
    "stopped",
    "shutting-down",
)
SPOT_REQUEST_ACTIVE_STATES: Sequence[str] = ("open", "active")


def _utc_iso(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value) if value is not None else "-"


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _parse_tags(tag_args: list[str]) -> list[tuple[str, str]]:
    """Parse 'key=value' strings into (key, value) tuples."""
    result: list[tuple[str, str]] = []
    for tag in tag_args:
        if "=" not in tag:
            raise ValueError(f"Invalid tag format {tag!r} — expected key=value")
        key, _, value = tag.partition("=")
        result.append((key.strip(), value.strip()))
    return result


def _ec2_tag_filters(tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    return [{"Name": f"tag:{k}", "Values": [v]} for k, v in tags]


def _asg_tag_filters(tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    # ASG API uses tag-key / tag-value, not tag:<key>
    return [
        f
        for k, v in tags
        for f in (
            {"Name": "tag-key", "Values": [k]},
            {"Name": "tag-value", "Values": [v]},
        )
    ]


def _matches_tags(resource_tags: list[dict[str, str]], tags: list[tuple[str, str]]) -> bool:
    tag_map = {t.get("Key", ""): t.get("Value", "") for t in resource_tags}
    return all(tag_map.get(k) == v for k, v in tags)


def _get_tag(tags: list[dict[str, str]], key: str) -> str:
    for t in tags or []:
        if t.get("Key") == key:
            return t.get("Value", "-")
    return "-"


# ── Discovery ────────────────────────────────────────────────────────────────


def _find_asgs(asg_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paginator = asg_client.get_paginator("describe_auto_scaling_groups")
    for page in paginator.paginate(Filters=_asg_tag_filters(tags)):
        results.extend(page.get("AutoScalingGroups", []))
    return results


def _find_ec2_fleets(ec2_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_fleets")
    filters = _ec2_tag_filters(tags) + [
        {"Name": "fleet-state", "Values": list(EC2_FLEET_ACTIVE_STATES)}
    ]
    for page in paginator.paginate(Filters=filters):
        results.extend(page.get("Fleets", []))
    return results


def _find_spot_fleets(ec2_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_spot_fleet_requests")
    for page in paginator.paginate():
        for config in page.get("SpotFleetRequestConfigs", []):
            if config.get("SpotFleetRequestState") not in SPOT_FLEET_ACTIVE_STATES:
                continue
            if _matches_tags(config.get("Tags") or [], tags):
                results.append(config)
    return results


def _find_run_instances(ec2_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Find ORB-managed instances launched via RunInstances (not fleet-owned)."""
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_instances")
    filters = _ec2_tag_filters(tags) + [
        {"Name": "instance-state-name", "Values": list(INSTANCE_ACTIVE_STATES)},
        {"Name": "tag:orb:provider-api", "Values": ["RunInstances"]},
    ]
    for page in paginator.paginate(Filters=filters):
        for reservation in page.get("Reservations", []):
            results.extend(reservation.get("Instances", []))
    return results


def _find_ec2_fleet_instances(ec2_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Find ORB-managed instances launched via EC2Fleet (including instant fleets).

    Instant fleets are invisible to describe_fleets without explicit FleetIds,
    so we discover them via their instances' orb:provider-api=EC2Fleet tag.
    Each instance carries an aws:ec2:fleet-id tag we can use to delete the fleet.
    """
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_instances")
    filters = _ec2_tag_filters(tags) + [
        {"Name": "instance-state-name", "Values": list(INSTANCE_ACTIVE_STATES)},
        {"Name": "tag:orb:provider-api", "Values": ["EC2Fleet"]},
    ]
    for page in paginator.paginate(Filters=filters):
        for reservation in page.get("Reservations", []):
            results.extend(reservation.get("Instances", []))
    return results


def _find_launch_templates(ec2_client, tags: list[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    paginator = ec2_client.get_paginator("describe_launch_templates")
    for page in paginator.paginate(Filters=_ec2_tag_filters(tags)):
        results.extend(page.get("LaunchTemplates", []))
    return results


# ── Printing ─────────────────────────────────────────────────────────────────


def _print_asgs(asgs: list[dict[str, Any]]) -> None:
    print(f"\nAuto Scaling Groups ({len(asgs)}):")
    if not asgs:
        print("  - none")
        return
    for asg in asgs:
        name = asg.get("AutoScalingGroupName", "-")
        desired = asg.get("DesiredCapacity", "-")
        instances = len(asg.get("Instances", []))
        created = _utc_iso(asg.get("CreatedTime"))
        request_id = _get_tag(asg.get("Tags", []), "orb:request-id")
        print(
            f"  - {name} | desired={desired} instances={instances} request={request_id} created={created}"
        )


def _print_ec2_fleets(fleets: list[dict[str, Any]]) -> None:
    print(f"\nEC2 Fleets ({len(fleets)}):")
    if not fleets:
        print("  - none")
        return
    for fleet in fleets:
        fleet_id = fleet.get("FleetId", "-")
        state = fleet.get("FleetState", "-")
        fleet_type = fleet.get("Type", "-")
        target = fleet.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity", "-")
        fulfilled = fleet.get("FulfilledCapacity", "-")
        created = _utc_iso(fleet.get("CreateTime"))
        request_id = _get_tag(fleet.get("Tags", []), "orb:request-id")
        print(
            f"  - {fleet_id} | state={state} type={fleet_type} target={target} fulfilled={fulfilled} request={request_id} created={created}"
        )


def _print_spot_fleets(fleets: list[dict[str, Any]]) -> None:
    print(f"\nSpot Fleet Requests ({len(fleets)}):")
    if not fleets:
        print("  - none")
        return
    for fleet in fleets:
        fleet_id = fleet.get("SpotFleetRequestId", "-")
        state = fleet.get("SpotFleetRequestState", "-")
        config = fleet.get("SpotFleetRequestConfig", {}) or {}
        target = config.get("TargetCapacity", "-")
        created = _utc_iso(fleet.get("CreateTime"))
        request_id = _get_tag(fleet.get("Tags", []), "orb:request-id")
        print(
            f"  - {fleet_id} | state={state} target={target} request={request_id} created={created}"
        )


def _print_instances(instances: list[dict[str, Any]]) -> None:
    print(f"\nRunInstances ({len(instances)}):")
    if not instances:
        print("  - none")
        return
    for inst in instances:
        inst_id = inst.get("InstanceId", "-")
        state = inst.get("State", {}).get("Name", "-")
        itype = inst.get("InstanceType", "-")
        launched = _utc_iso(inst.get("LaunchTime"))
        request_id = _get_tag(inst.get("Tags", []), "orb:request-id")
        print(
            f"  - {inst_id} | state={state} type={itype} request={request_id} launched={launched}"
        )


def _print_ec2_fleet_instances(instances: list[dict[str, Any]]) -> None:
    print(f"\nEC2Fleet Instances / Instant Fleets ({len(instances)}):")
    if not instances:
        print("  - none")
        return
    for inst in instances:
        inst_id = inst.get("InstanceId", "-")
        state = inst.get("State", {}).get("Name", "-")
        itype = inst.get("InstanceType", "-")
        launched = _utc_iso(inst.get("LaunchTime"))
        request_id = _get_tag(inst.get("Tags", []), "orb:request-id")
        fleet_id = _get_tag(inst.get("Tags", []), "aws:ec2:fleet-id")
        print(
            f"  - {inst_id} | state={state} type={itype} fleet={fleet_id} request={request_id} launched={launched}"
        )


def _print_launch_templates(templates: list[dict[str, Any]]) -> None:
    print(f"\nLaunch Templates ({len(templates)}):")
    if not templates:
        print("  - none")
        return
    for lt in templates:
        lt_id = lt.get("LaunchTemplateId", "-")
        name = lt.get("LaunchTemplateName", "-")
        created = _utc_iso(lt.get("CreateTime"))
        request_id = _get_tag(lt.get("Tags", []), "orb:request-id")
        print(f"  - {lt_id} | name={name} request={request_id} created={created}")


# ── Termination ───────────────────────────────────────────────────────────────


def _terminate_asgs(asg_client, asgs: list[dict[str, Any]], dry_run: bool) -> None:
    if not asgs:
        print("No ASGs to delete.")
        return
    for asg in asgs:
        name = asg.get("AutoScalingGroupName", "")
        if dry_run:
            print(f"  [dry-run] Would delete ASG: {name}")
            continue
        try:
            asg_client.delete_auto_scaling_group(
                AutoScalingGroupName=name,
                ForceDelete=True,
            )
            print(f"  - deleted ASG: {name}")
        except ClientError as exc:
            print(f"  - failed to delete ASG {name}: {exc}", file=sys.stderr)


def _terminate_ec2_fleets(ec2_client, fleets: list[dict[str, Any]], dry_run: bool) -> None:
    if not fleets:
        print("No EC2 Fleets to terminate.")
        return
    fleet_ids: list[str] = [f for fleet in fleets if (f := fleet.get("FleetId"))]
    for chunk in _chunked(fleet_ids, 100):
        if dry_run:
            print(f"  [dry-run] Would delete EC2 Fleets: {chunk}")
            continue
        try:
            response = ec2_client.delete_fleets(FleetIds=chunk, TerminateInstances=True)
            for item in response.get("SuccessfulFleetDeletions", []):
                print(f"  - deleted EC2 Fleet: {item.get('FleetId', '-')}")
            for item in response.get("UnsuccessfulFleetDeletions", []):
                print(
                    f"  - failed EC2 Fleet {item.get('FleetId', '-')}: {item.get('ErrorMessage', 'unknown')}",
                    file=sys.stderr,
                )
        except ClientError as exc:
            print(f"  - EC2 Fleet deletion error: {exc}", file=sys.stderr)


def _terminate_spot_fleets(ec2_client, fleets: list[dict[str, Any]], dry_run: bool) -> None:
    if not fleets:
        print("No Spot Fleet Requests to cancel.")
        return
    fleet_ids: list[str] = [f for fleet in fleets if (f := fleet.get("SpotFleetRequestId"))]
    for chunk in _chunked(fleet_ids, 100):
        if dry_run:
            print(f"  [dry-run] Would cancel Spot Fleets: {chunk}")
            continue
        try:
            response = ec2_client.cancel_spot_fleet_requests(
                SpotFleetRequestIds=chunk, TerminateInstances=True
            )
            for item in response.get("SuccessfulFleetRequests", []):
                print(f"  - cancelled Spot Fleet: {item.get('SpotFleetRequestId', '-')}")
            for item in response.get("UnsuccessfulFleetRequests", []):
                print(
                    f"  - failed Spot Fleet {item.get('SpotFleetRequestId', '-')}: {item.get('ErrorMessage', 'unknown')}",
                    file=sys.stderr,
                )
        except ClientError as exc:
            print(f"  - Spot Fleet cancellation error: {exc}", file=sys.stderr)


def _terminate_instances(ec2_client, instances: list[dict[str, Any]], dry_run: bool) -> None:
    if not instances:
        print("No RunInstances instances to terminate.")
        return
    instance_ids: list[str] = [i for inst in instances if (i := inst.get("InstanceId"))]
    for chunk in _chunked(instance_ids, 1000):
        if dry_run:
            print(f"  [dry-run] Would terminate instances: {chunk}")
            continue
        try:
            ec2_client.terminate_instances(InstanceIds=chunk)
            print(f"  - terminating instances: {chunk}")
        except ClientError as exc:
            print(f"  - instance termination error: {exc}", file=sys.stderr)


def _terminate_ec2_fleet_instances(
    ec2_client, instances: list[dict[str, Any]], dry_run: bool
) -> None:
    """Terminate EC2Fleet instances (including instant fleets).

    Attempts delete_fleets first (handles fleets still in active state),
    then terminates instances directly as a safety net for already-deleted fleets.
    """
    if not instances:
        print("No EC2Fleet instances to terminate.")
        return

    # Collect unique fleet IDs from instance tags
    fleet_ids: list[str] = list(
        {
            fid
            for inst in instances
            if (fid := _get_tag(inst.get("Tags", []), "aws:ec2:fleet-id")) != "-"
        }
    )

    if fleet_ids:
        for chunk in _chunked(fleet_ids, 100):
            if dry_run:
                print(f"  [dry-run] Would delete EC2 Fleets (instant): {chunk}")
                continue
            try:
                response = ec2_client.delete_fleets(FleetIds=chunk, TerminateInstances=True)
                for item in response.get("SuccessfulFleetDeletions", []):
                    print(f"  - deleted EC2 Fleet (instant): {item.get('FleetId', '-')}")
                for item in response.get("UnsuccessfulFleetDeletions", []):
                    # Fleet may already be deleted — not an error
                    print(
                        f"  - fleet {item.get('FleetId', '-')} already gone or failed: {item.get('ErrorMessage', 'unknown')}"
                    )
            except ClientError as exc:
                print(f"  - EC2 Fleet deletion error: {exc}", file=sys.stderr)

    # Safety net: terminate instances directly (covers already-deleted fleets)
    instance_ids: list[str] = [i for inst in instances if (i := inst.get("InstanceId"))]
    for chunk in _chunked(instance_ids, 1000):
        if dry_run:
            print(f"  [dry-run] Would terminate EC2Fleet instances: {chunk}")
            continue
        try:
            ec2_client.terminate_instances(InstanceIds=chunk)
            print(f"  - terminating EC2Fleet instances: {chunk}")
        except ClientError as exc:
            print(f"  - EC2Fleet instance termination error: {exc}", file=sys.stderr)


def _delete_launch_templates(ec2_client, templates: list[dict[str, Any]], dry_run: bool) -> None:
    if not templates:
        print("No launch templates to delete.")
        return
    for lt in templates:
        lt_id = lt.get("LaunchTemplateId", "")
        name = lt.get("LaunchTemplateName", "-")
        if dry_run:
            print(f"  [dry-run] Would delete launch template: {lt_id} ({name})")
            continue
        try:
            ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
            print(f"  - deleted launch template: {lt_id} ({name})")
        except ClientError as exc:
            print(f"  - failed to delete launch template {lt_id}: {exc}", file=sys.stderr)


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"List (and optionally terminate) all ORB-managed AWS resources "
            f"tagged {ORB_TAG_KEY}={ORB_TAG_VALUE}. "
            f"Covers ASGs, EC2 Fleets, Spot Fleets, RunInstances, and Launch Templates."
        )
    )
    parser.add_argument(
        "--region", default=DEFAULT_REGION, help=f"AWS region (default: {DEFAULT_REGION})"
    )
    parser.add_argument("--profile", default=None, help="AWS profile name")
    parser.add_argument(
        "--tag",
        metavar="KEY=VALUE",
        action="append",
        dest="tags",
        default=[],
        help=(
            "Tag filter as key=value. Repeatable: --tag k1=v1 --tag k2=v2. "
            f"Defaults to {ORB_TAG_KEY}={ORB_TAG_VALUE} when omitted."
        ),
    )
    parser.add_argument(
        "--terminate", action="store_true", help="Terminate all discovered resources"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be terminated without doing it"
    )
    args = parser.parse_args()

    if args.dry_run and not args.terminate:
        parser.error("--dry-run requires --terminate")

    try:
        tags = _parse_tags(args.tags) if args.tags else [(ORB_TAG_KEY, ORB_TAG_VALUE)]
    except ValueError as exc:
        parser.error(str(exc))
        return 1

    session = boto3.Session(profile_name=args.profile) if args.profile else boto3.Session()
    ec2_client = session.client("ec2", region_name=args.region)
    asg_client = session.client("autoscaling", region_name=args.region)

    print(f"Region: {args.region}")
    print(f"Filters: {', '.join(f'{k}={v}' for k, v in tags)}")

    try:
        asgs = _find_asgs(asg_client, tags)
        ec2_fleets = _find_ec2_fleets(ec2_client, tags)
        spot_fleets = _find_spot_fleets(ec2_client, tags)
        fleet_instances = _find_ec2_fleet_instances(ec2_client, tags)
        run_instances = _find_run_instances(ec2_client, tags)
        launch_templates = _find_launch_templates(ec2_client, tags)
    except ClientError as exc:
        print(f"AWS error during discovery: {exc}", file=sys.stderr)
        return 1

    _print_asgs(asgs)
    _print_ec2_fleets(ec2_fleets)
    _print_spot_fleets(spot_fleets)
    _print_ec2_fleet_instances(fleet_instances)
    _print_instances(run_instances)
    _print_launch_templates(launch_templates)

    total = (
        len(asgs)
        + len(ec2_fleets)
        + len(spot_fleets)
        + len(fleet_instances)
        + len(run_instances)
        + len(launch_templates)
    )
    print(f"\nTotal ORB resources found: {total}")

    if not args.terminate:
        print("\nRun with --terminate to delete all of the above.")
        return 0

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Terminating resources...")

    try:
        # Order matters: delete ASGs and fleets first (they own instances),
        # then fleet instances (instant fleets), then RunInstances, then launch templates last.
        _terminate_asgs(asg_client, asgs, args.dry_run)
        _terminate_ec2_fleets(ec2_client, ec2_fleets, args.dry_run)
        _terminate_spot_fleets(ec2_client, spot_fleets, args.dry_run)
        _terminate_ec2_fleet_instances(ec2_client, fleet_instances, args.dry_run)
        _terminate_instances(ec2_client, run_instances, args.dry_run)
        _delete_launch_templates(ec2_client, launch_templates, args.dry_run)
    except ClientError as exc:
        print(f"AWS error during termination: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
