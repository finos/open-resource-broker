"""Shared AWS cleanup helpers for onaws integration test fixtures.

All functions are best-effort: they log failures but never raise, so teardown
cannot mask the actual test failure.
"""

import logging
import time
from typing import List, Optional

from botocore.exceptions import ClientError

log = logging.getLogger("onaws.cleanup")


def wait_for_instances_terminated(
    instance_ids: List[str],
    ec2_client,
    timeout: int = 300,
) -> None:
    """Poll until all instances reach terminated or shutting-down state.

    Returns when all instances are in a terminal state or timeout is reached.
    Never raises.
    """
    if not instance_ids:
        return

    terminal = {"shutting-down", "terminated"}
    deadline = time.time() + timeout
    pending = list(instance_ids)

    log.info("Waiting for %d instance(s) to terminate (timeout=%ds)", len(pending), timeout)

    while pending and time.time() < deadline:
        still_pending = []
        try:
            response = ec2_client.describe_instances(InstanceIds=pending)
            states: dict = {}
            for reservation in response.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    states[inst["InstanceId"]] = inst["State"]["Name"]

            for iid in pending:
                state = states.get(iid)
                if state is None or state in terminal:
                    log.debug("Instance %s: %s", iid, state or "not found")
                else:
                    still_pending.append(iid)
                    log.debug("Instance %s: %s (waiting)", iid, state)

        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "InvalidInstanceID.NotFound":
                # All instances gone — treat as terminated
                log.debug("Instances not found (likely terminated): %s", pending)
                still_pending = []
            else:
                log.warning("Error polling instance states: %s", exc)
                still_pending = pending  # retry

        except Exception as exc:
            log.warning("Unexpected error polling instance states: %s", exc)
            still_pending = pending

        pending = still_pending
        if pending:
            time.sleep(10)

    if pending:
        log.warning("Timed out waiting for %d instance(s) to terminate: %s", len(pending), pending)
    else:
        log.info("All instances reached terminal state")


def cleanup_launch_templates_for_request(request_id: str, ec2_client) -> None:
    """Find launch templates tagged with orb:request-id and delete them.

    Never raises.
    """
    if not request_id:
        return

    try:
        response = ec2_client.describe_launch_templates(
            Filters=[{"Name": "tag:orb:request-id", "Values": [request_id]}]
        )
        templates = response.get("LaunchTemplates", [])
        if not templates:
            log.debug("No launch templates found for request %s", request_id)
            return

        lt_ids = [lt["LaunchTemplateId"] for lt in templates]
        log.info(
            "Deleting %d launch template(s) for request %s: %s",
            len(lt_ids),
            request_id,
            lt_ids,
        )
        for lt_id in lt_ids:
            try:
                ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
                log.info("Deleted launch template %s", lt_id)
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code in (
                    "InvalidLaunchTemplateId.NotFound",
                    "InvalidLaunchTemplateId.Malformed",
                ):
                    log.debug("Launch template %s already gone", lt_id)
                else:
                    log.warning("Failed to delete launch template %s: %s", lt_id, exc)
            except Exception as exc:
                log.warning("Unexpected error deleting launch template %s: %s", lt_id, exc)

    except Exception as exc:
        log.warning("Failed to list launch templates for request %s: %s", request_id, exc)


