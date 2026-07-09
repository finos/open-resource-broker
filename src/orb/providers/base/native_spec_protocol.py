"""Provider-neutral protocol for native-spec rendering services.

This module defines the structural protocol that both AWS and Kubernetes
(and any future) provider native-spec services must satisfy.  Placing it
here prevents providers from importing each other just to share the type.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NativeSpecServiceProtocol(Protocol):
    """Structural protocol matching NativeSpecService from the application layer.

    Using a protocol here avoids a providers→application import while still
    allowing type-safe usage of the service across provider boundaries.

    Both :class:`orb.providers.aws.infrastructure.services.aws_native_spec_service.AWSNativeSpecService`
    and :class:`orb.providers.k8s.infrastructure.services.k8s_native_spec_service.K8sNativeSpecService`
    accept a conforming instance; this neutral location keeps those two
    providers from depending on each other.
    """

    spec_renderer: Any
    logger: Any

    def is_native_spec_enabled(self) -> bool:  # pyright: ignore[reportReturnType]
        pass  # type: ignore[empty-body]

    def render_spec(self, spec: dict, context: dict) -> dict:  # pyright: ignore[reportReturnType]
        pass  # type: ignore[empty-body]


__all__ = ["NativeSpecServiceProtocol"]
