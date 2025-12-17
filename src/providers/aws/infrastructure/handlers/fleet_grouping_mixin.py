"""Shared mixin for grouping instances by AWS resource ownership."""

from __future__ import annotations

from typing import Any, Optional


class FleetGroupingMixin:
    """Provides reusable grouping logic for Fleet/ASG handlers.
    Why this class exists:


    """

    grouping_chunk_size = 50

    def _group_instances_from_mapping(
        self,
        instance_ids: list[str],
        resource_mapping: dict[str, tuple[Optional[str], int]],
    ) -> dict[Optional[str], dict[str, Any]]:
        """
        Handler receives a list of instances, these instances can belong to different resources of the same type,
        e.g., multiple ASGs, EC2 Fleets, or Spot Fleets.

        Step 1: for each instance in resource_mapping we need to map it to a resource which created it.
        Unless this resouce is specified in the mapping we need to use AWS API to retrieve this information.

        This method attempts to group instances by their controlling fleet resource
        (e.g., Auto Scaling Group, EC2 Fleet) using a provided mapping. If an
        instance is not found in the mapping or the mapping is ambiguous, it falls
        back to querying the AWS API for that instance.

        Args:
            instance_ids: A list of instance IDs to be grouped.
            resource_mapping: Dictionary mapping instance_id -> (resource_id, desired_capacity).
                This mapping is typically pre-fetched from a database or cache.

        Returns:
            A dictionary where keys are the resource identifiers (e.g., fleet IDs)
            and values are dictionaries containing a list of instance IDs and
            details about the resource. A special key `None` is used for
            instances that do not belong to a recognized group.

        Example:
            >>> instance_ids = ["i-123", "i-456", "i-789", "i-abc"]
            >>> resource_mapping = {
            ...     \"i-123\": (\"asg-A\", 2),
            ...     \"i-456\": (\"asg-A\", 2),
            ...     \"i-789\": (None, 0),
            ... }
            # "i-abc" is not in the mapping and will require an API lookup.
            # "i-789" is in the mapping but has no associated ASG name, so it will also need to be looked up.
            # Assume the lookup finds it belongs to "asg-B".
            >>> self._group_instances_from_mapping(instance_ids, resource_mapping)
            {
                "asg-A": {
                    "instance_ids": ["i-123", "i-456"],
                    "fleet_details": { ... }  # Fetched details for asg-A
                },
                "asg-B": {
                    "instance_ids": ["i-abc"],
                    "fleet_details": { ... }  # Fetched details for asg-B
                },
                None: {
                    "instance_ids": ["i-789"]
                }
            }
        """

        # Initialize result containers and early return for empty input
        groups: dict[Optional[str], dict[str, Any]] = {}
        if not instance_ids:
            return groups

        # Get handler-specific label for logging (e.g., "ASG", "EC2 Fleet", "Spot Fleet")
        label = self._grouping_label()

        # Use provided resource mapping (already a dict) for O(1) lookup performance
        resource_map = resource_mapping or {}

        # Track instances that need AWS API lookup due to missing/incomplete mapping data
        instances_needing_lookup: list[str] = []

        # Track resource IDs that need detailed information fetched from AWS
        group_ids_to_fetch: set[str] = set()

        self._logger.info(
            "Processing %d instances using resource mapping for %s grouping",
            len(instance_ids),
            label,
        )

        # PHASE 1: Process each instance using cached resource mapping data
        for instance_id in instance_ids:
            # Check if we have cached mapping data for this instance
            mapping_entry = resource_map.get(instance_id)
            if mapping_entry is None:
                # No mapping data available – defer this instance to AWS lookup phase
                instances_needing_lookup.append(instance_id)
                self._logger.debug(
                    "%s grouping: %s not found in resource mapping, will query AWS",
                    label,
                    instance_id,
                )
                continue

            # Extract resource information from mapping
            resource_id, desired_capacity = mapping_entry

            # Classify the mapping entry to determine how to handle this instance
            classification, group_id = self._classify_mapping_entry(resource_id, desired_capacity)

            if classification == "group" and group_id:
                # Mapping clearly associates the instance with a managed resource (ASG/Fleet)
                self._add_instance_to_group(groups, group_id, instance_id)
                group_ids_to_fetch.add(group_id)
                self._logger.debug(
                    "%s grouping: %s mapped to %s via resource mapping",
                    label,
                    instance_id,
                    group_id,
                )
            elif classification == "non_group":
                # Mapping indicates the instance is unmanaged/standalone (RunInstances, etc.)
                self._add_non_group_instance(groups, instance_id)
                self._logger.debug(
                    "%s grouping: %s identified as non-%s via resource mapping",
                    label,
                    instance_id,
                    label.lower(),
                )
            else:
                # Mapping is ambiguous or incomplete – AWS lookup required
                instances_needing_lookup.append(instance_id)
                self._logger.debug(
                    "%s grouping: %s requires AWS lookup (incomplete mapping)",
                    label,
                    instance_id,
                )

        # PHASE 2: Query AWS APIs for instances with missing/incomplete mapping data
        if instances_needing_lookup:
            # Only invoke AWS APIs for instances that couldn't be resolved from mapping
            self._logger.info(
                "Making AWS API calls for %d %s instances with incomplete resource mapping",
                len(instances_needing_lookup),
                label.lower(),
            )
            # Delegate to handler-specific AWS API logic
            self._collect_groups_from_instances(
                instances_needing_lookup, groups, group_ids_to_fetch
            )

        # PHASE 3: Fetch detailed resource information for all identified groups
        if group_ids_to_fetch:
            # Delegate to handler-specific resource details fetching
            self._fetch_and_attach_group_details(group_ids_to_fetch, groups)

        # PHASE 4: Log performance metrics and optimization results
        self._log_grouping_summary(
            total_instances=len(instance_ids),
            group_count=len(groups),
            looked_up=len(instances_needing_lookup),
        )
        return groups

    def _group_instances_direct(
        self, instance_ids: list[str]
    ) -> dict[Optional[str], dict[str, Any]]:
        """Group instances using AWS lookups only."""
        groups: dict[Optional[str], dict[str, Any]] = {}
        if not instance_ids:
            return groups

        group_ids_to_fetch: set[str] = set()
        self._collect_groups_from_instances(instance_ids, groups, group_ids_to_fetch)

        if group_ids_to_fetch:
            self._fetch_and_attach_group_details(group_ids_to_fetch, groups)

        self._log_grouping_summary(
            total_instances=len(instance_ids),
            group_count=len(groups),
            looked_up=len(instance_ids),
        )
        return groups

    def _classify_mapping_entry(
        self, resource_id: Optional[str], desired_capacity: int
    ) -> tuple[str, Optional[str]]:
        """
        Classify mapping entry result.

        Returns tuple (classification, group_id) where classification is one of:
        - "group": instance belongs to group and group_id contains identifier
        - "non_group": instance is not part of managed group
        - "unknown": need AWS lookup
        """
        # If we have a resource_id (fleet/ASG), treat as grouped even if desired_capacity
        # isn't populated; missing desired_capacity should not force non-group handling.
        if resource_id:
            return "group", resource_id
        if resource_id is None or desired_capacity == 0:
            return "non_group", None
        return "unknown", None

    def _add_instance_to_group(
        self, groups: dict[Optional[str], dict[str, Any]], group_id: str, instance_id: str
    ) -> None:
        """Add instance to specific group, initializing structure if needed."""
        details_key = self._group_details_key()
        if group_id not in groups:
            groups[group_id] = {"instance_ids": []}
            if group_id is not None:
                groups[group_id][details_key] = None
        groups[group_id]["instance_ids"].append(instance_id)

    def _add_non_group_instance(
        self, groups: dict[Optional[str], dict[str, Any]], instance_id: str
    ) -> None:
        """Add instance to non-group bucket."""
        if None not in groups:
            groups[None] = {"instance_ids": []}
        groups[None]["instance_ids"].append(instance_id)

    def _log_grouping_summary(self, total_instances: int, group_count: int, looked_up: int) -> None:
        """Log grouping summary for diagnostics."""
        label = self._grouping_label()
        self._logger.info(
            "Grouped %d instances into %d %s groups", total_instances, group_count, label.lower()
        )
        if total_instances > looked_up:
            self._logger.info(
                "Optimized %s grouping by avoiding AWS lookups for %d instances",
                label.lower(),
                total_instances - looked_up,
            )

    # Abstract hooks for subclasses
    def _collect_groups_from_instances(
        self,
        instance_ids: list[str],
        groups: dict[Optional[str], dict[str, Any]],
        group_ids_to_fetch: set[str],
    ) -> None:
        """Populate groups using AWS API lookups (implemented by subclasses)."""
        raise NotImplementedError

    def _fetch_and_attach_group_details(
        self, group_ids: set[str], groups: dict[Optional[str], dict[str, Any]]
    ) -> None:
        """Fetch and attach resource details for grouped identifiers."""
        raise NotImplementedError

    def _group_details_key(self) -> str:
        """Return dictionary key used to store group details."""
        return "fleet_details"

    def _grouping_label(self) -> str:
        """Human-readable label for grouping operations."""
        return "fleet"
