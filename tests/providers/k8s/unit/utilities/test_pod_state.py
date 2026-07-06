"""Unit tests for :mod:`orb.providers.k8s.utilities.pod_state`.

Covers the context-aware ``Succeeded`` phase mapping introduced by the
handler-type distinction:

* Bare ``Pod`` and ``Job``: ``Succeeded`` → ``"terminated"``
* ``Deployment`` and ``StatefulSet``: ``Succeeded`` → ``"running"``
  (controller will respawn the pod; the state is transient)

Also verifies the unchanged mappings for other phases.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from orb.providers.k8s.utilities.pod_state import (
    _CONTROLLER_RESPAWNS_SUCCEEDED,
    pod_status_string,
)

# ---------------------------------------------------------------------------
# pod_status_string — Succeeded phase, context-aware
# ---------------------------------------------------------------------------


class TestSucceededPhaseMapping:
    """Validate context-aware Succeeded phase semantics."""

    def test_bare_pod_succeeded_maps_to_terminated(self) -> None:
        """Bare Pod Succeeded → terminated (run-to-completion, no restart)."""
        assert pod_status_string("Succeeded", False, provider_api="Pod") == "terminated"

    def test_job_pod_succeeded_maps_to_terminated(self) -> None:
        """Job pod Succeeded → terminated (completions counted, not respawned)."""
        assert pod_status_string("Succeeded", False, provider_api="Job") == "terminated"

    def test_deployment_succeeded_maps_to_running(self) -> None:
        """Deployment pod Succeeded → running (controller will respawn immediately)."""
        assert pod_status_string("Succeeded", False, provider_api="Deployment") == "running"

    def test_statefulset_succeeded_maps_to_running(self) -> None:
        """StatefulSet pod Succeeded → running (controller will respawn immediately)."""
        assert pod_status_string("Succeeded", False, provider_api="StatefulSet") == "running"

    def test_unknown_provider_api_succeeded_maps_to_terminated(self) -> None:
        """Unknown provider_api falls back to terminated (safe/conservative)."""
        assert pod_status_string("Succeeded", False, provider_api="CustomCRD") == "terminated"

    def test_none_provider_api_succeeded_maps_to_terminated(self) -> None:
        """No provider_api (legacy callers) maps Succeeded → terminated."""
        assert pod_status_string("Succeeded", False) == "terminated"
        assert pod_status_string("Succeeded", False, provider_api=None) == "terminated"

    def test_readiness_flag_does_not_affect_succeeded_mapping(self) -> None:
        """The ``ready`` flag is irrelevant for Succeeded phase — result is determined
        solely by provider_api."""
        assert pod_status_string("Succeeded", True, provider_api="Pod") == "terminated"
        assert pod_status_string("Succeeded", True, provider_api="Deployment") == "running"


# ---------------------------------------------------------------------------
# pod_status_string — non-Succeeded phases
# ---------------------------------------------------------------------------


class TestOtherPhaseMappings:
    """Verify unchanged phase mappings are not accidentally affected."""

    @pytest.mark.parametrize("provider_api", [None, "Pod", "Deployment", "Job", "StatefulSet"])
    def test_pending_phase(self, provider_api: str | None) -> None:
        assert pod_status_string("Pending", False, provider_api=provider_api) == "pending"

    @pytest.mark.parametrize("provider_api", [None, "Pod", "Deployment", "Job", "StatefulSet"])
    def test_running_ready(self, provider_api: str | None) -> None:
        assert pod_status_string("Running", True, provider_api=provider_api) == "running"

    @pytest.mark.parametrize("provider_api", [None, "Pod", "Deployment", "Job", "StatefulSet"])
    def test_running_not_ready(self, provider_api: str | None) -> None:
        assert pod_status_string("Running", False, provider_api=provider_api) == "starting"

    @pytest.mark.parametrize("provider_api", [None, "Pod", "Deployment", "Job", "StatefulSet"])
    def test_failed_phase(self, provider_api: str | None) -> None:
        assert pod_status_string("Failed", False, provider_api=provider_api) == "failed"

    @pytest.mark.parametrize("provider_api", [None, "Pod", "Deployment", "Job", "StatefulSet"])
    def test_unknown_phase_maps_to_pending(self, provider_api: str | None) -> None:
        assert pod_status_string(None, False, provider_api=provider_api) == "pending"
        assert pod_status_string("Unknown", False, provider_api=provider_api) == "pending"


# ---------------------------------------------------------------------------
# _CONTROLLER_RESPAWNS_SUCCEEDED constant sanity-check
# ---------------------------------------------------------------------------


def test_controller_respawns_set_contains_expected_types() -> None:
    assert "Deployment" in _CONTROLLER_RESPAWNS_SUCCEEDED
    assert "StatefulSet" in _CONTROLLER_RESPAWNS_SUCCEEDED
    assert "Pod" not in _CONTROLLER_RESPAWNS_SUCCEEDED
    assert "Job" not in _CONTROLLER_RESPAWNS_SUCCEEDED


# ---------------------------------------------------------------------------
# Warning logged by base_handler for Deployment / StatefulSet Succeeded
# ---------------------------------------------------------------------------


class TestBaseHandlerSucceededWarning:
    """Verify the handler emits a warning for controller-respawned Succeeded pods."""

    def _make_handler(self, provider_api: str) -> object:
        """Return a minimal K8sHandlerBase subclass with a mock logger."""

        from orb.providers.k8s.configuration.config import K8sProviderConfig
        from orb.providers.k8s.handlers.base_handler import K8sHandlerBase

        class _ConcreteHandler(K8sHandlerBase):
            PROVIDER_API = provider_api

            async def acquire_hosts(self, request, template):  # type: ignore[override]
                raise NotImplementedError

            def check_hosts_status(self, request):  # type: ignore[override]
                raise NotImplementedError

            async def release_hosts(self, machine_ids, request):  # type: ignore[override]
                raise NotImplementedError

            @classmethod
            def get_example_templates(cls):  # type: ignore[override]
                return []

        client = MagicMock()
        config = K8sProviderConfig(namespace="test")
        logger = MagicMock()
        return _ConcreteHandler(kubernetes_client=client, config=config, logger=logger), logger

    def _make_pod(self, *, name: str, phase: str) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(
            metadata=SimpleNamespace(
                name=name,
                namespace="test",
                labels={"orb.io/request-id": "req-1"},
            ),
            spec=SimpleNamespace(node_name="node-1"),
            status=SimpleNamespace(
                phase=phase,
                pod_ip=None,
                host_ip=None,
                start_time=None,
                conditions=[],
                container_statuses=[],
            ),
        )

    def test_deployment_succeeded_logs_warning(self) -> None:
        handler, logger = self._make_handler("Deployment")
        pod = self._make_pod(name="pod-abc", phase="Succeeded")
        result = handler._instance_dict_for_pod(pod, "test")
        assert result["status"] == "running"
        logger.warning.assert_called_once()
        msg = logger.warning.call_args[0][0]
        assert "Succeeded" in msg
        assert "respawn" in msg

    def test_statefulset_succeeded_logs_warning(self) -> None:
        handler, logger = self._make_handler("StatefulSet")
        pod = self._make_pod(name="pod-sts", phase="Succeeded")
        result = handler._instance_dict_for_pod(pod, "test")
        assert result["status"] == "running"
        logger.warning.assert_called_once()

    def test_bare_pod_succeeded_no_warning(self) -> None:
        handler, logger = self._make_handler("Pod")
        pod = self._make_pod(name="pod-bare", phase="Succeeded")
        result = handler._instance_dict_for_pod(pod, "test")
        assert result["status"] == "terminated"
        logger.warning.assert_not_called()

    def test_job_succeeded_no_warning(self) -> None:
        handler, logger = self._make_handler("Job")
        pod = self._make_pod(name="pod-job", phase="Succeeded")
        result = handler._instance_dict_for_pod(pod, "test")
        assert result["status"] == "terminated"
        logger.warning.assert_not_called()

    def test_bare_pod_succeeded_reason_fallback(self) -> None:
        """When kubernetes has not set a terminated reason, supply a fallback."""
        handler, _logger = self._make_handler("Pod")
        pod = self._make_pod(name="pod-bare", phase="Succeeded")
        result = handler._instance_dict_for_pod(pod, "test")
        assert result["status"] == "terminated"
        assert result["status_reason"] == "Container completed successfully"
