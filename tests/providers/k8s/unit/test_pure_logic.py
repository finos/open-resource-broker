"""Unit tests for pure k8s provider logic modules.

Targets (in order):
- utilities/dns_names.py            (validate_dns_1123_label, validate_dns_1123_subdomain)
- utilities/labels.py               (validate_namespace, build_label_selector, private validators)
- utilities/pod_state.py            (is_pod_ready, pod_status_string, extract_status_reason,
                                     is_fatal_waiting_reason, is_crash_loop_or_repeated_failure)
- infrastructure/handlers/shared/pod_state_translator.py
                                    (_to_iso8601, _cpu_quantity_to_vcpus, _pod_private_dns_name,
                                     instance_dict_for_pod, instance_dict_for_state)
- infrastructure/handlers/shared/label_stamper.py
                                    (stamp_native_workload_body)
- infrastructure/handlers/shared/namespace_resolver.py
                                    (resolve_namespace, resolve_namespace_from_provider_data)
- infrastructure/handlers/pod_status.py    (PodStatusResolver.compute_fulfilment)
- infrastructure/handlers/deployment_status.py (DeploymentStatusResolver.compute_fulfilment)
- infrastructure/handlers/statefulset_status.py (StatefulSetStatusResolver.compute_fulfilment)
- infrastructure/handlers/job_status.py    (JobStatusResolver.compute_fulfilment, _is_terminal)
- defaults_loader.py                (KubernetesDefaultsLoader)
- services/template_validation_service.py  (K8sTemplateValidationService)
- validation/template_validator.py  (_config_dict_to_k8s_fields, uncovered branches)
- configuration/config.py           (K8sNamingConfig, K8sProviderConfig validators)
- resilience/circuit_breaker.py     (K8sCircuitBreaker threshold_provider)
- resilience/retry_classifier.py    (K8sRetryClassifier)
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# dns_names
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateDns1123Label:
    """Tests for :func:`validate_dns_1123_label`."""

    def test_valid_label_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        validate_dns_1123_label("my-pod")  # no exception

    def test_single_char_label_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        validate_dns_1123_label("a")

    def test_all_digits_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        validate_dns_1123_label("123")

    def test_max_length_label_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        validate_dns_1123_label("a" * 63)

    def test_empty_string_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="must not be empty"):
            validate_dns_1123_label("")

    def test_too_long_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="exceeds the DNS-1123 label maximum"):
            validate_dns_1123_label("a" * 64)

    def test_uppercase_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("MyPod")

    def test_leading_hyphen_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("-my-pod")

    def test_trailing_hyphen_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("my-pod-")

    def test_custom_field_name_in_error(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="my-field"):
            validate_dns_1123_label("", field_name="my-field")

    def test_underscore_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_label

        with pytest.raises(ValueError, match="not a valid DNS-1123 label"):
            validate_dns_1123_label("my_pod")


@pytest.mark.unit
class TestValidateDns1123Subdomain:
    """Tests for :func:`validate_dns_1123_subdomain`."""

    def test_valid_subdomain_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        validate_dns_1123_subdomain("my.service.account")

    def test_single_label_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        validate_dns_1123_subdomain("myaccount")

    def test_max_253_chars_passes(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        # Build a valid 253-char subdomain: 4 segments separated by 3 dots
        # 63 + 1 + 63 + 1 + 63 + 1 + 61 = 253
        seg63 = "a" * 63
        seg61 = "a" * 61
        subdomain = f"{seg63}.{seg63}.{seg63}.{seg61}"
        assert len(subdomain) == 253
        validate_dns_1123_subdomain(subdomain)

    def test_empty_string_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        with pytest.raises(ValueError, match="must not be empty"):
            validate_dns_1123_subdomain("")

    def test_too_long_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        with pytest.raises(ValueError, match="exceeds the DNS-1123 subdomain maximum"):
            validate_dns_1123_subdomain("a" * 254)

    def test_dot_at_start_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        with pytest.raises(ValueError, match="not a valid DNS-1123 subdomain"):
            validate_dns_1123_subdomain(".my.service")

    def test_segment_with_leading_hyphen_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        with pytest.raises(ValueError, match="not a valid DNS-1123 subdomain"):
            validate_dns_1123_subdomain("my.-service.account")

    def test_uppercase_in_segment_raises(self) -> None:
        from orb.providers.k8s.utilities.dns_names import validate_dns_1123_subdomain

        with pytest.raises(ValueError, match="not a valid DNS-1123 subdomain"):
            validate_dns_1123_subdomain("My.service")


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestValidateNamespace:
    """Tests for :func:`validate_namespace` in utilities/labels.py."""

    def test_valid_namespace_passes(self) -> None:
        from orb.providers.k8s.utilities.labels import validate_namespace

        validate_namespace("my-namespace")  # no exception

    def test_empty_namespace_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, validate_namespace

        with pytest.raises(K8sValidationError, match="must not be empty"):
            validate_namespace("")

    def test_too_long_namespace_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, validate_namespace

        with pytest.raises(K8sValidationError, match="exceeds max length"):
            validate_namespace("a" * 64)

    def test_uppercase_namespace_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, validate_namespace

        with pytest.raises(K8sValidationError, match="not a valid RFC 1123 DNS label"):
            validate_namespace("MyNamespace")

    def test_namespace_with_underscore_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, validate_namespace

        with pytest.raises(K8sValidationError, match="not a valid RFC 1123 DNS label"):
            validate_namespace("my_namespace")


@pytest.mark.unit
class TestBuildLabelSelector:
    """Tests for :func:`build_label_selector`."""

    def test_valid_selector_assembled(self) -> None:
        from orb.providers.k8s.utilities.labels import build_label_selector

        result = build_label_selector("orb.io", "managed", "true")
        assert result == "orb.io/managed=true"

    def test_empty_value_allowed(self) -> None:
        from orb.providers.k8s.utilities.labels import build_label_selector

        result = build_label_selector("orb.io", "request-id", "")
        assert result == "orb.io/request-id="

    def test_invalid_prefix_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("", "key", "val")

    def test_invalid_key_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("orb.io", "", "val")

    def test_injection_char_in_value_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("orb.io", "key", "val=injection")

    def test_prefix_too_long_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("a" * 254, "key", "val")

    def test_key_too_long_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("orb.io", "k" * 64, "val")

    def test_value_too_long_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("orb.io", "key", "v" * 64)

    def test_prefix_with_invalid_chars_raises(self) -> None:
        from orb.providers.k8s.utilities.labels import K8sValidationError, build_label_selector

        with pytest.raises(K8sValidationError):
            build_label_selector("orb=io", "key", "val")


# ---------------------------------------------------------------------------
# pod_state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsPodReady:
    """Tests for :func:`is_pod_ready`."""

    def _make_condition(self, type_: str, status: str) -> Any:
        return SimpleNamespace(type=type_, status=status)

    def test_ready_true_condition_returns_true(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        conditions = [self._make_condition("Ready", "True")]
        assert is_pod_ready(conditions) is True

    def test_ready_false_condition_returns_false(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        conditions = [self._make_condition("Ready", "False")]
        assert is_pod_ready(conditions) is False

    def test_empty_conditions_returns_false(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        assert is_pod_ready([]) is False

    def test_non_ready_condition_returns_false(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        conditions = [self._make_condition("PodScheduled", "True")]
        assert is_pod_ready(conditions) is False

    def test_multiple_conditions_finds_ready(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        conditions = [
            self._make_condition("PodScheduled", "True"),
            self._make_condition("Ready", "True"),
        ]
        assert is_pod_ready(conditions) is True

    def test_condition_with_none_attrs_skipped(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_pod_ready

        # Objects with no type/status attributes
        assert is_pod_ready([SimpleNamespace()]) is False


@pytest.mark.unit
class TestPodStatusString:
    """Tests for :func:`pod_status_string`."""

    def test_running_and_ready_returns_running(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Running", True) == "running"

    def test_running_but_not_ready_returns_starting(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Running", False) == "starting"

    def test_pending_phase_returns_pending(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Pending", False) == "pending"

    def test_failed_phase_returns_failed(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Failed", False) == "failed"

    def test_unknown_phase_returns_pending(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Unknown", False) == "pending"

    def test_none_phase_returns_pending(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string(None, False) == "pending"

    def test_succeeded_bare_pod_returns_terminated(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Succeeded", False, provider_api="Pod") == "terminated"

    def test_succeeded_job_returns_terminated(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Succeeded", False, provider_api="Job") == "terminated"

    def test_succeeded_deployment_returns_running(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Succeeded", False, provider_api="Deployment") == "running"

    def test_succeeded_statefulset_returns_running(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Succeeded", False, provider_api="StatefulSet") == "running"

    def test_succeeded_no_provider_api_returns_terminated(self) -> None:
        from orb.providers.k8s.utilities.pod_state import pod_status_string

        assert pod_status_string("Succeeded", False) == "terminated"


@pytest.mark.unit
class TestExtractStatusReason:
    """Tests for :func:`extract_status_reason`."""

    def _make_terminated(self, reason: str) -> Any:
        terminated = SimpleNamespace(reason=reason)
        state = SimpleNamespace(terminated=terminated, waiting=None)
        return SimpleNamespace(state=state)

    def _make_waiting(self, reason: str) -> Any:
        waiting = SimpleNamespace(reason=reason)
        state = SimpleNamespace(terminated=None, waiting=waiting)
        return SimpleNamespace(state=state)

    def _make_condition(self, type_: str, status: str, reason: str) -> Any:
        return SimpleNamespace(type=type_, status=status, reason=reason)

    def test_terminated_reason_returned(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cs = self._make_terminated("OOMKilled")
        result = extract_status_reason([cs], [])
        assert result == "OOMKilled"

    def test_waiting_reason_returned_when_no_terminated(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cs = self._make_waiting("CrashLoopBackOff")
        result = extract_status_reason([cs], [])
        assert result == "CrashLoopBackOff"

    def test_pod_scheduled_false_returns_reason(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cond = self._make_condition("PodScheduled", "False", "Unschedulable")
        result = extract_status_reason([], [cond])
        assert result == "Unschedulable"

    def test_no_reason_returns_none(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        result = extract_status_reason([], [])
        assert result is None

    def test_init_container_fatal_waiting_reason_returned(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        init_cs = self._make_waiting("ImagePullBackOff")
        result = extract_status_reason([], [], init_container_statuses=[init_cs])
        assert result == "ImagePullBackOff"

    def test_init_container_non_fatal_waiting_reason_not_returned(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        # "ContainerCreating" is not in FATAL_WAITING_REASONS
        init_cs = self._make_waiting("ContainerCreating")
        result = extract_status_reason([], [], init_container_statuses=[init_cs])
        assert result is None

    def test_container_status_with_no_state_skipped(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cs = SimpleNamespace(state=None)
        result = extract_status_reason([cs], [])
        assert result is None

    def test_terminated_reason_empty_string_falls_through(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cs = SimpleNamespace(
            state=SimpleNamespace(terminated=SimpleNamespace(reason=""), waiting=None)
        )
        # Empty reason is falsy — should fall through to next check
        result = extract_status_reason([cs], [])
        assert result is None

    def test_pod_scheduled_true_condition_not_returned(self) -> None:
        from orb.providers.k8s.utilities.pod_state import extract_status_reason

        cond = self._make_condition("PodScheduled", "True", "SomeReason")
        result = extract_status_reason([], [cond])
        assert result is None


@pytest.mark.unit
class TestIsFatalWaitingReason:
    """Tests for :func:`is_fatal_waiting_reason`."""

    def test_crash_loop_back_off_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("CrashLoopBackOff") is True

    def test_image_pull_back_off_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("ImagePullBackOff") is True

    def test_err_image_pull_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("ErrImagePull") is True

    def test_invalid_image_name_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("InvalidImageName") is True

    def test_container_creating_is_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("ContainerCreating") is False

    def test_none_is_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason(None) is False

    def test_empty_string_is_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_fatal_waiting_reason

        assert is_fatal_waiting_reason("") is False


@pytest.mark.unit
class TestIsCrashLoopOrRepeatedFailure:
    """Tests for :func:`is_crash_loop_or_repeated_failure`."""

    def _make_cs(
        self,
        *,
        restart_count: int = 0,
        waiting_reason: str | None = None,
        last_exit_code: int | None = None,
    ) -> Any:
        waiting = SimpleNamespace(reason=waiting_reason) if waiting_reason else None
        current_state = SimpleNamespace(waiting=waiting, terminated=None)
        if last_exit_code is not None:
            last_terminated = SimpleNamespace(exit_code=last_exit_code)
            last_state = SimpleNamespace(terminated=last_terminated)
        else:
            last_state = SimpleNamespace(terminated=None)
        return SimpleNamespace(
            restart_count=restart_count,
            state=current_state,
            last_state=last_state,
        )

    def test_crash_loop_back_off_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(waiting_reason="CrashLoopBackOff")
        assert is_crash_loop_or_repeated_failure([cs]) is True

    def test_high_restart_count_with_nonzero_exit_code_is_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(restart_count=2, last_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs]) is True

    def test_low_restart_count_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(restart_count=1, last_exit_code=1)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_high_restart_count_zero_exit_code_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(restart_count=3, last_exit_code=0)
        assert is_crash_loop_or_repeated_failure([cs]) is False

    def test_on_failure_policy_skips_restart_count_heuristic(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(restart_count=5, last_exit_code=1)
        # With OnFailure, restart heuristic is skipped — not fatal
        assert is_crash_loop_or_repeated_failure([cs], restart_policy="OnFailure") is False

    def test_on_failure_policy_still_catches_crash_loop_back_off(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(waiting_reason="CrashLoopBackOff")
        # CrashLoopBackOff is always fatal even with OnFailure
        assert is_crash_loop_or_repeated_failure([cs], restart_policy="OnFailure") is True

    def test_empty_container_statuses_returns_false(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        assert is_crash_loop_or_repeated_failure([]) is False

    def test_custom_restart_threshold(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = self._make_cs(restart_count=5, last_exit_code=2)
        # With default threshold=2, this is fatal
        assert is_crash_loop_or_repeated_failure([cs], restart_threshold=2) is True
        # With threshold=10, not yet fatal
        assert is_crash_loop_or_repeated_failure([cs], restart_threshold=10) is False

    def test_cs_with_no_state_not_fatal(self) -> None:
        from orb.providers.k8s.utilities.pod_state import is_crash_loop_or_repeated_failure

        cs = SimpleNamespace(restart_count=5, state=None, last_state=None)
        assert is_crash_loop_or_repeated_failure([cs]) is False


# ---------------------------------------------------------------------------
# pod_state_translator
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestToIso8601:
    """Tests for :func:`_to_iso8601`."""

    def test_none_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _to_iso8601,
        )

        assert _to_iso8601(None) is None

    def test_naive_datetime_gets_utc(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _to_iso8601,
        )

        naive = datetime(2026, 1, 15, 10, 30, 0)
        result = _to_iso8601(naive)
        assert result is not None
        assert "T" in result
        assert "+00:00" in result

    def test_aware_datetime_preserved(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _to_iso8601,
        )

        aware = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = _to_iso8601(aware)
        assert result == "2026-01-15T10:30:00+00:00"

    def test_string_returned_unchanged(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _to_iso8601,
        )

        s = "2026-01-15T10:30:00+00:00"
        assert _to_iso8601(s) == s


@pytest.mark.unit
class TestCpuQuantityToVcpus:
    """Tests for :func:`_cpu_quantity_to_vcpus`."""

    def test_none_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        assert _cpu_quantity_to_vcpus(None) is None

    def test_plain_integer_string(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        assert _cpu_quantity_to_vcpus("32") == 32

    def test_millicpu_rounds_up(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        # 1500m = 1.5 vCPUs → rounds up to 2
        assert _cpu_quantity_to_vcpus("1500m") == 2

    def test_millicpu_exact(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        # 2000m = exactly 2 vCPUs
        assert _cpu_quantity_to_vcpus("2000m") == 2

    def test_millicpu_zero_returns_zero(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        assert _cpu_quantity_to_vcpus("0m") == 0

    def test_unparseable_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        assert _cpu_quantity_to_vcpus("abc") is None

    def test_empty_string_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        assert _cpu_quantity_to_vcpus("") is None

    def test_small_millicpu_rounds_up_to_one(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _cpu_quantity_to_vcpus,
        )

        # 100m = 0.1 vCPU → rounds up to 1 (max(1, ceil))
        assert _cpu_quantity_to_vcpus("100m") == 1


@pytest.mark.unit
class TestPodPrivateDnsName:
    """Tests for :func:`_pod_private_dns_name`."""

    def test_returns_dashed_ip_form(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _pod_private_dns_name,
        )

        result = _pod_private_dns_name("10.0.0.5", "default")
        assert result == "10-0-0-5.default.pod.cluster.local"

    def test_none_pod_ip_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _pod_private_dns_name,
        )

        assert _pod_private_dns_name(None, "default") is None

    def test_empty_pod_ip_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _pod_private_dns_name,
        )

        assert _pod_private_dns_name("", "default") is None

    def test_empty_namespace_returns_none(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            _pod_private_dns_name,
        )

        assert _pod_private_dns_name("10.0.0.1", "") is None


def _make_pod(
    name: str = "orb-pod-0000",
    phase: str = "Running",
    ready: bool = True,
    pod_ip: str | None = "10.0.0.1",
    node_name: str | None = "node1",
    labels: dict | None = None,
    image: str | None = "myimage:latest",
    restart_count: int = 0,
) -> Any:
    """Build a minimal pod-like SimpleNamespace."""
    metadata = SimpleNamespace(name=name, labels=labels or {})
    container = SimpleNamespace(image=image)
    spec = SimpleNamespace(
        containers=[container] if image else [],
        node_name=node_name,
        restart_policy="Always",
    )
    status_condition = SimpleNamespace(type="Ready", status="True" if ready else "False")
    container_status = SimpleNamespace(
        state=SimpleNamespace(terminated=None, waiting=None),
        restart_count=restart_count,
        last_state=SimpleNamespace(terminated=None),
    )
    status = SimpleNamespace(
        phase=phase,
        pod_ip=pod_ip,
        host_ip="192.168.1.1",
        start_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        conditions=[status_condition],
        container_statuses=[container_status],
        init_container_statuses=[],
    )
    return SimpleNamespace(metadata=metadata, spec=spec, status=status)


@pytest.mark.unit
class TestInstanceDictForPod:
    """Tests for :func:`instance_dict_for_pod`."""

    def test_running_pod_has_correct_fields(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(name="my-pod", phase="Running", ready=True, pod_ip="10.0.0.5")
        result = instance_dict_for_pod(pod, "default", provider_api="Pod")

        assert result["instance_id"] == "my-pod"
        assert result["status"] == "running"
        assert result["private_ip"] == "10.0.0.5"
        assert result["public_ip"] is None
        assert result["instance_type"] == "k8s/Pod"
        assert result["image_id"] == "myimage:latest"
        assert result["provider_data"]["namespace"] == "default"
        assert result["private_dns_name"] == "10-0-0-5.default.pod.cluster.local"

    def test_pending_pod_status(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(phase="Pending", ready=False, pod_ip=None)
        result = instance_dict_for_pod(pod, "test-ns", provider_api="Pod")

        assert result["status"] == "pending"
        assert result["private_ip"] is None
        assert result["private_dns_name"] is None

    def test_failed_pod_status(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(phase="Failed", ready=False)
        result = instance_dict_for_pod(pod, "default", provider_api="Pod")

        assert result["status"] == "failed"

    def test_succeeded_pod_returns_terminated(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(phase="Succeeded", ready=False)
        result = instance_dict_for_pod(pod, "default", provider_api="Pod")

        assert result["status"] == "terminated"

    def test_node_state_cache_enriches_instance_type(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        node_state = SimpleNamespace(
            instance_type="m5.large",
            capacity_type="on-demand",
            region="us-east-1",
            zone="us-east-1a",
            cpu_capacity="4",
        )
        cache = MagicMock()
        cache.get.return_value = node_state
        pod = _make_pod(node_name="node1")
        result = instance_dict_for_pod(pod, "default", provider_api="Pod", node_state_cache=cache)

        assert result["instance_type"] == "m5.large"
        assert result["price_type"] == "on-demand"
        assert result["provider_data"]["vcpus"] == 4

    def test_node_state_cache_miss_uses_provider_api_type(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        cache = MagicMock()
        cache.get.return_value = None
        pod = _make_pod(node_name="node1")
        result = instance_dict_for_pod(
            pod, "default", provider_api="Deployment", node_state_cache=cache
        )

        assert result["instance_type"] == "k8s/Deployment"

    def test_fatal_waiting_reason_escalates_to_failed(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        waiting = SimpleNamespace(reason="ImagePullBackOff")
        state = SimpleNamespace(terminated=None, waiting=waiting)
        cs = SimpleNamespace(
            state=state, restart_count=0, last_state=SimpleNamespace(terminated=None)
        )
        metadata = SimpleNamespace(name="pod1", labels={})
        spec = SimpleNamespace(containers=[], node_name=None, restart_policy="Always")
        status = SimpleNamespace(
            phase="Pending",
            pod_ip=None,
            host_ip=None,
            start_time=None,
            conditions=[],
            container_statuses=[cs],
            init_container_statuses=[],
        )
        pod = SimpleNamespace(metadata=metadata, spec=spec, status=status)

        result = instance_dict_for_pod(pod, "default", provider_api="Pod")
        assert result["status"] == "failed"
        assert result["status_reason"] == "ImagePullBackOff"

    def test_succeeded_deployment_pod_logs_warning(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(phase="Succeeded", ready=False)
        logger = MagicMock()
        result = instance_dict_for_pod(pod, "default", provider_api="Deployment", logger=logger)

        # Deployment's Succeeded pod is treated as running
        assert result["status"] == "running"
        logger.warning.assert_called_once()

    def test_pod_with_no_spec_containers_has_no_image_id(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        pod = _make_pod(image=None)
        result = instance_dict_for_pod(pod, "default", provider_api="Pod")
        assert result["image_id"] is None

    def test_crash_looping_pod_escalated_to_failed(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        # CrashLoopBackOff while briefly Running
        waiting = SimpleNamespace(reason="CrashLoopBackOff")
        state = SimpleNamespace(terminated=None, waiting=waiting)
        cs = SimpleNamespace(
            state=state, restart_count=3, last_state=SimpleNamespace(terminated=None)
        )
        metadata = SimpleNamespace(name="pod1", labels={})
        spec = SimpleNamespace(containers=[], node_name=None, restart_policy="Always")
        status_cond = SimpleNamespace(type="Ready", status="True")
        status = SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.1",
            host_ip=None,
            start_time=None,
            conditions=[status_cond],
            container_statuses=[cs],
            init_container_statuses=[],
        )
        pod = SimpleNamespace(metadata=metadata, spec=spec, status=status)

        result = instance_dict_for_pod(pod, "default", provider_api="Pod")
        assert result["status"] == "failed"
        assert result["status_reason"] == "CrashLoopBackOff"

    def test_disruption_target_condition_captured(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_pod,
        )

        ready_cond = SimpleNamespace(type="Ready", status="True", reason=None, message=None)
        disruption_cond = SimpleNamespace(
            type="DisruptionTarget", status="True", reason="Underutilized", message="Spot eviction"
        )
        metadata = SimpleNamespace(name="pod1", labels={})
        container = SimpleNamespace(image="img:latest")
        spec = SimpleNamespace(containers=[container], node_name=None, restart_policy="Always")
        status = SimpleNamespace(
            phase="Running",
            pod_ip="10.0.0.1",
            host_ip=None,
            start_time=None,
            conditions=[ready_cond, disruption_cond],
            container_statuses=[],
            init_container_statuses=[],
        )
        pod = SimpleNamespace(metadata=metadata, spec=spec, status=status)

        result = instance_dict_for_pod(pod, "default", provider_api="Pod")
        assert result["provider_data"]["disrupted_reason"] == "Underutilized"
        assert result["provider_data"]["disrupted_message"] == "Spot eviction"


@pytest.mark.unit
class TestInstanceDictForState:
    """Tests for :func:`instance_dict_for_state`."""

    def _make_state(
        self,
        pod_name: str = "my-pod",
        namespace: str = "default",
        status: str = "running",
        pod_ip: str | None = "10.0.0.1",
        node_name: str | None = "node1",
        start_time: str | None = "2026-01-01T00:00:00+00:00",
        labels: dict | None = None,
        host_ip: str | None = None,
        restart_count: int = 0,
        disrupted_reason: str | None = None,
        disrupted_message: str | None = None,
        status_reason: str | None = None,
        phase: str | None = "Running",
        ready: bool = True,
        image_id: str | None = "myimage:latest",
    ) -> Any:
        return SimpleNamespace(
            pod_name=pod_name,
            namespace=namespace,
            status=status,
            pod_ip=pod_ip,
            node_name=node_name,
            start_time=start_time,
            labels=labels or {},
            host_ip=host_ip,
            restart_count=restart_count,
            disrupted_reason=disrupted_reason,
            disrupted_message=disrupted_message,
            status_reason=status_reason,
            phase=phase,
            ready=ready,
            image_id=image_id,
        )

    def test_basic_running_state(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_state,
        )

        state = self._make_state()
        result = instance_dict_for_state(state, provider_api="Pod")

        assert result["instance_id"] == "my-pod"
        assert result["status"] == "running"
        assert result["private_ip"] == "10.0.0.1"
        assert result["instance_type"] == "k8s/Pod"
        assert result["image_id"] == "myimage:latest"
        assert result["private_dns_name"] == "10-0-0-1.default.pod.cluster.local"

    def test_node_cache_enrichment(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_state,
        )

        node_state = SimpleNamespace(
            instance_type="t3.micro",
            capacity_type="spot",
            region="us-west-2",
            zone="us-west-2a",
            cpu_capacity="2",
        )
        cache = MagicMock()
        cache.get.return_value = node_state
        state = self._make_state(node_name="mynode")
        result = instance_dict_for_state(state, provider_api="Pod", node_state_cache=cache)

        assert result["instance_type"] == "t3.micro"
        assert result["provider_data"]["availability_zone"] == "us-west-2a"

    def test_state_with_no_image_id(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_state,
        )

        state = self._make_state(image_id=None)
        result = instance_dict_for_state(state, provider_api="Pod")
        assert result["image_id"] is None

    def test_labels_copied_as_tags(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.pod_state_translator import (
            instance_dict_for_state,
        )

        state = self._make_state(labels={"env": "prod"})
        result = instance_dict_for_state(state, provider_api="Pod")
        assert result["tags"] == {"env": "prod"}


# ---------------------------------------------------------------------------
# label_stamper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStampNativeWorkloadBody:
    """Tests for :func:`stamp_native_workload_body`."""

    def _make_request(self, request_id: str = "req-123", template_id: str = "tmpl-456") -> Any:
        return SimpleNamespace(request_id=request_id, template_id=template_id)

    def test_basic_deployment_body_stamped(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
            stamp_native_workload_body,
        )

        native = {"apiVersion": "apps/v1", "kind": "Deployment", "spec": {}}
        request = self._make_request()

        result = stamp_native_workload_body(
            native,
            workload_name="my-deploy",
            namespace="prod",
            replicas=3,
            request=request,
            label_prefix="orb.io",
        )

        assert result["metadata"]["name"] == "my-deploy"
        assert result["metadata"]["namespace"] == "prod"
        assert result["metadata"]["labels"]["orb.io/managed"] == "true"
        assert result["metadata"]["labels"]["orb.io/request-id"] == "req-123"
        assert result["metadata"]["labels"]["orb.io/template-id"] == "tmpl-456"
        assert result["spec"]["replicas"] == 3
        # Pod template must carry request-id label
        assert result["spec"]["template"]["metadata"]["labels"]["orb.io/request-id"] == "req-123"
        # Selector must carry request-id so controller can find pods
        assert result["spec"]["selector"]["matchLabels"]["orb.io/request-id"] == "req-123"

    def test_original_body_not_mutated(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
            stamp_native_workload_body,
        )

        native = {"spec": {"replicas": 1}}
        request = self._make_request()
        stamp_native_workload_body(
            native,
            workload_name="d",
            namespace="ns",
            replicas=5,
            request=request,
            label_prefix="orb.io",
        )
        # Original unchanged
        assert native["spec"]["replicas"] == 1

    def test_job_body_uses_parallelism_completions(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
            stamp_native_workload_body,
        )

        native = {"spec": {"parallelism": 1, "completions": 1, "template": {}}}
        request = self._make_request()

        result = stamp_native_workload_body(
            native,
            workload_name="my-job",
            namespace="jobs",
            replicas=5,
            request=request,
            label_prefix="orb.io",
        )

        assert result["spec"]["parallelism"] == 5
        assert result["spec"]["completions"] == 5
        # Job should NOT have spec.selector set by ORB
        assert "selector" not in result["spec"]

    def test_existing_labels_preserved(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
            stamp_native_workload_body,
        )

        native = {"metadata": {"labels": {"app": "myapp"}}}
        request = self._make_request()

        result = stamp_native_workload_body(
            native,
            workload_name="d",
            namespace="ns",
            replicas=1,
            request=request,
            label_prefix="orb.io",
        )

        assert result["metadata"]["labels"]["app"] == "myapp"
        assert result["metadata"]["labels"]["orb.io/managed"] == "true"


# ---------------------------------------------------------------------------
# namespace_resolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveNamespaceFromProviderData:
    """Tests for :func:`resolve_namespace_from_provider_data`."""

    def _make_config(self, namespace: str = "default") -> Any:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        return K8sProviderConfig(namespace=namespace)  # type: ignore[call-arg]

    def test_provider_data_namespace_returned(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
            resolve_namespace_from_provider_data,
        )

        config = self._make_config(namespace="default")
        result = resolve_namespace_from_provider_data({"namespace": "custom-ns"}, config)
        assert result == "custom-ns"

    def test_missing_key_falls_back_to_config(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
            resolve_namespace_from_provider_data,
        )

        config = self._make_config(namespace="my-default")
        result = resolve_namespace_from_provider_data({}, config)
        assert result == "my-default"

    def test_empty_string_namespace_falls_back_to_config(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
            resolve_namespace_from_provider_data,
        )

        config = self._make_config(namespace="fallback-ns")
        result = resolve_namespace_from_provider_data({"namespace": ""}, config)
        assert result == "fallback-ns"

    def test_none_namespace_falls_back_to_config(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.shared.namespace_resolver import (
            resolve_namespace_from_provider_data,
        )

        config = self._make_config(namespace="fallback-ns")
        result = resolve_namespace_from_provider_data({"namespace": None}, config)
        assert result == "fallback-ns"


# ---------------------------------------------------------------------------
# Status resolvers — compute_fulfilment (pure logic, no handler needed)
# ---------------------------------------------------------------------------


def _make_instance(status: str) -> dict[str, Any]:
    return {"status": status}


@pytest.mark.unit
class TestPodStatusResolverComputeFulfilment:
    """Tests for :meth:`PodStatusResolver.compute_fulfilment` in isolation."""

    def _resolver(self) -> Any:
        from orb.providers.k8s.infrastructure.handlers.pod_status import PodStatusResolver

        # Create a resolver with a minimal handler stub
        handler = MagicMock()
        return PodStatusResolver(handler)

    def test_all_running_fulfils(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 3
        result = r.compute_fulfilment(instances, 3)
        assert result.state == "fulfilled"
        assert result.running_count == 3

    def test_all_terminated_fulfils(self) -> None:
        r = self._resolver()
        instances = [_make_instance("terminated")] * 2
        result = r.compute_fulfilment(instances, 2)
        assert result.state == "fulfilled"
        assert "completed" in result.message.lower()

    def test_mixed_running_and_terminated_fulfils(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running"), _make_instance("terminated")]
        result = r.compute_fulfilment(instances, 2)
        assert result.state == "fulfilled"

    def test_pending_pods_in_progress(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running"), _make_instance("pending")]
        result = r.compute_fulfilment(instances, 2)
        assert result.state == "in_progress"

    def test_all_failed_returns_failed(self) -> None:
        r = self._resolver()
        instances = [_make_instance("failed"), _make_instance("failed")]
        result = r.compute_fulfilment(instances, 2)
        assert result.state == "failed"

    def test_partial_running_returns_partial(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")]
        result = r.compute_fulfilment(instances, 3)
        assert result.state == "partial"

    def test_empty_instances_returns_in_progress(self) -> None:
        r = self._resolver()
        result = r.compute_fulfilment([], 2)
        assert result.state == "in_progress"

    def test_failed_with_some_running_not_failed(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running"), _make_instance("failed")]
        result = r.compute_fulfilment(instances, 2)
        # Not all failed — should be partial or in_progress
        assert result.state != "failed"


@pytest.mark.unit
class TestDeploymentStatusResolverComputeFulfilment:
    """Tests for :meth:`DeploymentStatusResolver.compute_fulfilment` in isolation."""

    def _resolver(self) -> Any:
        from orb.providers.k8s.infrastructure.handlers.deployment_status import (
            DeploymentStatusResolver,
        )

        handler = MagicMock()
        handler.config.controller_status_cache_ttl_seconds = 5.0
        return DeploymentStatusResolver(handler)

    def test_all_ready_with_controller_view_fulfils(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 3
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 3})
        assert result.state == "fulfilled"
        assert result.running_count == 3

    def test_controller_view_overrides_pod_count(self) -> None:
        r = self._resolver()
        # Pod list shows 2 running but controller says 3 ready
        instances = [_make_instance("running")] * 2
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 3})
        assert result.state == "fulfilled"
        assert result.running_count == 3

    def test_pending_pods_in_progress(self) -> None:
        r = self._resolver()
        instances = [_make_instance("pending")]
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 0})
        assert result.state == "in_progress"

    def test_all_failed_returns_failed(self) -> None:
        r = self._resolver()
        instances = [_make_instance("failed")] * 3
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 0})
        assert result.state == "failed"

    def test_no_controller_view_falls_back_to_pods(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 2
        result = r.compute_fulfilment(instances, 2, controller_view={})
        assert result.state == "fulfilled"

    def test_partial_cache_all_failed_no_pending_is_failed(self) -> None:
        r = self._resolver()
        # Fewer pods than requested — all failed, none pending, none ready
        instances = [_make_instance("failed")]
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 0})
        assert result.state == "failed"

    def test_partial_ready_returns_partial(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 1
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 1})
        assert result.state == "partial"

    def test_starting_returns_in_progress(self) -> None:
        r = self._resolver()
        result = r.compute_fulfilment([], 2, controller_view={"ready_replicas": 0})
        assert result.state == "in_progress"
        assert "starting" in result.message.lower()


@pytest.mark.unit
class TestStatefulSetStatusResolverComputeFulfilment:
    """Tests for :meth:`StatefulSetStatusResolver.compute_fulfilment` in isolation."""

    def _resolver(self) -> Any:
        from orb.providers.k8s.infrastructure.handlers.statefulset_status import (
            StatefulSetStatusResolver,
        )

        handler = MagicMock()
        handler.config.controller_status_cache_ttl_seconds = 5.0
        return StatefulSetStatusResolver(handler)

    def test_all_ready_fulfils(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 3
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 3})
        assert result.state == "fulfilled"
        assert "StatefulSet ready" in result.message

    def test_partial_cache_all_failed_no_pending_is_failed(self) -> None:
        r = self._resolver()
        instances = [_make_instance("failed")]
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 0})
        assert result.state == "failed"

    def test_pending_pods_in_progress(self) -> None:
        r = self._resolver()
        instances = [_make_instance("starting")]
        result = r.compute_fulfilment(instances, 3, controller_view={"ready_replicas": 0})
        assert result.state == "in_progress"

    def test_partial_ready_returns_partial(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")] * 2
        result = r.compute_fulfilment(instances, 5, controller_view={"ready_replicas": 2})
        assert result.state == "partial"

    def test_no_instances_in_progress(self) -> None:
        r = self._resolver()
        result = r.compute_fulfilment([], 3, controller_view={"ready_replicas": 0})
        assert result.state == "in_progress"


@pytest.mark.unit
class TestJobStatusResolverComputeFulfilment:
    """Tests for :meth:`JobStatusResolver.compute_fulfilment` and :meth:`_is_terminal`."""

    def _resolver(self) -> Any:
        from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver

        handler = MagicMock()
        handler.config.controller_status_cache_ttl_seconds = 5.0
        return JobStatusResolver(handler)

    def test_complete_condition_fulfils(self) -> None:
        r = self._resolver()
        view = {"conditions": [{"type": "Complete", "status": "True"}], "succeeded": 3}
        result = r.compute_fulfilment([], 3, controller_view=view)
        assert result.state == "fulfilled"
        assert "complete" in result.message.lower()

    def test_failed_condition_fails(self) -> None:
        r = self._resolver()
        view = {
            "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
            "succeeded": 0,
        }
        result = r.compute_fulfilment([], 3, controller_view=view)
        assert result.state == "failed"
        assert "BackoffLimitExceeded" in result.message

    def test_active_pods_in_progress(self) -> None:
        r = self._resolver()
        view = {"active": 2, "succeeded": 1, "failed": 0, "conditions": []}
        result = r.compute_fulfilment([], 3, controller_view=view)
        assert result.state == "in_progress"

    def test_all_succeeded_fulfils(self) -> None:
        r = self._resolver()
        view = {"active": 0, "succeeded": 3, "failed": 0, "conditions": []}
        result = r.compute_fulfilment([], 3, controller_view=view)
        assert result.state == "fulfilled"

    def test_all_failed_no_running_fails(self) -> None:
        r = self._resolver()
        view = {"active": 0, "succeeded": 0, "failed": 2, "conditions": []}
        result = r.compute_fulfilment([], 2, controller_view=view)
        assert result.state == "failed"

    def test_partial_succeeded_returns_partial(self) -> None:
        r = self._resolver()
        view = {"active": 0, "succeeded": 1, "failed": 0, "conditions": []}
        result = r.compute_fulfilment([], 3, controller_view=view)
        assert result.state == "partial"

    def test_no_controller_view_uses_pod_status(self) -> None:
        r = self._resolver()
        instances = [_make_instance("running")]
        result = r.compute_fulfilment(instances, 1, controller_view={})
        assert result.state == "fulfilled"

    def test_is_terminal_complete(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver

        assert JobStatusResolver._is_terminal(
            {"conditions": [{"type": "Complete", "status": "True"}]}
        )

    def test_is_terminal_failed(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver

        assert JobStatusResolver._is_terminal(
            {"conditions": [{"type": "Failed", "status": "True"}]}
        )

    def test_is_not_terminal_when_no_conditions(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver

        assert not JobStatusResolver._is_terminal({"conditions": []})

    def test_is_not_terminal_when_condition_status_false(self) -> None:
        from orb.providers.k8s.infrastructure.handlers.job_status import JobStatusResolver

        assert not JobStatusResolver._is_terminal(
            {"conditions": [{"type": "Complete", "status": "False"}]}
        )

    def test_starting_when_no_pods_no_controller_view(self) -> None:
        r = self._resolver()
        result = r.compute_fulfilment([], 3, controller_view={})
        assert result.state == "in_progress"


# ---------------------------------------------------------------------------
# defaults_loader
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKubernetesDefaultsLoader:
    """Tests for :class:`KubernetesDefaultsLoader`."""

    def test_load_defaults_returns_dict(self) -> None:
        from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader

        loader = KubernetesDefaultsLoader()
        result = loader.load_defaults()
        assert isinstance(result, dict)

    def test_load_defaults_returns_empty_dict_on_missing_file(self) -> None:
        from orb.providers.k8s.defaults_loader import KubernetesDefaultsLoader

        loader = KubernetesDefaultsLoader()
        # Patch importlib.resources.files to raise so we test the fallback path
        with patch("importlib.resources.files", side_effect=Exception("resource not found")):
            result = loader.load_defaults()
        assert result == {}


# ---------------------------------------------------------------------------
# template_validation_service
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestK8sTemplateValidationService:
    """Tests for :class:`K8sTemplateValidationService`."""

    def _make_service(self) -> Any:
        from orb.providers.k8s.services.template_validation_service import (
            K8sTemplateValidationService,
        )

        return K8sTemplateValidationService(logger=MagicMock())

    def _make_operation(self, template_config: dict) -> Any:
        return SimpleNamespace(parameters={"template_config": template_config})

    def test_valid_pod_template_succeeds(self) -> None:
        svc = self._make_service()
        op = self._make_operation({"image_id": "nginx:latest", "provider_api": "Pod"})
        result = svc.validate_template(op)
        assert result.success is True

    def test_missing_template_config_errors(self) -> None:
        svc = self._make_service()
        op = SimpleNamespace(parameters={})
        result = svc.validate_template(op)
        assert result.success is False
        assert "MISSING_TEMPLATE_CONFIG" in str(result.error_code)

    def test_invalid_provider_api_errors(self) -> None:
        svc = self._make_service()
        op = self._make_operation({"image_id": "nginx", "provider_api": "DaemonSet"})
        result = svc.validate_template(op)
        assert result.success is True  # returns success with validation result
        data = result.data
        assert not data["valid"]
        assert any("provider_api" in e for e in data["errors"])

    def test_invalid_restart_policy_errors(self) -> None:
        svc = self._make_service()
        op = self._make_operation({"image_id": "nginx", "restart_policy": "BadPolicy"})
        result = svc.validate_template(op)
        data = result.data
        assert not data["valid"]

    def test_always_restart_policy_for_job_errors(self) -> None:
        svc = self._make_service()
        op = self._make_operation(
            {"image_id": "nginx", "provider_api": "Job", "restart_policy": "Always"}
        )
        result = svc.validate_template(op)
        data = result.data
        assert not data["valid"]
        assert any("restart_policy" in e for e in data["errors"])

    def test_nonfailure_restart_policy_for_deployment_warns(self) -> None:
        svc = self._make_service()
        op = self._make_operation(
            {"image_id": "nginx", "provider_api": "Deployment", "restart_policy": "Never"}
        )
        result = svc.validate_template(op)
        data = result.data
        assert data["valid"]  # warning, not error
        assert any("restart_policy" in w for w in data["warnings"])

    def test_negative_max_instances_errors(self) -> None:
        svc = self._make_service()
        op = self._make_operation({"image_id": "nginx", "max_instances": -1})
        result = svc.validate_template(op)
        data = result.data
        assert not data["valid"]

    def test_missing_image_errors(self) -> None:
        svc = self._make_service()
        op = self._make_operation({"provider_api": "Pod"})
        result = svc.validate_template(op)
        data = result.data
        assert not data["valid"]
        assert any("image_id" in e for e in data["errors"])

    def test_exception_returns_error_result(self) -> None:
        svc = self._make_service()
        op = SimpleNamespace(parameters={"template_config": None})  # will trigger missing config
        result = svc.validate_template(op)
        assert result.success is False


# ---------------------------------------------------------------------------
# validation/template_validator.py — uncovered branches
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigDictToK8sFields:
    """Tests for :func:`_config_dict_to_k8s_fields` (private helper)."""

    def test_camelcase_keys_normalised(self) -> None:
        from orb.providers.k8s.validation.template_validator import _config_dict_to_k8s_fields

        config = {"templateId": "t1", "imageId": "nginx", "maxInstances": 5}
        result = _config_dict_to_k8s_fields(config)
        assert result.get("template_id") == "t1"
        assert result.get("image_id") == "nginx"
        assert result.get("max_instances") == 5

    def test_nonpositive_max_instances_dropped(self) -> None:
        from orb.providers.k8s.validation.template_validator import _config_dict_to_k8s_fields

        config = {"image_id": "nginx", "max_instances": 0}
        result = _config_dict_to_k8s_fields(config)
        assert "max_instances" not in result

    def test_nonnumeric_max_instances_dropped(self) -> None:
        from orb.providers.k8s.validation.template_validator import _config_dict_to_k8s_fields

        config = {"image_id": "nginx", "max_instances": "abc"}
        result = _config_dict_to_k8s_fields(config)
        assert "max_instances" not in result

    def test_none_values_excluded(self) -> None:
        from orb.providers.k8s.validation.template_validator import _config_dict_to_k8s_fields

        config = {"image_id": "nginx", "namespace": None}
        result = _config_dict_to_k8s_fields(config)
        assert "namespace" not in result

    def test_max_number_alias_normalised(self) -> None:
        # maxNumber is an alias for max_instances
        from orb.providers.k8s.validation.template_validator import _config_dict_to_k8s_fields

        config = {"image_id": "nginx", "maxNumber": 10}
        result = _config_dict_to_k8s_fields(config)
        assert result.get("max_instances") == 10


@pytest.mark.unit
class TestK8sTemplateValidatorDictInput:
    """Tests for :class:`K8sTemplateValidator` when input is a raw config dict."""

    def _validator(self) -> Any:
        from orb.providers.k8s.validation.template_validator import K8sTemplateValidator

        return K8sTemplateValidator()

    def test_dict_with_valid_fields_passes(self) -> None:
        v = self._validator()
        result = v.validate({"template_id": "t1", "image_id": "nginx:latest"})
        assert result.valid

    def test_dict_with_max_number_zero_errors(self) -> None:
        v = self._validator()
        result = v.validate({"template_id": "t1", "image_id": "nginx", "maxNumber": 0})
        assert not result.valid
        assert any("max_instances" in e for e in result.errors)

    def test_dict_with_camelcase_provider_api_error(self) -> None:
        v = self._validator()
        result = v.validate({"template_id": "t1", "image_id": "nginx", "providerApi": "DaemonSet"})
        assert not result.valid

    def test_toleration_dict_invalid_fails(self) -> None:
        v = self._validator()
        # An int as a toleration entry is not a dict or K8sToleration
        result = v.validate({"template_id": "t1", "image_id": "nginx", "tolerations": [42]})
        assert not result.valid
        # Error message includes info about the toleration issue
        assert len(result.errors) > 0

    def test_restart_policy_always_for_job_errors(self) -> None:
        v = self._validator()
        result = v.validate(
            {
                "template_id": "t1",
                "image_id": "nginx",
                "providerApi": "Job",
                "restartPolicy": "Always",
            }
        )
        assert not result.valid


# ---------------------------------------------------------------------------
# configuration/config.py validators
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestK8sNamingConfig:
    """Tests for :class:`K8sNamingConfig` validators."""

    def test_default_config_valid(self) -> None:
        from orb.providers.k8s.configuration.config import K8sNamingConfig

        config = K8sNamingConfig.model_validate({})
        assert config.prefix == "orb"
        assert config.uuid_chars == 20

    def test_invalid_prefix_raises(self) -> None:
        from orb.providers.k8s.configuration.config import K8sNamingConfig

        with pytest.raises(Exception, match="not a valid DNS-1123 label"):
            K8sNamingConfig.model_validate({"prefix": "Bad-Prefix"})

    def test_empty_prefix_raises(self) -> None:
        from orb.providers.k8s.configuration.config import K8sNamingConfig

        with pytest.raises(Exception, match="non-empty"):
            K8sNamingConfig.model_validate({"prefix": ""})

    def test_too_long_prefix_raises(self) -> None:
        from orb.providers.k8s.configuration.config import K8sNamingConfig

        with pytest.raises(Exception, match="too long"):
            K8sNamingConfig.model_validate({"prefix": "a" * 21})

    def test_budget_overflow_raises(self) -> None:
        from orb.providers.k8s.configuration.config import K8sNamingConfig

        # prefix=my-prefix (9 chars) + 1 hyphen + uuid_chars=32 = 42 > max_deployment_name_len=40
        with pytest.raises(Exception):
            K8sNamingConfig.model_validate(
                {"prefix": "my-prefix", "uuid_chars": 32, "max_deployment_name_len": 40}
            )


@pytest.mark.unit
class TestK8sProviderConfigValidators:
    """Tests for :class:`K8sProviderConfig` field validators."""

    def test_default_config_has_default_namespace(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        config = K8sProviderConfig()  # type: ignore[call-arg]
        assert config.namespace == "default"

    def test_explicit_namespace_accepted(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        config = K8sProviderConfig(namespace="my-ns")  # type: ignore[call-arg]
        assert config.namespace == "my-ns"

    def test_invalid_namespace_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception):
            K8sProviderConfig(namespace="My_Invalid_NS")  # type: ignore[call-arg]

    def test_invalid_label_prefix_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception):
            K8sProviderConfig(label_prefix="bad label")  # type: ignore[call-arg]

    def test_valid_label_prefix_accepted(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        config = K8sProviderConfig(label_prefix="my-company.io")  # type: ignore[call-arg]
        assert config.label_prefix == "my-company.io"

    def test_invalid_restart_policy_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="default_restart_policy"):
            K8sProviderConfig(default_restart_policy="always")  # type: ignore[call-arg]

    def test_valid_restart_policies_accepted(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        for policy in ("Always", "OnFailure", "Never"):
            config = K8sProviderConfig(default_restart_policy=policy)  # type: ignore[call-arg]
            assert config.default_restart_policy == policy

    def test_empty_context_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="non-empty"):
            K8sProviderConfig(context="   ")  # type: ignore[call-arg]

    def test_empty_namespaces_list_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="non-empty list"):
            K8sProviderConfig(namespaces=[])  # type: ignore[call-arg]

    def test_namespaces_with_empty_entry_rejected(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="non-empty strings"):
            K8sProviderConfig(namespaces=["valid", ""])  # type: ignore[call-arg]

    def test_native_spec_without_rejection_flag_raises(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="reject_high_risk_pod_fields"):
            K8sProviderConfig(  # type: ignore[call-arg]
                native_spec_enabled=True, reject_high_risk_pod_fields=False
            )

    def test_legacy_field_names_remapped(self) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        config = K8sProviderConfig(kube_config_path=None, kube_context=None)  # type: ignore[call-arg]
        # No error means remapping worked
        assert config.kubeconfig_path is None

    def test_kubeconfig_path_nonexistent_raises(self, tmp_path: Any) -> None:
        from orb.providers.k8s.configuration.config import K8sProviderConfig

        with pytest.raises(Exception, match="does not exist"):
            K8sProviderConfig(kubeconfig_path=str(tmp_path / "nonexistent.yaml"))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# resilience
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestK8sCircuitBreaker:
    """Tests for :class:`K8sCircuitBreaker`."""

    def _make_breaker(self, name: str, threshold_provider=None) -> Any:
        from orb.providers.k8s.resilience.circuit_breaker import K8sCircuitBreaker

        return K8sCircuitBreaker(
            service_name=name,
            failure_threshold=3,
            reset_timeout=60,
            threshold_provider=threshold_provider,
        )

    def test_default_threshold_used_without_provider(self) -> None:
        cb = self._make_breaker("test-cb-default")
        assert cb._get_failure_threshold() == 3

    def test_threshold_provider_overrides_static(self) -> None:
        provider = MagicMock(return_value=10)
        cb = self._make_breaker("test-cb-provider", threshold_provider=provider)
        assert cb._get_failure_threshold() == 10
        provider.assert_called_once()

    def test_threshold_provider_raises_falls_back(self) -> None:
        provider = MagicMock(side_effect=RuntimeError("config reload failed"))
        cb = self._make_breaker("test-cb-fallback", threshold_provider=provider)
        # Should fall back to the static threshold
        assert cb._get_failure_threshold() == 3

    def test_threshold_provider_returns_none_falls_back(self) -> None:
        provider = MagicMock(return_value=None)
        cb = self._make_breaker("test-cb-none", threshold_provider=provider)
        assert cb._get_failure_threshold() == 3

    def test_threshold_provider_returns_zero_falls_back(self) -> None:
        provider = MagicMock(return_value=0)
        cb = self._make_breaker("test-cb-zero", threshold_provider=provider)
        assert cb._get_failure_threshold() == 3

    def test_metrics_emit_called_on_state_change(self) -> None:
        from orb.providers.k8s.resilience.circuit_breaker import K8sCircuitBreaker

        metrics = MagicMock()
        cb = K8sCircuitBreaker(
            service_name="test-cb-metrics",
            failure_threshold=1,
            reset_timeout=60,
            metrics=metrics,
        )
        import time

        now = time.monotonic()
        cb.record_failure(now)
        # Circuit should open after 1 failure; metrics should have been called
        metrics.set_circuit_breaker_state.assert_called()

    def test_no_metrics_no_error(self) -> None:
        cb = self._make_breaker("test-cb-no-metrics")
        import time

        # Should not raise when no metrics are provided
        cb.record_failure(time.monotonic())


@pytest.mark.unit
class TestK8sRetryClassifier:
    """Tests for :class:`K8sRetryClassifier`."""

    def _make_classifier(self) -> Any:
        from orb.providers.k8s.resilience.retry_classifier import K8sRetryClassifier

        return K8sRetryClassifier()

    def test_non_api_exception_not_non_retryable(self) -> None:
        classifier = self._make_classifier()
        assert classifier.is_non_retryable(RuntimeError("network error")) is False

    def test_regular_exception_not_non_retryable(self) -> None:
        classifier = self._make_classifier()
        assert classifier.is_non_retryable(ValueError("bad value")) is False

    def test_404_api_exception_non_retryable(self) -> None:
        classifier = self._make_classifier()
        try:
            from kubernetes.client.exceptions import ApiException

            exc = ApiException(status=404)
            assert classifier.is_non_retryable(exc) is True
        except ImportError:
            pytest.skip("kubernetes SDK not installed")

    def test_500_api_exception_is_retryable(self) -> None:
        classifier = self._make_classifier()
        try:
            from kubernetes.client.exceptions import ApiException

            exc = ApiException(status=500)
            assert classifier.is_non_retryable(exc) is False
        except ImportError:
            pytest.skip("kubernetes SDK not installed")

    def test_403_api_exception_non_retryable(self) -> None:
        classifier = self._make_classifier()
        try:
            from kubernetes.client.exceptions import ApiException

            exc = ApiException(status=403)
            assert classifier.is_non_retryable(exc) is True
        except ImportError:
            pytest.skip("kubernetes SDK not installed")
