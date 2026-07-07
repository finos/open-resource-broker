"""Kubernetes handler factory — mirrors aws_handler_factory placement.

The canonical registry and factory logic lives in
:class:`orb.providers.k8s.strategy.handler_registry.K8sHandlerRegistry`.
This module re-exports :class:`K8sHandlerRegistry` under the
``providers/k8s/infrastructure/`` namespace so the k8s provider tree
mirrors the AWS provider layout:

  providers/aws/infrastructure/aws_handler_factory.py → AWSHandlerFactory
  providers/k8s/infrastructure/k8s_handler_factory.py → K8sHandlerRegistry

No new logic lives here; import from this module or directly from
:mod:`orb.providers.k8s.strategy.handler_registry` — both resolve to the
same class.
"""

from orb.providers.k8s.strategy.handler_registry import K8sHandlerRegistry

__all__ = ["K8sHandlerRegistry"]
