"""A recording stand-in for ``K8sClient`` that serves custom objects from memory.

Only discovery is allowed on the core API (``get_api_resources``); any other
attribute of ``core_v1``, and any use of ``apps_v1`` or ``batch_v1``, fails the
test.  Every call is recorded so a test can assert what was touched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

ALLOWED_CORE_V1 = frozenset({"get_api_resources"})


class _Guard:
    """Fails on any attribute access outside ``allowed``; records the ones it allows."""

    def __init__(self, name: str, allowed: frozenset[str], calls: list[str]) -> None:
        self._name = name
        self._allowed = allowed
        self._calls = calls

    def __getattr__(self, attr: str) -> Any:
        if attr not in self._allowed:
            # Recorded first: production code swallows exceptions, so the
            # attempt must be visible to the test even when the raise is caught.
            self._calls.append(f"forbidden:{self._name}.{attr}")
            raise AssertionError(f"{self._name}.{attr} must not be used by this provider type")

        def _call(*args: Any, **kwargs: Any) -> Any:
            self._calls.append(f"{self._name}.{attr}")
            return MagicMock(resources=[], group_version="v1")

        return _call


class FakeCustomObjectsApi:
    """In-memory ``CustomObjectsApi`` for one group/version/plural."""

    def __init__(self, calls: list[str]) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self._calls = calls

    def create_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, body: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        self._calls.append("custom_objects_api.create_namespaced_custom_object")
        metadata = {**body["metadata"], "namespace": namespace}
        self.objects[(namespace, metadata["name"])] = {**body, "metadata": metadata}
        return self.objects[(namespace, metadata["name"])]

    def list_namespaced_custom_object(
        self,
        group: str,
        version: str,
        namespace: str,
        plural: str,
        label_selector: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        self._calls.append("custom_objects_api.list_namespaced_custom_object")
        wanted = dict(part.split("=", 1) for part in label_selector.split(",") if "=" in part)
        items = [
            obj
            for (ns, _name), obj in self.objects.items()
            if ns == namespace
            and all(obj["metadata"].get("labels", {}).get(k) == v for k, v in wanted.items())
        ]
        return {"items": items}

    def delete_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str, **_: Any
    ) -> dict[str, Any]:
        self._calls.append("custom_objects_api.delete_namespaced_custom_object")
        if (namespace, name) not in self.objects:
            from kubernetes.client.exceptions import ApiException

            raise ApiException(status=404, reason="Not Found")
        del self.objects[(namespace, name)]
        return {}


class FakeK8sClient:
    """Stands in for ``K8sClient``; records every call it serves."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.core_v1 = _Guard("core_v1", ALLOWED_CORE_V1, self.calls)
        self.apps_v1 = _Guard("apps_v1", frozenset(), self.calls)
        self.batch_v1 = _Guard("batch_v1", frozenset(), self.calls)
        self.custom_objects_api = FakeCustomObjectsApi(self.calls)
        self.api_client = MagicMock()
        self.api_client.configuration.host = "https://fake.example"

    def cleanup(self) -> None:
        self.calls.append("cleanup")

    def forbidden_calls(self) -> list[str]:
        """Attempts on core, apps or batch APIs beyond discovery (recorded even if caught)."""
        return [call for call in self.calls if call.startswith("forbidden:")]
