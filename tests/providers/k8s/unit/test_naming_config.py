"""Tests for K8sNamingConfig and the configurable make_*_name functions.

Covers:
- K8sNamingConfig validation (prefix too long, uuid_chars overflow per-kind budget)
- Each make_*_name honours the naming config
- DNS-1123 compliance of generated names
- Per-kind length budget respected
- Backward-compat: names without a naming config match the historical pattern
- parse_statefulset_pod_ordinal still works on both old and new name formats
"""

from __future__ import annotations

import re

import pytest

from orb.providers.k8s.configuration.config import K8sNamingConfig, K8sProviderConfig
from orb.providers.k8s.utilities.deployment_spec import make_deployment_name
from orb.providers.k8s.utilities.job_spec import make_job_name
from orb.providers.k8s.utilities.pod_spec import make_pod_name
from orb.providers.k8s.utilities.statefulset_spec import (
    make_statefulset_name,
    parse_statefulset_pod_ordinal,
)

# ---------------------------------------------------------------------------
# DNS-1123 label pattern validator used in assertions
# ---------------------------------------------------------------------------

_DNS_1123_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?$")

REQUEST_ID = "550e8400-e29b-41d4-a716-446655440000"
# Stripped: "550e8400e29b41d4a716446655440000" (32 hex chars)


def _is_dns_1123(name: str) -> bool:
    return bool(_DNS_1123_RE.match(name))


# ---------------------------------------------------------------------------
# K8sNamingConfig validation
# ---------------------------------------------------------------------------