def get_machine_ids_from_ec2(request_id: str, ec2_client) -> List[str]:
    """Look up instance IDs tagged with orb:request-id=<request_id> in EC2.

    Used in teardown when the cleanup path has already run and we just need
    instance IDs to pass to wait_for_instances_terminated. Never raises.
    """
    try:
        response = ec2_client.describe_instances(
            Filters=[
                {"Name": "tag:orb:request-id", "Values": [request_id]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        ids = []
        for reservation in response.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                ids.append(inst["InstanceId"])
        return ids
    except Exception as exc:
        log.warning("get_machine_ids_from_ec2 failed for %s: %s", request_id, exc)
        return []


def cleanup_tracked_requests(
    tracked_request_ids: List[str],
    hfm,
    ec2_client,
    termination_timeout: int = 300,
) -> None:
    """Orchestrate full cleanup for a list of tracked request IDs.

    For each request:
      1. Collect all machine IDs regardless of request status.
      2. Call request_return_machines if any machines exist.
      3. Wait for instances to reach terminated/shutting-down.
      4. Delete any launch templates tagged with the request ID.

    Never raises.
    """
    if not tracked_request_ids:
        return

    log.info("Cleaning up %d tracked request(s)", len(tracked_request_ids))

    for req_id in tracked_request_ids:
        try:
            status = hfm.get_request_status(req_id)
            machines = status.get("requests", [{}])[0].get("machines", [])
            machine_ids = [
                m.get("machineId") or m.get("machine_id")
                for m in machines
                if m.get("machineId") or m.get("machine_id")
            ]
        except Exception as exc:
            log.warning("Could not get status for request %s: %s", req_id, exc)
            machine_ids = []

        if machine_ids:
            log.info(
                "Returning %d machine(s) for request %s: %s",
                len(machine_ids),
                req_id,
                machine_ids,
            )
            try:
                hfm.request_return_machines(machine_ids)
            except Exception as exc:
                log.warning("request_return_machines failed for request %s: %s", req_id, exc)

            wait_for_instances_terminated(machine_ids, ec2_client, timeout=termination_timeout)
        else:
            log.debug("No machines to return for request %s", req_id)

        cleanup_launch_templates_for_request(req_id, ec2_client)


def cleanup_resources_by_session_tag(
    session_id: str,
    ec2_client,
    autoscaling_client=None,
) -> None:
    """Find and clean up all resources tagged test-session=<session_id>.

    Terminates EC2 instances, deletes launch templates, and optionally deletes
    ASGs. Never raises.
    """
    if not session_id:
        return

    tag_filter = {"Name": "tag:test-session", "Values": [session_id]}
    log.info("Running session cleanup for test-session=%s", session_id)

    # Terminate tagged EC2 instances
    try:
        paginator = ec2_client.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[
                tag_filter,
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        instance_ids = []
        for page in pages:
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instance_ids.append(inst["InstanceId"])

        if instance_ids:
            log.info(
                "Session cleanup: terminating %d instance(s): %s", len(instance_ids), instance_ids
            )
            try:
                ec2_client.terminate_instances(InstanceIds=instance_ids)
            except Exception as exc:
                log.warning("Session cleanup: terminate_instances failed: %s", exc)
        else:
            log.info("Session cleanup: no tagged instances found")

    except Exception as exc:
        log.warning("Session cleanup: failed to list tagged instances: %s", exc)

    # Delete tagged launch templates
    try:
        paginator = ec2_client.get_paginator("describe_launch_templates")
        pages = paginator.paginate(Filters=[tag_filter])
        lt_ids = []
        for page in pages:
            for lt in page.get("LaunchTemplates", []):
                lt_ids.append(lt["LaunchTemplateId"])

        if lt_ids:
            log.info("Session cleanup: deleting %d launch template(s): %s", len(lt_ids), lt_ids)
            for lt_id in lt_ids:
                try:
                    ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
                except Exception as exc:
                    log.warning(
                        "Session cleanup: failed to delete launch template %s: %s", lt_id, exc
                    )
        else:
            log.info("Session cleanup: no tagged launch templates found")

    except Exception as exc:
        log.warning("Session cleanup: failed to list tagged launch templates: %s", exc)

    # Delete tagged ASGs
    if autoscaling_client is not None:
        try:
            paginator = autoscaling_client.get_paginator("describe_auto_scaling_groups")
            pages = paginator.paginate(
                Filters=[
                    {"Name": "tag-key", "Values": ["test-session"]},
                    {"Name": "tag-value", "Values": [session_id]},
                ]
            )
            asg_names = []
            for page in pages:
                for asg in page.get("AutoScalingGroups", []):
                    asg_names.append(asg["AutoScalingGroupName"])

            if asg_names:
                log.info("Session cleanup: deleting %d ASG(s): %s", len(asg_names), asg_names)
                for asg_name in asg_names:
                    try:
                        autoscaling_client.delete_auto_scaling_group(
                            AutoScalingGroupName=asg_name,
                            ForceDelete=True,
                        )
                    except Exception as exc:
                        log.warning("Session cleanup: failed to delete ASG %s: %s", asg_name, exc)
            else:
                log.info("Session cleanup: no tagged ASGs found")

        except Exception as exc:
            log.warning("Session cleanup: failed to list tagged ASGs: %s", exc)

    log.info("Session cleanup complete for test-session=%s", session_id)


def cleanup_all_orb_resources(
    ec2_client,
    autoscaling_client=None,
    session_id: Optional[str] = None,
) -> None:
    """Nuclear option: find and clean up all resources tagged orb:managed-by.

    If session_id is provided, also filters by test-session=<session_id> for
    more targeted cleanup. Terminates EC2 instances, deletes launch templates,
    and optionally deletes ASGs. Never raises.
    """
    if session_id:
        log.info("Running nuclear cleanup filtered to test-session=%s", session_id)
        cleanup_resources_by_session_tag(session_id, ec2_client, autoscaling_client)
        return

    log.info("Running nuclear cleanup of all orb:managed-by resources")

    # Terminate tagged EC2 instances
    try:
        paginator = ec2_client.get_paginator("describe_instances")
        pages = paginator.paginate(
            Filters=[
                {"Name": "tag-key", "Values": ["orb:managed-by"]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "stopping", "stopped"],
                },
            ]
        )
        instance_ids = []
        for page in pages:
            for reservation in page.get("Reservations", []):
                for inst in reservation.get("Instances", []):
                    instance_ids.append(inst["InstanceId"])

        if instance_ids:
            log.info(
                "Nuclear cleanup: terminating %d instance(s): %s", len(instance_ids), instance_ids
            )
            try:
                ec2_client.terminate_instances(InstanceIds=instance_ids)
            except Exception as exc:
                log.warning("Nuclear cleanup: terminate_instances failed: %s", exc)
        else:
            log.info("Nuclear cleanup: no tagged instances found")

    except Exception as exc:
        log.warning("Nuclear cleanup: failed to list tagged instances: %s", exc)

    # Delete tagged launch templates
    try:
        paginator = ec2_client.get_paginator("describe_launch_templates")
        pages = paginator.paginate(Filters=[{"Name": "tag-key", "Values": ["orb:managed-by"]}])
        lt_ids = []
        for page in pages:
            for lt in page.get("LaunchTemplates", []):
                lt_ids.append(lt["LaunchTemplateId"])

        if lt_ids:
            log.info("Nuclear cleanup: deleting %d launch template(s): %s", len(lt_ids), lt_ids)
            for lt_id in lt_ids:
                try:
                    ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
                except Exception as exc:
                    log.warning(
                        "Nuclear cleanup: failed to delete launch template %s: %s", lt_id, exc
                    )
        else:
            log.info("Nuclear cleanup: no tagged launch templates found")

    except Exception as exc:
        log.warning("Nuclear cleanup: failed to list tagged launch templates: %s", exc)

    # Delete tagged ASGs
    if autoscaling_client is not None:
        try:
            paginator = autoscaling_client.get_paginator("describe_auto_scaling_groups")
            pages = paginator.paginate(Filters=[{"Name": "tag-key", "Values": ["orb:managed-by"]}])
            asg_names = []
            for page in pages:
                for asg in page.get("AutoScalingGroups", []):
                    asg_names.append(asg["AutoScalingGroupName"])

            if asg_names:
                log.info("Nuclear cleanup: deleting %d ASG(s): %s", len(asg_names), asg_names)
                for asg_name in asg_names:
                    try:
                        autoscaling_client.delete_auto_scaling_group(
                            AutoScalingGroupName=asg_name,
                            ForceDelete=True,
                        )
                    except Exception as exc:
                        log.warning("Nuclear cleanup: failed to delete ASG %s: %s", asg_name, exc)
            else:
                log.info("Nuclear cleanup: no tagged ASGs found")

        except Exception as exc:
            log.warning("Nuclear cleanup: failed to list tagged ASGs: %s", exc)

    log.info("Nuclear cleanup complete")
