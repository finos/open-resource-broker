#!/usr/bin/env python3

import argparse
import sys
from typing import Iterable, List

import boto3
from botocore.exceptions import ClientError

ACTIVE_STATES = [
    "pending",
    "running",
    "stopping",
    "stopped",
    "shutting-down",
]


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _collect_instance_ids(ec2_client, name_prefix: str) -> List[str]:
    paginator = ec2_client.get_paginator("describe_instances")
    filters = [
        {"Name": "tag:Name", "Values": [f"{name_prefix}*"]},
        {"Name": "instance-state-name", "Values": ACTIVE_STATES},
    ]

    instance_ids: List[str] = []
    for page in paginator.paginate(Filters=filters):
        for reservation in page.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                instance_id = instance.get("InstanceId")
                if instance_id:
                    instance_ids.append(instance_id)

    return instance_ids


def _terminate_instances(ec2_client, instance_ids: List[str], dry_run: bool) -> None:
    if not instance_ids:
        print("No instances found to terminate.")
        return

    print(f"Found {len(instance_ids)} instance(s) to terminate.")
    for chunk in _chunked(instance_ids, 1000):
        try:
            ec2_client.terminate_instances(InstanceIds=chunk, DryRun=dry_run)
            if dry_run:
                print(f"Dry run succeeded for {len(chunk)} instance(s): {chunk}")
            else:
                print(f"Termination started for {len(chunk)} instance(s): {chunk}")
        except ClientError as exc:
            if dry_run and exc.response.get("Error", {}).get("Code") == "DryRunOperation":
                print(f"Dry run succeeded for {len(chunk)} instance(s): {chunk}")
                continue
            raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Terminate EC2 instances in a region whose Name tag starts with a prefix."
    )
    parser.add_argument("--region", default="eu-west-1", help="AWS region (default: eu-west-1)")
    parser.add_argument(
        "--prefix",
        default="req-",
        help='Name tag prefix to match (default: "req-")',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate permissions without terminating instances.",
    )
    args = parser.parse_args()

    session = boto3.session.Session()
    ec2_client = session.client("ec2", region_name=args.region)

    try:
        instance_ids = _collect_instance_ids(ec2_client, args.prefix)
        _terminate_instances(ec2_client, instance_ids, args.dry_run)
    except ClientError as exc:
        print(f"AWS error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
