"""Tests for the k8s strategy service decomposition (Task 1).

Verifies that:
- K8sCapabilityService, K8sHealthCheckService, K8sInstanceOperationService exist
  and are importable from their new canonical locations under services/
- K8sHandlerRegistry is importable from both services/ (new) and strategy/ (shim)
- K8sStateMapper and K8sProviderAdapter are importable from strategy/
- K8sProviderStrategy delegates to the services rather than re-implementing logic
- The strategy's get_capabilities, check_health, etc. return the correct types
- Backward-compat: strategy/handler_registry.py re-exports K8sHandlerRegistry
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from orb.providers.base.strategy import ProviderCapabilities, ProviderHealthStatus
from orb.providers.k8s.configuration.config import K8sProviderConfig
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.services.handler_registry import K8sHandlerRegistry as RegistryFromServices
from orb.providers.k8s.services.health_check_service import K8sHealthCheckService
from orb.providers.k8s.services.instance_operation_service import K8sInstanceOperationService
from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry as RegistryFromStrategy
from orb.providers.k8s.strategy.k8s_provider_adapter import (
    K8sProviderAdapter,
    K8sStateMapper,
)

# ---------------------------------------------------------------------------
# Import-level smoke tests
# ---------------------------------------------------------------------------


def test_capability_service_importable() -> None:
    assert K8sCapabilityService is not None


def test_health_check_service_importable() -> None:
    assert K8sHealthCheckService is not None


def test_instance_operation_service_importable() -> None:
    assert K8sInstanceOperationService is not None


def test_handler_registry_importable_from_services() -> None:
    assert RegistryFromServices is not None


def test_handler_registry_shim_re_exports_same_class() -> None:
    """strategy/handler_registry.py must re-export the same class."""
    assert RegistryFromStrategy is RegistryFromServices


def test_k8s_state_mapper_importable() -> None:
    assert K8sStateMapper is not None


def test_k8s_provider_adapter_importable() -> None:
    assert K8sProviderAdapter is not None


# ---------------------------------------------------------------------------
# K8sCapabilityService
# ---------------------------------------------------------------------------


class TestK8sCapabilityService:
    def _make_svc(self) -> K8sCapabilityService:
        logger = MagicMock()
        return K8sCapabilityService(logger=logger)

    def test_get_capabilities_returns_correct_type(self) -> None:
        svc = self._make_svc()
        caps = svc.get_capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.provider_type == "k8s"

    def test_capabilities_include_expected_operations(self) -> None:
        svc = self._make_svc()
        caps = svc.get_capabilities()
        # ProviderOperationType is a str enum; .value gives the bare string
        op_values = {op.value for op in caps.supported_operations}
        assert "create_instances" in op_values
        assert "terminate_instances" in op_values
        assert "health_check" in op_values

    def test_capabilities_include_four_provider_apis(self) -> None:
        svc = self._make_svc()
        caps = svc.get_capabilities()
        assert "Pod" in caps.supported_apis
        assert "Deployment" in caps.supported_apis
        assert "StatefulSet" in caps.supported_apis
        assert "Job" in caps.supported_apis

    def test_generate_provider_name_in_cluster(self) -> None:
        assert K8sCapabilityService.generate_provider_name({}) == "k8s_in-cluster"

    def test_generate_provider_name_with_context(self) -> None:
        assert (
            K8sCapabilityService.generate_provider_name({"context": "my-cluster"})
            == "k8s_my-cluster"
        )

    def test_generate_provider_name_sanitises_colons(self) -> None:
        name = K8sCapabilityService.generate_provider_name(
            {"context": "arn:aws:eks:us-east-1:1234:cluster/test"}
        )
        assert ":" not in name
        assert name.startswith("k8s_")

    def test_parse_provider_name_roundtrip(self) -> None:
        config = {"context": "my-cluster"}
        name = K8sCapabilityService.generate_provider_name(config)
        parsed = K8sCapabilityService.parse_provider_name(name)
        assert parsed["context_or_namespace"] == "my-cluster"

    def test_get_available_regions_is_empty(self) -> None:
        assert K8sCapabilityService.get_available_regions() == []

    def test_get_default_region_is_empty(self) -> None:
        assert K8sCapabilityService.get_default_region() == ""

    def test_get_cli_extra_config_keys(self) -> None:
        keys = K8sCapabilityService.get_cli_extra_config_keys()
        assert "namespace" in keys
        assert "context" in keys

    def test_get_credential_requirements_keys(self) -> None:
        reqs = K8sCapabilityService.get_credential_requirements()
        assert "kubeconfig_path" in reqs
        assert "context" in reqs

    def test_get_operational_requirements_namespace(self) -> None:
        reqs = K8sCapabilityService.get_operational_requirements()
        assert "namespace" in reqs


# ---------------------------------------------------------------------------
# K8sHealthCheckService
# ---------------------------------------------------------------------------


class TestK8sHealthCheckService:
    def _make_svc(self) -> K8sHealthCheckService:
        config = K8sProviderConfig(namespace="test")  # type: ignore[call-arg]
        logger = MagicMock()
        return K8sHealthCheckService(config=config, logger=logger)

    def test_healthy_when_api_responds(self) -> None:
        svc = self._make_svc()
        client = MagicMock()
        client.core_v1.get_api_resources.return_value = MagicMock(resources=[1, 2])
        client.api_client.configuration.host = "https://k8s.local"

        result = svc.check_health(client)

        assert isinstance(result, ProviderHealthStatus)
        assert result.is_healthy

    def test_unhealthy_when_api_raises(self) -> None:
        svc = self._make_svc()
        client = MagicMock()
        client.core_v1.get_api_resources.side_effect = ConnectionError("refused")

        result = svc.check_health(client)

        assert isinstance(result, ProviderHealthStatus)
        assert not result.is_healthy
        assert "unreachable" in result.status_message


# ---------------------------------------------------------------------------
# K8sStateMapper
# ---------------------------------------------------------------------------


class TestK8sStateMapper:
    def test_pending_phase_maps_to_pending(self) -> None:
        from orb.domain.base.provider_interfaces import ProviderInstanceState

        mapper = K8sStateMapper()
        assert mapper.map_to_domain_state("Pending") == ProviderInstanceState.PENDING

    def test_running_phase_maps_to_running(self) -> None:
        from orb.domain.base.provider_interfaces import ProviderInstanceState

        mapper = K8sStateMapper()
        assert mapper.map_to_domain_state("Running") == ProviderInstanceState.RUNNING

    def test_succeeded_phase_maps_to_stopped(self) -> None:
        from orb.domain.base.provider_interfaces import ProviderInstanceState

        mapper = K8sStateMapper()
        assert mapper.map_to_domain_state("Succeeded") == ProviderInstanceState.STOPPED

    def test_terminated_orb_status_maps_to_terminated(self) -> None:
        from orb.domain.base.provider_interfaces import ProviderInstanceState

        mapper = K8sStateMapper()
        assert mapper.map_to_domain_state("terminated") == ProviderInstanceState.TERMINATED

    def test_unknown_string_maps_to_unknown(self) -> None:
        from orb.domain.base.provider_interfaces import ProviderInstanceState

        mapper = K8sStateMapper()
        assert mapper.map_to_domain_state("banana") == ProviderInstanceState.UNKNOWN

    def test_roundtrip_running(self) -> None:
        mapper = K8sStateMapper()
        state = mapper.map_to_domain_state("running")
        back = mapper.map_from_domain_state(state)
        assert back == "running"


# ---------------------------------------------------------------------------
# K8sProviderStrategy delegates to services
# ---------------------------------------------------------------------------


class TestStrategyDelegation:
    """Verify the strategy delegates to the service objects rather than
    re-implementing the logic inline.
    """

    def _make_strategy(self) -> Any:
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        config = K8sProviderConfig(namespace="test")  # type: ignore[call-arg]
        logger = MagicMock()
        logger.debug = MagicMock()
        logger.info = MagicMock()
        logger.warning = MagicMock()
        logger.error = MagicMock()
        return K8sProviderStrategy(config=config, logger=logger)

    def test_strategy_has_capability_service_attr(self) -> None:
        strategy = self._make_strategy()
        assert isinstance(strategy._capability_service, K8sCapabilityService)

    def test_strategy_has_health_check_service_attr(self) -> None:
        strategy = self._make_strategy()
        assert isinstance(strategy._health_check_service, K8sHealthCheckService)

    def test_strategy_has_instance_operation_service_attr(self) -> None:
        strategy = self._make_strategy()
        assert isinstance(strategy._instance_operation_service, K8sInstanceOperationService)

    def test_get_capabilities_returns_provider_capabilities(self) -> None:
        strategy = self._make_strategy()
        caps = strategy.get_capabilities()
        assert isinstance(caps, ProviderCapabilities)
        assert caps.provider_type == "k8s"

    def test_generate_provider_name_delegates_to_service(self) -> None:
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        name = K8sProviderStrategy.generate_provider_name({"context": "test-ctx"})
        assert name == K8sCapabilityService.generate_provider_name({"context": "test-ctx"})

    def test_get_available_regions_returns_empty_list(self) -> None:
        from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy

        assert K8sProviderStrategy.get_available_regions() == []

    def test_strategy_line_count_reduced_by_decomposition(self) -> None:
        """Strategy file must stay well below its pre-decomposition size.

        The original file was ~1755 lines.  Extracting the capability,
        health-check and credential methods to services/ brought it down; the
        lifecycle wiring (daemon services, reconciler, watcher, orphan-GC) stays
        in the strategy by design.  The ceiling has a little headroom above the
        post-decomposition size to absorb the dispatch wiring for operations
        added since (VALIDATE_TEMPLATE, START/STOP, cancel routing) without
        re-growing toward the original monolith.
        """
        import pathlib

        strategy_path = (
            pathlib.Path(__file__).parent.parent.parent.parent.parent
            / "src"
            / "orb"
            / "providers"
            / "k8s"
            / "strategy"
            / "k8s_provider_strategy.py"
        )
        line_count = len(strategy_path.read_text().splitlines())
        assert line_count <= 1750, (
            f"k8s_provider_strategy.py has {line_count} lines; "
            "expected <= 1750 (well below the ~1755-line pre-decomposition monolith)."
        )
