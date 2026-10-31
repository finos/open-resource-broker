"""Maintenance sweep for ORB-managed launch templates that nothing uses any more.

When a whole resource is cancelled and its member instances cannot be read, ORB
keeps the launch template it created so it never deletes one that is still in
use. If no later return happens for that request the template stays behind. This
sweep finds such templates and deletes them. It is an explicit operator action,
not a background job: nothing here runs unless it is asked for.

A template is deleted only when every one of these holds:

* it carries the ``orb:managed-by`` tag (templates a user supplied never do),
* it is older than the minimum age, so a template created for a request that has
  not launched anything yet is not removed,
* no pending, running, stopping or stopped instance was launched from it and no
  instance carries its request id,
* no Auto Scaling group, EC2 Fleet or Spot Fleet request that is still active
  references it.

Every keep-or-delete decision is made, from lookups only, before the first delete.
A lookup that fails therefore aborts the sweep with nothing deleted: a template is
never deleted on incomplete information. A delete that fails part-way ends the
sweep with a report that lists what was deleted, what failed and what was left.

The sweep sees every ORB-managed template in the provider's account and region,
including those of other ORB installations that share them (the ``orb:managed-by``
tag is the same for all of them); only unused ones are deleted. Other regions need a
run per provider instance.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Iterable, Optional

from botocore.exceptions import ClientError

from orb.domain.base.ports import LoggingPort
from orb.providers.aws.resilience.call_executor import AWSCallExecutor
from orb.providers.base.strategy import ProviderOperation, ProviderResult

if TYPE_CHECKING:
    from orb.domain.base.ports.configuration_port import ConfigurationPort
    from orb.providers.aws.infrastructure.aws_client import AWSClient

MANAGED_BY_TAG = "orb:managed-by"
MANAGED_BY_VALUE = "open-resource-broker"
REQUEST_ID_TAG = "orb:request-id"

DEFAULT_MIN_AGE_HOURS = 24
# A template created moments ago may belong to a request that has not launched
# anything yet. Anything younger than this is kept unless the caller forces it.
MIN_AGE_FLOOR_HOURS = 1

_LIVE_INSTANCE_STATES = ["pending", "running", "stopping", "stopped"]
_ACTIVE_FLEET_STATES = ["submitted", "active", "modifying"]


class SweepAborted(Exception):
    """A lookup failed, so nothing was deleted."""


class AWSLaunchTemplateSweepService:
    """Delete ORB-managed launch templates that no live resource uses."""

    def __init__(
        self,
        aws_client: "AWSClient",
        logger: LoggingPort,
        config_port: Optional["ConfigurationPort"] = None,
    ) -> None:
        self._aws_client = aws_client
        self._logger = logger
        self._config_port = config_port
        self._executor = AWSCallExecutor.for_client(aws_client, "launch_template_sweep", logger)

    async def cleanup_orphaned_templates(self, operation: ProviderOperation) -> ProviderResult:
        """Provider operation entry point (blocking AWS calls run in a worker thread)."""
        params = operation.parameters
        dry_run = bool(params.get("dry_run", False))
        force = bool(params.get("force", False))
        min_age_hours = params.get("min_age_hours", DEFAULT_MIN_AGE_HOURS)
        if (
            not isinstance(min_age_hours, int)
            or isinstance(min_age_hours, bool)
            or min_age_hours < 0
        ):
            return ProviderResult.error_result(
                "min_age_hours must be a non-negative integer", "INVALID_PARAMETERS"
            )
        if min_age_hours < MIN_AGE_FLOOR_HOURS and not force:
            return ProviderResult.error_result(
                f"min_age_hours below {MIN_AGE_FLOOR_HOURS} can delete a template that an "
                "in-flight request has just created; pass force to allow it",
                "INVALID_PARAMETERS",
            )
        try:
            report = await asyncio.to_thread(
                self.sweep, dry_run=dry_run, min_age=timedelta(hours=min_age_hours)
            )
        except SweepAborted as exc:
            return ProviderResult.error_result(
                f"Launch template sweep aborted, nothing was deleted: {exc}",
                "LAUNCH_TEMPLATE_SWEEP_ABORTED",
            )
        except Exception as exc:
            return ProviderResult.error_result(
                f"Launch template sweep failed: {exc}", "LAUNCH_TEMPLATE_SWEEP_FAILED"
            )
        if report.get("failed"):
            return ProviderResult.error_result(
                f"Deleted {len(report['deleted'])} launch template(s); "
                f"{len(report['failed'])} could not be deleted",
                "LAUNCH_TEMPLATE_SWEEP_PARTIAL",
            ).model_copy(update={"data": report})
        return ProviderResult.success_result(report, {"operation": "cleanup_orphaned_resources"})

    # ------------------------------------------------------------------
    # Sweep
    # ------------------------------------------------------------------

    def sweep(self, *, dry_run: bool, min_age: timedelta) -> dict[str, Any]:
        """Return ``{"deleted", "would_delete", "kept", "failed"}`` for the ORB-managed templates."""
        if not self._cleanup_allowed():
            return {
                "deleted": [],
                "would_delete": [],
                "kept": [],
                "failed": [],
                "message": "Launch template cleanup is disabled in the provider configuration",
            }
        dry_run = dry_run or self._configured_dry_run()

        templates = self._managed_templates()
        referenced = self._referenced_template_ids() if templates else set()
        now = datetime.now(timezone.utc)

        # Decide for every template first. A failed lookup raises here, before
        # anything has been deleted.
        to_delete: list[dict[str, str]] = []
        kept: list[dict[str, str]] = []
        for template in templates:
            lt_id = template["LaunchTemplateId"]
            name = template.get("LaunchTemplateName", lt_id)
            request_id = {t["Key"]: t["Value"] for t in template.get("Tags", [])}.get(
                REQUEST_ID_TAG, ""
            )
            entry = {"launch_template_id": lt_id, "name": name, "request_id": request_id}
            reason = self._keep_reason(template, request_id, referenced, now, min_age)
            if reason:
                kept.append({**entry, "reason": reason})
            else:
                to_delete.append(entry)

        if dry_run:
            return {"deleted": [], "would_delete": to_delete, "kept": kept, "failed": []}

        deleted: list[dict[str, str]] = []
        failed: list[dict[str, str]] = []
        for entry in to_delete:
            try:
                self._delete_template(entry["launch_template_id"])
            except Exception as exc:
                self._logger.warning(
                    "Could not delete launch template %s: %s", entry["launch_template_id"], exc
                )
                failed.append({**entry, "error": str(exc)})
                continue
            self._logger.info("Deleted unused launch template %s", entry["name"])
            deleted.append(entry)
        return {"deleted": deleted, "would_delete": [], "kept": kept, "failed": failed}

    def _keep_reason(
        self,
        template: dict[str, Any],
        request_id: str,
        referenced: set[str],
        now: datetime,
        min_age: timedelta,
    ) -> str:
        """Return why a template must be kept, or an empty string when it may go."""
        created = template.get("CreateTime")
        if not isinstance(created, datetime):
            return "creation time unknown"
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now - created < min_age:
            return "younger than the minimum age"
        lt_id = template["LaunchTemplateId"]
        if lt_id in referenced:
            return "referenced by an active group or fleet"
        if self._has_live_instances("tag:aws:ec2launchtemplate:id", lt_id):
            return "live instances were launched from it"
        if request_id and self._has_live_instances(f"tag:{REQUEST_ID_TAG}", request_id):
            return "its request still has live instances"
        return ""

    # ------------------------------------------------------------------
    # AWS lookups (each raises SweepAborted on failure)
    # ------------------------------------------------------------------

    def _call(self, func: Any, name: str, operation_type: str = "read_only", **kwargs: Any) -> Any:
        return self._executor.execute(
            func,
            operation_type=operation_type,
            operation_name=name,
            preserve_client_error=True,
            log_response=False,
            **kwargs,
        )

    def _pages(self, func: Any, name: str, result_key: str, **kwargs: Any) -> Iterable[Any]:
        token: Optional[str] = None
        try:
            while True:
                page_kwargs = dict(kwargs)
                if token:
                    page_kwargs["NextToken"] = token
                response = self._call(func, name, **page_kwargs)
                yield from response.get(result_key, [])
                token = response.get("NextToken")
                if not isinstance(token, str) or not token:
                    return
        except Exception as exc:
            raise SweepAborted(f"{name} failed: {exc}") from exc

    def _managed_templates(self) -> list[dict[str, Any]]:
        return list(
            self._pages(
                self._aws_client.ec2_client.describe_launch_templates,
                "describe_launch_templates",
                "LaunchTemplates",
                Filters=[{"Name": f"tag:{MANAGED_BY_TAG}", "Values": [MANAGED_BY_VALUE]}],
            )
        )

    def _has_live_instances(self, filter_name: str, filter_value: str) -> bool:
        pages = self._pages(
            self._aws_client.ec2_client.describe_instances,
            "describe_instances",
            "Reservations",
            Filters=[
                {"Name": filter_name, "Values": [filter_value]},
                {"Name": "instance-state-name", "Values": _LIVE_INSTANCE_STATES},
            ],
            MaxResults=5,
        )
        return any(reservation.get("Instances") for reservation in pages)

    def _referenced_template_ids(self) -> set[str]:
        """Launch template ids used by Auto Scaling groups and active fleets."""
        ids: set[str] = set()

        for group in self._pages(
            self._aws_client.autoscaling_client.describe_auto_scaling_groups,
            "describe_auto_scaling_groups",
            "AutoScalingGroups",
        ):
            ids.update(_template_ids_of_group(group))

        for fleet in self._pages(
            self._aws_client.ec2_client.describe_fleets,
            "describe_fleets",
            "Fleets",
            Filters=[{"Name": "fleet-state", "Values": _ACTIVE_FLEET_STATES}],
        ):
            ids.update(_template_ids_of_configs(fleet.get("LaunchTemplateConfigs", [])))

        for request in self._pages(
            self._aws_client.ec2_client.describe_spot_fleet_requests,
            "describe_spot_fleet_requests",
            "SpotFleetRequestConfigs",
        ):
            if request.get("SpotFleetRequestState") in _ACTIVE_FLEET_STATES:
                config = request.get("SpotFleetRequestConfig", {})
                ids.update(_template_ids_of_configs(config.get("LaunchTemplateConfigs", [])))
        return ids

    def _delete_template(self, launch_template_id: str) -> None:
        try:
            self._call(
                self._aws_client.ec2_client.delete_launch_template,
                "delete_launch_template",
                operation_type="standard",
                LaunchTemplateId=launch_template_id,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "InvalidLaunchTemplateId.NotFound":
                return
            raise

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _cleanup_config(self) -> Any:
        from orb.providers.aws.infrastructure.handlers.launch_template_cleanup import (
            LaunchTemplateCleanupService,
        )

        return LaunchTemplateCleanupService(
            self._aws_client, self._config_port, self._logger
        ).get_cleanup_config()

    def _cleanup_allowed(self) -> bool:
        cleanup = self._cleanup_config()
        return bool(cleanup.enabled and cleanup.delete_launch_template)

    def _configured_dry_run(self) -> bool:
        return bool(self._cleanup_config().dry_run)


def _template_ids_of_configs(configs: Iterable[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for config in configs:
        spec = config.get("LaunchTemplateSpecification") or {}
        if spec.get("LaunchTemplateId"):
            ids.add(spec["LaunchTemplateId"])
    return ids


def _template_ids_of_group(group: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    direct = group.get("LaunchTemplate") or {}
    if direct.get("LaunchTemplateId"):
        ids.add(direct["LaunchTemplateId"])
    mixed = (group.get("MixedInstancesPolicy") or {}).get("LaunchTemplate") or {}
    spec = mixed.get("LaunchTemplateSpecification") or {}
    if spec.get("LaunchTemplateId"):
        ids.add(spec["LaunchTemplateId"])
    return ids
