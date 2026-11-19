"""Map ORB machine tags onto Kubernetes pod labels for ``TAG_INSTANCES``.

ORB tags machines with ``key: value`` pairs; AWS writes them as EC2 tags.  The
Kubernetes provider writes them as pod labels so a container can read them
through the downward API.  Label keys and values are far more restricted than
EC2 tags, so this module is the single place that decides how a tag becomes a
label.

Key mapping
-----------

* ``orb:<name>`` (ORB's reserved tag namespace) becomes ``<label_prefix>/<name>``,
  where ``label_prefix`` is the provider's configured ``label_prefix``
  (default ``orb.io``).  ``orb:node-name`` therefore becomes
  ``orb.io/node-name``.  ``:`` is not allowed in a label key, which is why the
  key cannot be used as-is.
* Any other key that is already a valid label key (``team``,
  ``example.com/team``) is written unchanged.
* Every other key is skipped.

Keys are never rewritten beyond the ``orb:`` mapping: an invalid key is
skipped rather than mangled into something the tag's author did not choose.

Skipped tags
------------

A tag is skipped, with a reason, when:

* its key cannot be mapped to a valid label key;
* the resulting key is one of the labels ORB uses to identify its own pods
  (``<label_prefix>/managed``, ``request-id``, ``machine-id``,
  ``provider-api``, ``template-id``, ``orb-instance``).  Rewriting those could
  detach a pod from its request or make the orphan GC treat it as foreign;
* its value is not a valid label value: at most 63 characters, empty or
  matching ``[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?``.  Values are never
  truncated or rewritten, because a changed node name is worse than none.
"""

from __future__ import annotations

import re
from typing import Mapping

from orb.providers.k8s.utilities.dns_names import (
    DNS_1123_SUBDOMAIN_MAX_LEN,
    DNS_1123_SUBDOMAIN_REGEX,
)

#: Tag-key prefix ORB reserves for its own tags (``orb:node-name`` ...).
ORB_TAG_PREFIX = "orb:"

#: Label names (after ``<label_prefix>/``) that ORB stamps on every pod it
#: creates and relies on to find them.  ``TAG_INSTANCES`` never overwrites them.
RESERVED_LABEL_NAMES: frozenset[str] = frozenset(
    {"managed", "request-id", "machine-id", "provider-api", "template-id", "orb-instance"}
)

# Kubernetes label name (the part after an optional ``prefix/``) and label
# value share one character rule.  Max 63 characters each; a value may be empty.
_LABEL_NAME_RE = re.compile(r"^[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
_LABEL_NAME_MAX_LEN = 63
_LABEL_VALUE_MAX_LEN = 63


def _label_name_problem(name: str) -> str | None:
    if not name:
        return "label name is empty"
    if len(name) > _LABEL_NAME_MAX_LEN:
        return f"label name is longer than {_LABEL_NAME_MAX_LEN} characters"
    if not _LABEL_NAME_RE.fullmatch(name):
        return "label name may only contain letters, digits, '-', '_' and '.'"
    return None


def label_key_problem(key: str) -> str | None:
    """Return why ``key`` is not a valid Kubernetes label key, or ``None`` if it is."""
    prefix, sep, name = key.rpartition("/")
    if sep:
        if not prefix:
            return "label key prefix is empty"
        if len(prefix) > DNS_1123_SUBDOMAIN_MAX_LEN or not DNS_1123_SUBDOMAIN_REGEX.fullmatch(
            prefix
        ):
            return f"label key prefix {prefix!r} is not a DNS-1123 subdomain"
    return _label_name_problem(name)


def label_value_problem(value: str) -> str | None:
    """Return why ``value`` is not a valid Kubernetes label value, or ``None`` if it is."""
    if value == "":
        return None
    if len(value) > _LABEL_VALUE_MAX_LEN:
        return f"label value is longer than {_LABEL_VALUE_MAX_LEN} characters"
    if not _LABEL_NAME_RE.fullmatch(value):
        return (
            "label value must start and end with a letter or digit and may only "
            "contain letters, digits, '-', '_' and '.'"
        )
    return None


def tag_key_to_label_key(tag_key: str, label_prefix: str) -> tuple[str | None, str | None]:
    """Map one tag key to a label key.

    Returns ``(label_key, None)`` on success or ``(None, reason)`` when the tag
    must be skipped.
    """
    if tag_key.startswith(ORB_TAG_PREFIX):
        name = tag_key[len(ORB_TAG_PREFIX) :]
        problem = _label_name_problem(name)
        if problem:
            return None, f"cannot map {tag_key!r} to a label: {problem}"
        label_key = f"{label_prefix}/{name}"
    else:
        problem = label_key_problem(tag_key)
        if problem:
            return None, f"{tag_key!r} is not a valid label key: {problem}"
        label_key = tag_key
    owner, sep, name = label_key.rpartition("/")
    if sep and owner == label_prefix and name in RESERVED_LABEL_NAMES:
        return None, f"{label_key!r} is reserved for ORB's own pod identity labels"
    return label_key, None


def tags_to_labels(
    tags: Mapping[str, object], label_prefix: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Map a machine's tags to pod labels.

    Returns ``(labels, skipped)``: the labels to write, and the tags left out
    keyed by tag key with the reason.
    """
    labels: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for tag_key, raw_value in tags.items():
        key = str(tag_key)
        label_key, reason = tag_key_to_label_key(key, label_prefix)
        if label_key is None:
            skipped[key] = reason or "invalid label key"
            continue
        value = "" if raw_value is None else str(raw_value)
        problem = label_value_problem(value)
        if problem:
            skipped[key] = f"value {value!r} is not a valid label value: {problem}"
            continue
        labels[label_key] = value
    return labels, skipped


__all__ = [
    "ORB_TAG_PREFIX",
    "RESERVED_LABEL_NAMES",
    "label_key_problem",
    "label_value_problem",
    "tag_key_to_label_key",
    "tags_to_labels",
]