class TestK8sNamingConfigValidation:
    """Unit tests for K8sNamingConfig field and budget validation."""

    def test_default_construction_succeeds(self) -> None:
        cfg = K8sNamingConfig()
        assert cfg.prefix == "orb"
        assert cfg.uuid_chars == 20

    def test_custom_prefix_and_uuid_chars(self) -> None:
        cfg = K8sNamingConfig(prefix="myapp", uuid_chars=10)
        assert cfg.prefix == "myapp"
        assert cfg.uuid_chars == 10

    def test_prefix_not_dns_1123_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            K8sNamingConfig(prefix="My-App")  # uppercase not allowed

    def test_prefix_with_invalid_chars_rejected(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            K8sNamingConfig(prefix="app_name")  # underscore not allowed

    def test_prefix_too_long_rejected(self) -> None:
        with pytest.raises(ValueError, match="too long"):
            K8sNamingConfig(prefix="a" * 21)

    def test_prefix_exactly_20_chars_accepted(self) -> None:
        cfg = K8sNamingConfig(prefix="a" * 20)
        # Budget: 20+1+20 = 41 <= 47 (deployment), fine
        assert cfg.prefix == "a" * 20

    def test_uuid_chars_too_small_rejected(self) -> None:
        with pytest.raises(ValueError):
            K8sNamingConfig(uuid_chars=7)

    def test_uuid_chars_too_large_rejected(self) -> None:
        with pytest.raises(ValueError):
            K8sNamingConfig(uuid_chars=33)

    def test_deployment_budget_overflow_rejected(self) -> None:
        """prefix=orb(3) + 1 + uuid_chars=32 = 36 > max_deployment=35."""
        with pytest.raises(ValueError, match="Deployment"):
            K8sNamingConfig(prefix="orb", uuid_chars=32, max_deployment_name_len=35)

    def test_pod_budget_overflow_rejected(self) -> None:
        """With tight max_pod_name_len, prefix+uuid+seq may overflow."""
        with pytest.raises(ValueError, match="Pod"):
            K8sNamingConfig(prefix="orb", uuid_chars=20, max_pod_name_len=28)

    def test_statefulset_budget_overflow_rejected(self) -> None:
        with pytest.raises(ValueError, match="StatefulSet"):
            K8sNamingConfig(prefix="orb", uuid_chars=20, max_statefulset_name_len=23)

    def test_job_budget_overflow_rejected(self) -> None:
        with pytest.raises(ValueError, match="Job"):
            K8sNamingConfig(prefix="orb", uuid_chars=20, max_job_name_len=23)

    def test_all_failures_listed_in_single_error(self) -> None:
        """When multiple kinds overflow, one ValueError lists all of them."""
        with pytest.raises(ValueError) as exc_info:
            K8sNamingConfig(
                prefix="orb",
                uuid_chars=20,
                max_pod_name_len=20,
                max_deployment_name_len=20,
                max_statefulset_name_len=20,
                max_job_name_len=20,
            )
        msg = str(exc_info.value)
        assert "Pod" in msg
        assert "Deployment" in msg
        assert "StatefulSet" in msg
        assert "Job" in msg


# ---------------------------------------------------------------------------
# make_pod_name honours K8sNamingConfig
# ---------------------------------------------------------------------------


class TestMakePodName:
    def test_default_no_naming_config(self) -> None:
        """Without naming config, historical 20-char uuid pattern."""
        name = make_pod_name(REQUEST_ID, 0)
        assert name == "orb-550e8400e29b41d4a716-0000"
        assert _is_dns_1123(name)

    def test_default_naming_config(self) -> None:
        """K8sNamingConfig() defaults = same as no-naming-config."""
        cfg = K8sNamingConfig()
        name_with = make_pod_name(REQUEST_ID, 0, naming=cfg)
        name_without = make_pod_name(REQUEST_ID, 0)
        assert name_with == name_without

    def test_custom_prefix_and_uuid_chars(self) -> None:
        cfg = K8sNamingConfig(prefix="job", uuid_chars=8)
        name = make_pod_name(REQUEST_ID, 0, naming=cfg)
        assert name.startswith("job-")
        # uuid segment = first 8 chars of "550e8400e29b41d4a716446655440000" = "550e8400"
        assert name == "job-550e8400-0000"

    def test_seq_zero_padded_to_4_digits(self) -> None:
        cfg = K8sNamingConfig(prefix="orb", uuid_chars=8)
        assert make_pod_name(REQUEST_ID, 0, naming=cfg).endswith("-0000")
        assert make_pod_name(REQUEST_ID, 1, naming=cfg).endswith("-0001")
        assert make_pod_name(REQUEST_ID, 9999, naming=cfg).endswith("-9999")

    def test_dns_1123_compliance(self) -> None:
        cfg = K8sNamingConfig(prefix="orb", uuid_chars=12)
        for seq in range(5):
            name = make_pod_name(REQUEST_ID, seq, naming=cfg)
            assert _is_dns_1123(name), f"Name {name!r} is not DNS-1123 compliant"

    def test_max_pod_name_len_respected(self) -> None:
        cfg = K8sNamingConfig(prefix="orb", uuid_chars=20)
        name = make_pod_name(REQUEST_ID, 0, naming=cfg)
        assert len(name) <= cfg.max_pod_name_len

    def test_two_different_request_ids_produce_different_names(self) -> None:
        # Use IDs whose first 20 hex chars differ (not just last digit)
        cfg = K8sNamingConfig(prefix="orb", uuid_chars=20)
        n1 = make_pod_name("aaaaaaaa-0000-0000-0000-000000000001", 0, naming=cfg)
        n2 = make_pod_name("bbbbbbbb-0000-0000-0000-000000000001", 0, naming=cfg)
        assert n1 != n2

    def test_req_prefix_stripped_before_uuid_segment(self) -> None:
        """A leading req- / req_ prefix is stripped so the uuid segment is pure hex."""
        cfg = K8sNamingConfig(prefix="orb", uuid_chars=8)
        # "req-550e8400..." → strip "req-" → "550e8400..." → same result as bare UUID
        name_with_prefix = make_pod_name(f"req-{REQUEST_ID}", 0, naming=cfg)
        name_bare = make_pod_name(REQUEST_ID, 0, naming=cfg)
        assert name_with_prefix == name_bare
        # req_ variant
        name_underscore = make_pod_name(f"req_{REQUEST_ID}", 0, naming=cfg)
        assert name_underscore == name_bare
        # Pure hex result, not "req5617..."
        assert "req" not in name_with_prefix


# ---------------------------------------------------------------------------
# make_deployment_name honours K8sNamingConfig
# ---------------------------------------------------------------------------


class TestMakeDeploymentName:
    def test_default_no_naming_config(self) -> None:
        """Without naming config, historical 8-char uuid pattern."""
        name = make_deployment_name(REQUEST_ID)
        assert name == "orb-550e8400"

    def test_default_naming_config(self) -> None:
        """K8sNamingConfig() default uuid_chars=20 → longer than historical 8."""
        cfg = K8sNamingConfig()
        name = make_deployment_name(REQUEST_ID, naming=cfg)
        assert name == "orb-550e8400e29b41d4a716"

    def test_custom_prefix_uuid_chars(self) -> None:
        cfg = K8sNamingConfig(prefix="myapp", uuid_chars=10)
        name = make_deployment_name(REQUEST_ID, naming=cfg)
        assert name == "myapp-550e8400e2"

    def test_dns_1123_compliance(self) -> None:
        cfg = K8sNamingConfig()
        name = make_deployment_name(REQUEST_ID, naming=cfg)
        assert _is_dns_1123(name)

    def test_max_deployment_name_len_respected(self) -> None:
        cfg = K8sNamingConfig()
        name = make_deployment_name(REQUEST_ID, naming=cfg)
        assert len(name) <= cfg.max_deployment_name_len


# ---------------------------------------------------------------------------
# make_statefulset_name honours K8sNamingConfig
# ---------------------------------------------------------------------------


class TestMakeStatefulSetName:
    def test_default_no_naming_config(self) -> None:
        name = make_statefulset_name(REQUEST_ID)
        assert name == "orb-550e8400"

    def test_default_naming_config(self) -> None:
        cfg = K8sNamingConfig()
        name = make_statefulset_name(REQUEST_ID, naming=cfg)
        assert name == "orb-550e8400e29b41d4a716"

    def test_dns_1123_compliance(self) -> None:
        cfg = K8sNamingConfig()
        name = make_statefulset_name(REQUEST_ID, naming=cfg)
        assert _is_dns_1123(name)

    def test_max_statefulset_name_len_respected(self) -> None:
        cfg = K8sNamingConfig()
        name = make_statefulset_name(REQUEST_ID, naming=cfg)
        assert len(name) <= cfg.max_statefulset_name_len


# ---------------------------------------------------------------------------
# make_job_name honours K8sNamingConfig
# ---------------------------------------------------------------------------


class TestMakeJobName:
    def test_default_no_naming_config(self) -> None:
        name = make_job_name(REQUEST_ID)
        assert name == "orb-550e8400"

    def test_default_naming_config(self) -> None:
        cfg = K8sNamingConfig()
        name = make_job_name(REQUEST_ID, naming=cfg)
        assert name == "orb-550e8400e29b41d4a716"

    def test_dns_1123_compliance(self) -> None:
        cfg = K8sNamingConfig()
        name = make_job_name(REQUEST_ID, naming=cfg)
        assert _is_dns_1123(name)

    def test_max_job_name_len_respected(self) -> None:
        cfg = K8sNamingConfig()
        name = make_job_name(REQUEST_ID, naming=cfg)
        assert len(name) <= cfg.max_job_name_len


# ---------------------------------------------------------------------------
# Backward compatibility: parse_statefulset_pod_ordinal
# ---------------------------------------------------------------------------


class TestParseStatefulSetPodOrdinalCompat:
    """parse_statefulset_pod_ordinal must work with both old and new name formats."""

    def test_old_format_8chars(self) -> None:
        """Historical: orb-550e8400-2."""
        sts_name = "orb-550e8400"
        pod_name = f"{sts_name}-2"
        assert parse_statefulset_pod_ordinal(pod_name, sts_name) == 2

    def test_new_format_20chars(self) -> None:
        """New: orb-550e8400e29b41d4a716-0."""
        sts_name = make_statefulset_name(REQUEST_ID, naming=K8sNamingConfig())
        pod_name = f"{sts_name}-0"
        assert parse_statefulset_pod_ordinal(pod_name, sts_name) == 0

    def test_custom_prefix_format(self) -> None:
        cfg = K8sNamingConfig(prefix="worker", uuid_chars=10)
        sts_name = make_statefulset_name(REQUEST_ID, naming=cfg)
        for ordinal in (0, 1, 42):
            pod_name = f"{sts_name}-{ordinal}"
            assert parse_statefulset_pod_ordinal(pod_name, sts_name) == ordinal

    def test_mismatched_sts_name_returns_none(self) -> None:
        sts_name = "orb-550e8400"
        wrong_sts = "orb-deadbeef"
        pod_name = f"{sts_name}-3"
        assert parse_statefulset_pod_ordinal(pod_name, wrong_sts) is None

    def test_non_numeric_suffix_returns_none(self) -> None:
        sts_name = "orb-550e8400"
        assert parse_statefulset_pod_ordinal(f"{sts_name}-abc", sts_name) is None


# ---------------------------------------------------------------------------
# K8sProviderConfig embeds K8sNamingConfig
# ---------------------------------------------------------------------------


class TestK8sProviderConfigNamingIntegration:
    def test_default_config_has_naming_embedded(self) -> None:
        cfg = K8sProviderConfig(namespace="test")  # type: ignore[call-arg]
        assert isinstance(cfg.naming, K8sNamingConfig)
        assert cfg.naming.prefix == "orb"
        assert cfg.naming.uuid_chars == 20

    def test_custom_naming_via_nested_config(self) -> None:
        naming = K8sNamingConfig(prefix="myapp", uuid_chars=12)
        cfg = K8sProviderConfig(namespace="test", naming=naming)  # type: ignore[call-arg]
        assert cfg.naming.prefix == "myapp"
        assert cfg.naming.uuid_chars == 12

    def test_invalid_naming_prefix_raises_at_config_load(self) -> None:
        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            K8sProviderConfig(  # type: ignore[call-arg]
                namespace="test",
                naming=K8sNamingConfig(prefix="UPPER"),
            )
