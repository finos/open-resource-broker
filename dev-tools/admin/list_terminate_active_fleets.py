#!/usr/bin/env python3

"""List active EC2 Fleet and Spot Fleet requests in a region."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = "eu-west-1"

# "Active" here means fleets that may still be provisioning or maintaining.
EC2_FLEET_ACTIVE_STATES: Sequence[str] = ("submitted", "active", "modifying")
SPOT_FLEET_ACTIVE_STATES: Sequence[str] = ("submitted", "active", "modifying")


def _utc_iso(value: Any) -> str:
    """Best-effort datetime formatting."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value) if value is not None else "-"


def _chunked(items: list[str], size: int) -> Iterable[list[str]]:
    """Yield fixed-size chunks."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _iter_ec2_fleets(ec2_client) -> Iterable[dict[str, Any]]:
    """Yield EC2 Fleets in active states."""
    paginator = ec2_client.get_paginator("describe_fleets")
    filters = [{"Name": "fleet-state", "Values": list(EC2_FLEET_ACTIVE_STATES)}]
    for page in paginator.paginate(Filters=filters):
        for fleet in page.get("Fleets", []):
            yield fleet


def _iter_spot_fleets(ec2_client) -> Iterable[dict[str, Any]]:
    """Yield Spot Fleet requests in active states."""
    paginator = ec2_client.get_paginator("describe_spot_fleet_requests")
    for page in paginator.paginate():
        for config in page.get("SpotFleetRequestConfigs", []):
            state = config.get("SpotFleetRequestState")
            if state in SPOT_FLEET_ACTIVE_STATES:
                yield config


def _print_ec2_fleets(fleets: list[dict[str, Any]]) -> None:
    print(f"EC2 Fleets ({len(fleets)}):")
    if not fleets:
        print("  - none")
        return

    for fleet in fleets:
        fleet_id = fleet.get("FleetId", "-")
        state = fleet.get("FleetState", "-")
        activity = fleet.get("ActivityStatus", "-")
        fleet_type = fleet.get("Type", "-")
        target = fleet.get("TargetCapacitySpecification", {}).get("TotalTargetCapacity", "-")
        fulfilled = fleet.get("FulfilledCapacity", "-")
        created = _utc_iso(fleet.get("CreateTime"))
        print(
            "  - "
            f"{fleet_id} | state={state} activity={activity} type={fleet_type} "
            f"target={target} fulfilled={fulfilled} created={created}"
        )


def _print_spot_fleets(fleets: list[dict[str, Any]]) -> None:
    print(f"Spot Fleets ({len(fleets)}):")
    if not fleets:
        print("  - none")
        return

    for fleet in fleets:
        fleet_id = fleet.get("SpotFleetRequestId", "-")
        state = fleet.get("SpotFleetRequestState", "-")
        activity = fleet.get("ActivityStatus", "-")
        config = fleet.get("SpotFleetRequestConfig", {}) or {}
        target = config.get("TargetCapacity", "-")
        created = _utc_iso(fleet.get("CreateTime"))
        print(
            f"  - {fleet_id} | state={state} activity={activity} target={target} created={created}"
        )


def _terminate_ec2_fleets(ec2_client, fleet_ids: list[str]) -> None:
    """Terminate EC2 Fleets and their instances."""
    if not fleet_ids:
        print("No active EC2 Fleets to terminate.")
        return

    print(f"Terminating {len(fleet_ids)} EC2 Fleet(s)...")
    for chunk in _chunked(fleet_ids, 100):
        response = ec2_client.delete_fleets(FleetIds=chunk, TerminateInstances=True)
        successful = response.get("SuccessfulFleetDeletions", []) or []
        unsuccessful = response.get("UnsuccessfulFleetDeletions", []) or []
        if successful:
            ids = [item.get("FleetId", "-") for item in successful]
            print(f"  - deleted EC2 Fleets: {ids}")
        if unsuccessful:
            for item in unsuccessful:
                print(
                    "  - failed to delete EC2 Fleet "
                    f"{item.get('FleetId', '-')}: {item.get('ErrorMessage', 'unknown error')}"
                )


def _terminate_spot_fleets(ec2_client, fleet_ids: list[str]) -> None:
    """Cancel Spot Fleets and terminate their instances."""
    if not fleet_ids:
        print("No active Spot Fleets to terminate.")
        return

    print(f"Cancelling {len(fleet_ids)} Spot Fleet request(s)...")
    for chunk in _chunked(fleet_ids, 100):
        response = ec2_client.cancel_spot_fleet_requests(
            SpotFleetRequestIds=chunk,
            TerminateInstances=True,
        )
        successful = response.get("SuccessfulFleetRequests", []) or []
        unsuccessful = response.get("UnsuccessfulFleetRequests", []) or []
        if successful:
            ids = [item.get("SpotFleetRequestId", "-") for item in successful]
            print(f"  - cancelled Spot Fleets: {ids}")
        if unsuccessful:
            for item in unsuccessful:
                print(
                    "  - failed to cancel Spot Fleet "
                    f"{item.get('SpotFleetRequestId', '-')}: {item.get('ErrorMessage', 'unknown error')}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "List active EC2 Fleet and Spot Fleet requests in the account/region "
            f"(default region: {DEFAULT_REGION})."
        )
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help="AWS region")
    parser.add_argument(
        "--profile",
        default=None,
        help="AWS profile name (optional; uses default credential chain otherwise)",
    )
    parser.add_argument(
        "--terminate",
        action="store_true",
        help="Terminate all active fleets that are found (also terminates instances).",
    )
    args = parser.parse_args()

    session = (
        boto3.session.Session(profile_name=args.profile)
        if args.profile
        else boto3.session.Session()
    )
    ec2_client = session.client("ec2", region_name=args.region)

    try:
        ec2_fleets = list(_iter_ec2_fleets(ec2_client))
        spot_fleets = list(_iter_spot_fleets(ec2_client))
    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1

    print(f"Region: {args.region}")
    _print_ec2_fleets(ec2_fleets)
    _print_spot_fleets(spot_fleets)

    if args.terminate:
        ec2_fleet_ids = [fleet.get("FleetId") for fleet in ec2_fleets if fleet.get("FleetId")]
        spot_fleet_ids = [
            fleet.get("SpotFleetRequestId")
            for fleet in spot_fleets
            if fleet.get("SpotFleetRequestId")
        ]
        try:
            _terminate_ec2_fleets(ec2_client, ec2_fleet_ids)
            _terminate_spot_fleets(ec2_client, spot_fleet_ids)
        except ClientError as exc:
            print(f"AWS termination error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
