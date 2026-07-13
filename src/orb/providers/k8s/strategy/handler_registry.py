"""Backward-compatible re-export shim for K8sHandlerRegistry.

The registry has been relocated to
:mod:`orb.providers.k8s.services.handler_registry` to mirror the AWS
provider's layout (registry lives under ``services/``).  This module
re-exports the class under the old path so any code that imports from
``orb.providers.k8s.strategy.handler_registry`` continues to work
without modification.
"""

from orb.providers.k8s.services.handler_registry import K8sHandlerRegistry

__all__ = ["K8sHandlerRegistry"]
