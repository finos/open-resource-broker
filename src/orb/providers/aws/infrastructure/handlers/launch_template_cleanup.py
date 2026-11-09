"""Launch-template cleanup concern extracted from AWSHandler.

The three methods here were previously inlined in ``AWSHandler``.  They form a
coherent *cleanup concern*: given a request ID, determine from config whether
cleanup is needed, then find and delete the matching ORB-managed launch
template.

``LaunchTemplateCleanupService`` owns this concern and is constructed by
``AWSHandler`` from its own injected dependencies.  ``AWSHandler`` retains
thin delegation wrappers (``_get_cleanup_config``, ``_cleanup_on_zero_capacity``,
``_delete_orb_launch_template``) so that existing call sites and test patches
are unaffected.
"""

from typing import Any, Callable, Iterable, Optional

from botocore.exceptions import ClientError

from orb.domain.base.ports import LoggingPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.providers.aws.configuration.cleanup_config import CleanupConfig
from orb.providers.aws.infrastructure.aws_client import AWSClient


class LaunchTemplateCleanupService:
    """Handles ORB launch-template cleanup after a resource reaches zero capacity.

    All public methods are warning-only: they never propagate exceptions to
    callers so that cleanup logic cannot block the main return/cancel flow.

    Args:
        aws_client: AWS client used to describe and delete EC2 launch templates.
        config_port: Configuration port for reading cleanup settings.  When
            ``None`` the service behaves as if cleanup is disabled.
        logger: Logging port used to emit debug / info / warning messages.
    """

    def __init__(
        self,
        aws_client: AWSClient,
        config_port: Optional[ConfigurationPort],
        logger: LoggingPort,
        config_port_provider: Optional[Callable[[], Optional[ConfigurationPort]]] = None,
    ) -> None:
        self._aws_client = aws_client
        self._fixed_config_port = config_port
        self._config_port_provider = config_port_provider
        self._logger = logger

    @property
    def _config_port(self) -> Optional[ConfigurationPort]:
        """The configuration port, read when used.

        A handler's port can be injected after the handler is built, so a snapshot
        taken at construction would keep cleanup disabled; a provider callable, when
        given, is asked every time.
        """
        if self._config_port_provider is not None:
            return self._config_port_provider()
        return self._fixed_config_port

    @_config_port.setter
    def _config_port(self, value: Optional[ConfigurationPort]) -> None:
        self._fixed_config_port = value
        self._config_port_provider = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_cleanup_config(self) -> CleanupConfig:
        """Read cleanup config from AWS provider defaults.

        Returns a default ``CleanupConfig`` (all-enabled) when the config port
        is absent, when the provider has no ``aws`` defaults block, or when
        reading raises.
        """
        try:
            if self._config_port is None:
                return CleanupConfig()
            provider_config = self._config_port.get_provider_config()
            if provider_config and provider_config.provider_defaults:
                defaults = provider_config.provider_defaults.get("aws")
                if defaults and defaults.cleanup is not None:
                    return CleanupConfig.model_validate(defaults.cleanup)
        except Exception as e:
            self._logger.warning("Failed to read cleanup config, using defaults: %s", e)
        return CleanupConfig()

    def cleanup_on_zero_capacity(
        self,
        resource_type: str,
        request_id: str,
        exclude_instance_ids: Optional[Iterable[str]] = None,
        delete_template: Optional[Callable[..., None]] = None,
    ) -> None:
        """Delete the ORB-managed launch template when a resource reaches zero capacity.

        Also deletes the versions ORB added to a user-provided launch template for
        the request. This is the single return-path entry point for both.

        Reads the cleanup config, checks that cleanup is enabled and that the
        resource type is included in the ``resources`` allow-list, then delegates
        to ``delete_orb_launch_template``.  All failures are warning-only so
        that cleanup never blocks the main return flow.

        Args:
            resource_type: Cleanup config resource key, e.g. ``"asg"``,
                ``"ec2_fleet"``, or ``"spot_fleet"``.
            request_id: The ORB request ID used to locate the launch template.
            exclude_instance_ids: Instances just submitted for termination;
                they are treated as gone even if a describe still shows them.
            delete_template: Callable that deletes the ORB-managed template; defaults
                to :meth:`delete_orb_launch_template`. Handlers pass their own wrapper.
        """
        if self._config_port is None:
            return

        try:
            cleanup = self.get_cleanup_config()
        except Exception as e:
            self._logger.warning("Failed to read cleanup config, skipping cleanup: %s", e)
            return

        if not cleanup.enabled:
            return

        if not getattr(cleanup.resources, resource_type, True):
            return

        (delete_template or self.delete_orb_launch_template)(request_id, exclude_instance_ids)
        self.delete_orb_template_versions(request_id, exclude_instance_ids)

    # Instances in these states still belong to the request. ``shutting-down``
    # is excluded: it is the in-flight tail of a return that already happened.
    _LIVE_INSTANCE_STATES = ("pending", "running", "stopping", "stopped")

    def _request_has_live_instances(
        self, request_id: str, exclude_instance_ids: Optional[Iterable[str]] = None
    ) -> bool:
        """Return True when any instance of the request is still live.

        A partial return must not delete the launch template while other
        machines of the same request remain; the cleanup runs again on the
        return that removes the last one. Instances in *exclude_instance_ids*
        were just submitted for termination and are ignored, so describe lag
        right after TerminateInstances cannot keep the template forever. If the
        lookup fails the request is assumed to still have instances, so the
        template is kept.
        """
        return self._has_live_instances("tag:orb:request-id", request_id, exclude_instance_ids)

    def _launch_template_in_use(
        self, launch_template_id: str, exclude_instance_ids: Optional[Iterable[str]] = None
    ) -> bool:
        """Return True when a live instance was launched from this launch template.

        EC2 stamps every instance with ``aws:ec2launchtemplate:id``, so this
        also covers custom or replace-mode specs that do not carry ORB's own
        request tag.
        """
        return self._has_live_instances(
            "tag:aws:ec2launchtemplate:id", launch_template_id, exclude_instance_ids
        )

    def _has_live_instances(
        self,
        filter_name: str,
        filter_value: str,
        exclude_instance_ids: Optional[Iterable[str]] = None,
    ) -> bool:
        excluded = set(exclude_instance_ids or ())
        filters = [
            {"Name": filter_name, "Values": [filter_value]},
            {"Name": "instance-state-name", "Values": list(self._LIVE_INSTANCE_STATES)},
        ]
        next_token: Optional[str] = None
        try:
            while True:
                kwargs: dict = {"Filters": filters}
                if next_token:
                    kwargs["NextToken"] = next_token
                response = self._aws_client.ec2_client.describe_instances(**kwargs)
                for reservation in response.get("Reservations", []):
                    for instance in reservation.get("Instances", []):
                        if instance.get("InstanceId") not in excluded:
                            return True
                next_token = response.get("NextToken")
                if not isinstance(next_token, str) or not next_token:
                    return False
        except Exception as e:
            self._logger.warning(
                "Could not check for live instances (%s=%s); keeping launch template: %s",
                filter_name,
                filter_value,
                e,
            )
            return True

    def _launch_template_prefix(self) -> Optional[str]:
        """Return the configured launch template name prefix, or None if unavailable."""
        if self._config_port is None:
            return None
        try:
            prefix = self._config_port.get_resource_prefix("launch_template")
        except Exception as e:
            self._logger.warning("Could not read launch template name prefix: %s", e)
            return None
        return prefix if isinstance(prefix, str) else None

    def delete_orb_launch_template(
        self, request_id: str, exclude_instance_ids: Optional[Iterable[str]] = None
    ) -> None:
        """Delete the ORB-managed launch template for a request, if one exists.

        Finds templates tagged with the request ID, verifies the
        ``orb:managed-by`` tag and that the template name carries the configured
        launch template prefix followed by the request ID, then deletes it.
        An empty prefix is valid (the configuration adapter returns "" when no
        prefix is set or its lookup fails): the expected name is then
        ``<request id>-<template id>``, which still matches only this request's
        templates.  Nothing is deleted if the port returns a non-string or raises.  A template kept
        because its name does not match (for example after the prefix was
        changed) is left in place and logged.  Respects the cleanup config
        dry_run flag.  All failures are warning-only
        so that LT cleanup never blocks the main return flow.
        """
        if self._config_port is None:
            self._logger.warning(
                "config_port not injected; skipping launch template cleanup for %s", request_id
            )
            return

        try:
            cleanup = self.get_cleanup_config()
        except Exception as e:
            self._logger.warning("Could not read cleanup config, skipping LT cleanup: %s", e)
            return

        if not cleanup.enabled or not cleanup.delete_launch_template:
            return

        dry_run = cleanup.dry_run

        if self._request_has_live_instances(request_id, exclude_instance_ids):
            self._logger.info(
                "Request %s still has live instances; keeping its launch template",
                request_id,
            )
            return

        prefix = self._launch_template_prefix()
        if prefix is None:
            self._logger.warning(
                "Launch template name prefix is unavailable; skipping launch template "
                "cleanup for %s",
                request_id,
            )
            return
        expected_name_prefix = f"{prefix}{request_id}-"

        try:
            response = self._aws_client.ec2_client.describe_launch_templates(
                Filters=[{"Name": "tag:orb:request-id", "Values": [request_id]}]
            )
            templates = response.get("LaunchTemplates", [])
            if not templates:
                self._logger.debug(
                    "No launch templates found for request %s; nothing to clean up", request_id
                )
                return

            self._logger.info(
                "Found %d launch template(s) to clean up for request %s",
                len(templates),
                request_id,
            )

            for lt in templates:
                tags = {t["Key"]: t["Value"] for t in lt.get("Tags", [])}
                lt_id = lt["LaunchTemplateId"]
                lt_name = lt.get("LaunchTemplateName", lt_id)

                if tags.get("orb:managed-by") != "open-resource-broker":
                    self._logger.warning(
                        "Launch template %s (%s) is not ORB-managed (orb:managed-by tag absent"
                        " or wrong); skipping deletion",
                        lt_name,
                        lt_id,
                    )
                    continue

                if tags.get("orb:request-id") != request_id:
                    self._logger.info(
                        "Launch template %s (%s) belongs to a different request; keeping it",
                        lt_name,
                        lt_id,
                    )
                    continue

                if not str(lt_name).startswith(expected_name_prefix):
                    self._logger.info(
                        "Launch template %s (%s) is tagged for request %s but its name does not "
                        "start with %r; keeping it",
                        lt_name,
                        lt_id,
                        request_id,
                        expected_name_prefix,
                    )
                    continue

                if self._launch_template_in_use(lt_id, exclude_instance_ids):
                    self._logger.info(
                        "Launch template %s (%s) still has live instances; keeping it",
                        lt_name,
                        lt_id,
                    )
                    continue

                if dry_run:
                    self._logger.info(
                        "[dry-run] Would delete launch template %s (%s) for request %s",
                        lt_name,
                        lt_id,
                        request_id,
                    )
                    continue

                try:
                    self._aws_client.ec2_client.delete_launch_template(LaunchTemplateId=lt_id)
                    self._logger.info(
                        "Deleted launch template %s (%s) for request %s",
                        lt_name,
                        lt_id,
                        request_id,
                    )
                except ClientError as e:
                    self._logger.warning(
                        "Failed to delete launch template %s (%s) for request %s: %s",
                        lt_name,
                        lt_id,
                        request_id,
                        e,
                    )

        except ClientError as e:
            self._logger.warning(
                "Failed to describe launch templates for request %s: %s",
                request_id,
                e,
            )
        except Exception as e:
            self._logger.warning(
                "Unexpected error cleaning up launch templates for request %s: %s",
                request_id,
                e,
            )

    def delete_orb_template_versions(
        self, request_id: str, released_instance_ids: Optional[Iterable[str]] = None
    ) -> None:
        """Delete the versions ORB created on user-provided launch templates for a request.

        When a template supplies a launch template and overrides, ORB adds a
        version to the user's template for each request; the template itself is
        never ORB's to delete, so without this the versions accumulate toward
        the per-template version limit.

        The versions are found from the instances that were just released: EC2
        stamps each instance with the launch template id and version it started
        from. A version is deleted only when all of these hold:

        * its template is not ORB-managed (those are removed as a whole),
        * it is not the default version,
        * its description is exactly the one ORB writes for this request,
        * the request has no live instance left.

        User-created versions therefore never match. All failures are
        warning-only so the cleanup cannot block the return flow.
        """
        released = list(released_instance_ids or ())
        if not released or self._config_port is None:
            return
        try:
            cleanup = self.get_cleanup_config()
        except Exception as e:
            self._logger.warning("Could not read cleanup config, skipping version cleanup: %s", e)
            return
        if not cleanup.enabled or not cleanup.delete_launch_template:
            return
        if self._request_has_live_instances(request_id, released):
            return

        try:
            used = self._launch_template_versions_of(released)
            description = f"Override for request {request_id}"
            for lt_id, versions in used.items():
                if self._is_orb_managed_template(lt_id):
                    continue
                orb_versions = [
                    v for v in sorted(versions) if self._version_is_orbs(lt_id, v, description)
                ]
                if not orb_versions:
                    continue
                if cleanup.dry_run:
                    self._logger.info(
                        "[dry-run] Would delete versions %s of launch template %s for request %s",
                        orb_versions,
                        lt_id,
                        request_id,
                    )
                    continue
                self._delete_versions(lt_id, orb_versions, request_id)
        except Exception as e:
            self._logger.warning(
                "Unexpected error cleaning up launch template versions for request %s: %s",
                request_id,
                e,
            )

    def delete_override_version(
        self, launch_template_id: str, version: str, request_id: str
    ) -> None:
        """Delete the single version ORB added to a user's template for a failed launch.

        Used when the launch call was definitively rejected, so no instance can be
        using the version. The version is deleted only when it is the recorded
        one, is neither the default nor the first version, and carries exactly the
        description ORB writes for this request. All failures are warning-only.
        """
        if self._config_port is None:
            return
        try:
            cleanup = self.get_cleanup_config()
            if not cleanup.enabled or not cleanup.delete_launch_template:
                return
            if not str(version).isdigit() or int(version) == 1:
                return
            description = f"Override for request {request_id}"
            if not self._version_is_orbs(launch_template_id, str(version), description):
                return
            if cleanup.dry_run:
                self._logger.info(
                    "[dry-run] Would delete version %s of launch template %s for request %s",
                    version,
                    launch_template_id,
                    request_id,
                )
                return
            self._delete_versions(launch_template_id, [str(version)], request_id)
        except Exception as e:
            self._logger.warning(
                "Could not clean up version %s of launch template %s for request %s: %s",
                version,
                launch_template_id,
                request_id,
                e,
            )

    def _launch_template_versions_of(self, instance_ids: list[str]) -> dict[str, set[str]]:
        """Return ``{launch template id: {version}}`` read from the instances' EC2 tags."""
        used: dict[str, set[str]] = {}
        response = self._aws_client.ec2_client.describe_instances(InstanceIds=instance_ids)
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                lt_id = tags.get("aws:ec2launchtemplate:id")
                version = tags.get("aws:ec2launchtemplate:version")
                if lt_id and version and version.isdigit():
                    used.setdefault(lt_id, set()).add(version)
        return used

    def _is_orb_managed_template(self, launch_template_id: str) -> bool:
        response = self._aws_client.ec2_client.describe_launch_templates(
            LaunchTemplateIds=[launch_template_id]
        )
        for template in response.get("LaunchTemplates", []):
            tags = {t["Key"]: t["Value"] for t in template.get("Tags", [])}
            if tags.get("orb:managed-by") == "open-resource-broker":
                return True
        return False

    def _version_is_orbs(self, launch_template_id: str, version: str, description: str) -> bool:
        """True for a non-default version carrying exactly the description ORB wrote."""
        response: Any = self._aws_client.ec2_client.describe_launch_template_versions(
            LaunchTemplateId=launch_template_id, Versions=[version]
        )
        for entry in response.get("LaunchTemplateVersions", []):
            if entry.get("DefaultVersion"):
                return False
            if entry.get("VersionDescription") == description:
                return True
        return False

    def _delete_versions(
        self, launch_template_id: str, versions: list[str], request_id: str
    ) -> None:
        try:
            response = self._aws_client.ec2_client.delete_launch_template_versions(
                LaunchTemplateId=launch_template_id, Versions=versions
            )
        except ClientError as e:
            self._logger.warning(
                "Failed to delete versions %s of launch template %s for request %s: %s",
                versions,
                launch_template_id,
                request_id,
                e,
            )
            return
        failed = response.get("UnsuccessfullyDeletedLaunchTemplateVersions", [])
        for entry in failed:
            self._logger.warning(
                "Launch template %s version %s was not deleted for request %s: %s",
                launch_template_id,
                entry.get("VersionNumber"),
                request_id,
                (entry.get("ResponseError") or {}).get("Message", "unknown reason"),
            )
        deleted = [
            v for v in versions if str(v) not in {str(f.get("VersionNumber")) for f in failed}
        ]
        if deleted:
            self._logger.info(
                "Deleted versions %s of launch template %s for request %s",
                deleted,
                launch_template_id,
                request_id,
            )
