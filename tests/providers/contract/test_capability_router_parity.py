"""Declared provider capabilities match the operations each strategy routes.

A strategy that routes an operation it does not declare would be rejected by
the registry's capability gate; a strategy that declares an operation it never
routes would pass the gate and then fail at dispatch. Both directions are
checked for every provider, with the routed set read from the strategy source.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from unittest.mock import MagicMock

import pytest

from orb.domain.base.operations import OperationType
from orb.providers.aws.services.capability_service import AWSCapabilityService
from orb.providers.aws.strategy.aws_provider_strategy import AWSProviderStrategy
from orb.providers.azure.strategy.azure_provider_strategy import AzureProviderStrategy
from orb.providers.gcp.strategy.gcp_provider_strategy import GCPProviderStrategy
from orb.providers.k8s.services.capability_service import K8sCapabilityService
from orb.providers.k8s.strategy.k8s_provider_strategy import K8sProviderStrategy
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


def _routed_operations(strategy_cls: type) -> set[OperationType]:
    """Operation types the strategy compares ``operation_type`` against."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(strategy_cls)))
    routed: set[OperationType] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not all(isinstance(o, ast.Eq) for o in node.ops):
            continue
        for operand in [node.left, *node.comparators]:
            if (
                isinstance(operand, ast.Attribute)
                and isinstance(operand.value, ast.Name)
                and operand.value.id.endswith("OperationType")
                and operand.attr in OperationType.__members__
            ):
                routed.add(OperationType[operand.attr])
    return routed


def _declared_aws() -> set[OperationType]:
    caps = AWSCapabilityService(handler_registry=None, logger=MagicMock()).get_capabilities()  # type: ignore[arg-type]
    return set(caps.supported_operations)


def _declared_k8s() -> set[OperationType]:
    return set(K8sCapabilityService(logger=MagicMock()).get_capabilities().supported_operations)


def _declared_azure() -> set[OperationType]:
    strategy = object.__new__(AzureProviderStrategy)
    return set(strategy.get_capabilities().supported_operations)


def _declared_gcp() -> set[OperationType]:
    strategy = object.__new__(GCPProviderStrategy)
    return set(strategy.get_capabilities().supported_operations)


def _declared_oci() -> set[OperationType]:
    strategy = object.__new__(OCIProviderStrategy)
    strategy._config = MagicMock(region="test-region")  # type: ignore[attr-defined]
    return set(strategy.get_capabilities().supported_operations)


_PROVIDERS = {
    "aws": (AWSProviderStrategy, _declared_aws),
    "k8s": (K8sProviderStrategy, _declared_k8s),
    "azure": (AzureProviderStrategy, _declared_azure),
    "gcp": (GCPProviderStrategy, _declared_gcp),
    "oci": (OCIProviderStrategy, _declared_oci),
}


@pytest.mark.provider_contract
@pytest.mark.parametrize("provider", sorted(_PROVIDERS))
def test_declared_operations_match_routed_operations(provider: str) -> None:
    strategy_cls, declared_of = _PROVIDERS[provider]
    routed = _routed_operations(strategy_cls)
    declared = declared_of()

    assert routed, f"no routed operations found for {provider}; the source scan needs updating"
    assert routed - declared == set(), (
        f"{provider} routes operations it does not declare: "
        f"{sorted(o.name for o in routed - declared)}"
    )
    assert declared - routed == set(), (
        f"{provider} declares operations it does not route: "
        f"{sorted(o.name for o in declared - routed)}"
    )
