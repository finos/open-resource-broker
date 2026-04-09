"""
Shared tag utilities for AWS resource handlers.

All ORB-managed resources receive a reserved ``orb:`` tag prefix so they can be
identified and filtered independently of any user-supplied tags.  User tags whose
Key starts with ``orb:`` are silently stripped to prevent spoofing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from orb.domain.base.ports.logging_port import LoggingPort

SYSTEM_TAG_PREFIX = "orb:"

MANAGED_BY_TAG: dict[str, str] = {"orb:managed-by": "open-resource-broker"}


def build_system_tags(
    request_id: str,
    template_id: str,
    provider_api: str,
    created_at: str | None = None,
) -> list[dict[str, str]]:
    """Return the list of ``{"Key": k, "Value": v}`` dicts for all ORB system tags.

    Args:
        request_id:   The ORB request UUID.
        template_id:  The ORB template UUID.
        provider_api: The AWS API used (e.g. ``"SpotFleet"``, ``"EC2Fleet"``).
        created_at:   Optional ISO-8601 UTC timestamp.  Defaults to *now*.

    Returns:
        A list of tag dicts ready to be embedded in an AWS API call.
    """
    ts = created_at or datetime.now(timezone.utc).isoformat()
    return [
        {"Key": "orb:managed-by", "Value": "open-resource-broker"},
        {"Key": "orb:request-id", "Value": str(request_id)},
        {"Key": "orb:template-id", "Value": str(template_id)},
        {"Key": "orb:provider-api", "Value": provider_api},
        {"Key": "orb:created-at", "Value": ts},
    ]


def build_resource_tags(
    config_port: Any,
    request_id: str,
    template_id: str,
    resource_prefix_key: str,
    provider_api: str,
    template_tags: dict[str, Any] | None = None,
    logger: LoggingPort | None = None,
) -> list[dict[str, str]]:
    """Build the flat tag list for an AWS resource.

    Combines a Name tag (prefix + request_id), any template-level tags, and
    the standard ORB system tags into a single merged list.

    Args:
        config_port:        ConfigurationPort used to resolve the resource prefix.
        request_id:         The ORB request UUID (string).
        template_id:        The ORB template UUID (string).
        resource_prefix_key: Key passed to ``config_port.get_resource_prefix``
                             (e.g. ``"fleet"``, ``"spot_fleet"``, ``"asg"``).
        provider_api:       AWS API label for the system tag (e.g. ``"EC2Fleet"``).
        template_tags:      Optional dict of user-supplied tags from the template.
        logger:             Optional LoggingPort for warnings about stripped keys.

    Returns:
        Merged list of ``{"Key": k, "Value": v}`` dicts ready for AWS API calls.
    """
    if template_tags:
        reserved = [k for k in template_tags if k.startswith(SYSTEM_TAG_PREFIX)]
        if reserved:
            if logger is not None:
                logger.warning(
                    "Stripping reserved tag key(s) from template_tags (must not start with '%s'): %s",
                    SYSTEM_TAG_PREFIX,
                    ", ".join(sorted(reserved)),
                )
            template_tags = {k: v for k, v in template_tags.items() if k not in reserved}

    prefix = config_port.get_resource_prefix(resource_prefix_key)
    user_tags: list[dict[str, str]] = [{"Key": "Name", "Value": f"{prefix}{request_id}"}]
    if template_tags:
        user_tags.extend([{"Key": k, "Value": str(v)} for k, v in template_tags.items()])
    return merge_tags(
        user_tags,
        build_system_tags(
            request_id=request_id,
            template_id=template_id,
            provider_api=provider_api,
        ),
    )


def merge_tags(
    user_tags: list[dict[str, str]],
    system_tags: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge user-supplied tags with ORB system tags.

    Rules:
    1. Any user tag whose ``Key`` starts with ``orb:`` is stripped (reserved namespace).
    2. System tags are appended after the filtered user tags.
    3. If a key appears in both (after stripping), the system tag value wins because
       system tags come last and AWS uses last-write-wins for duplicate keys.

    Args:
        user_tags:   Tags supplied by the caller / template (``{"Key": ..., "Value": ...}``).
        system_tags: Tags produced by :func:`build_system_tags`.

    Returns:
        Deduplicated merged list with system tags taking precedence.
    """
    # Strip reserved keys from user tags
    filtered = [t for t in user_tags if not t.get("Key", "").startswith(SYSTEM_TAG_PREFIX)]

    # Build final list; system tags override duplicates
    system_keys = {t["Key"] for t in system_tags}
    deduped = [t for t in filtered if t.get("Key") not in system_keys]
    return deduped + system_tags
