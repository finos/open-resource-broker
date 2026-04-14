"""Tests for the GCP compute client wrapper."""

from __future__ import annotations

import sys
from types import ModuleType
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from orb.infrastructure.mocking.dry_run_context import dry_run_context
from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.exceptions import GCPDryRunBlockedError
from orb.providers.gcp.infrastructure.compute_client import (
    GCPComputeClient,
    GCP_MUTATION_RETRYABLE_GOOGLE_API_EXCEPTIONS,
    GCP_READ_RETRYABLE_GOOGLE_API_EXCEPTIONS,
    GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS,
)


class _FakeInstancesClient:
    def __init__(self) -> None:
        self.insert_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []

    def insert(self, **kwargs: object) -> object:
        self.insert_calls.append(kwargs)
        return SimpleNamespace(name="insert-op")

    def get(self, **kwargs: object) -> object:
        self.get_calls.append(kwargs)
        return SimpleNamespace(name="vm-1", status="RUNNING", self_link="instance-link")


class _FakeImagesClient:
    def __init__(self) -> None:
        self.get_from_family_calls: list[dict[str, object]] = []

    def get_from_family(self, **kwargs: object) -> object:
        self.get_from_family_calls.append(kwargs)
        return SimpleNamespace(name="debian-12", self_link="image-link")


def _config(**overrides: object) -> GCPProviderConfig:
    payload: dict[str, object] = {
        "project_id": "orb-example-12345",
        "region": "us-central1",
        "max_retries": 4,
        "connect_timeout": 7,
        "read_timeout": 11,
    }
    payload.update(overrides)
    return GCPProviderConfig(**payload)


def test_create_instance_passes_configured_retry_and_timeout(monkeypatch) -> None:
    fake_instances_client = _FakeInstancesClient()
    fake_compute_v1 = SimpleNamespace(InstancesClient=lambda: fake_instances_client)
    client = GCPComputeClient(config=_config(), logger=MagicMock())

    monkeypatch.setattr(client, "_compute_v1", lambda: fake_compute_v1)
    monkeypatch.setattr(client, "_build_retry_policy", lambda operation_name: f"{operation_name}-policy")

    body = SimpleNamespace(name="vm-1")
    client.create_instance(zone="us-central1-a", body=body)

    assert fake_instances_client.insert_calls == [
        {
            "project": "orb-example-12345",
            "zone": "us-central1-a",
            "instance_resource": body,
            "retry": "mutation-policy",
            "timeout": (7.0, 11.0),
        }
    ]


def test_get_image_from_family_passes_configured_retry_and_timeout(monkeypatch) -> None:
    fake_images_client = _FakeImagesClient()
    fake_compute_v1 = SimpleNamespace(ImagesClient=lambda: fake_images_client)
    client = GCPComputeClient(config=_config(), logger=MagicMock())

    monkeypatch.setattr(client, "_compute_v1", lambda: fake_compute_v1)
    monkeypatch.setattr(client, "_build_retry_policy", lambda operation_name: f"{operation_name}-policy")

    client.get_image_from_family(image_project="debian-cloud", family="debian-12")

    assert fake_images_client.get_from_family_calls == [
        {
            "project": "debian-cloud",
            "family": "debian-12",
            "retry": "image_read-policy",
            "timeout": (7.0, 11.0),
        }
    ]


def test_max_retries_zero_disables_sdk_retry(monkeypatch) -> None:
    fake_instances_client = _FakeInstancesClient()
    fake_compute_v1 = SimpleNamespace(InstancesClient=lambda: fake_instances_client)
    client = GCPComputeClient(config=_config(max_retries=0), logger=MagicMock())

    monkeypatch.setattr(client, "_compute_v1", lambda: fake_compute_v1)

    client.get_instance(zone="us-central1-a", instance_name="vm-1")

    assert fake_instances_client.get_calls == [
        {
            "project": "orb-example-12345",
            "zone": "us-central1-a",
            "instance": "vm-1",
            "retry": None,
            "timeout": (7.0, 11.0),
        }
    ]


def test_retryable_exception_list_is_explicit() -> None:
    assert GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS == (
        "InternalServerError",
        "BadGateway",
        "ServiceUnavailable",
        "GatewayTimeout",
        "TooManyRequests",
    )
    assert GCP_READ_RETRYABLE_GOOGLE_API_EXCEPTIONS == (
        *GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS,
        "DeadlineExceeded",
    )
    assert GCP_MUTATION_RETRYABLE_GOOGLE_API_EXCEPTIONS == (
        *GCP_RETRYABLE_GOOGLE_API_EXCEPTIONS,
        "ResourceExhausted",
    )


def test_retry_policy_is_cached_per_operation_profile(monkeypatch) -> None:
    client = GCPComputeClient(config=_config(), logger=MagicMock())
    build_calls: list[str] = []

    def fake_build_retry_policy(operation_name: str) -> str:
        build_calls.append(operation_name)
        return f"{operation_name}-policy"

    monkeypatch.setattr(client, "_build_retry_policy", fake_build_retry_policy)

    assert client._get_retry_policy("read") == "read-policy"
    assert client._get_retry_policy("image_read") == "read-policy"
    assert client._get_retry_policy("mutation") == "mutation-policy"
    assert client._get_retry_policy("delete") == "delete-policy"
    assert build_calls == ["read", "mutation", "delete"]


def test_build_retry_policy_uses_profile_specific_exceptions_and_logs(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeRetry:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    def _fake_if_exception_type(*exc_types: object) -> tuple[object, ...]:
        return exc_types

    class InternalServerError(Exception):
        pass

    class BadGateway(Exception):
        pass

    class ServiceUnavailable(Exception):
        pass

    class GatewayTimeout(Exception):
        pass

    class TooManyRequests(Exception):
        pass

    class DeadlineExceeded(Exception):
        pass

    class ResourceExhausted(Exception):
        pass

    google_module = ModuleType("google")
    api_core_module = ModuleType("google.api_core")
    exceptions_module = ModuleType("google.api_core.exceptions")
    retry_module = ModuleType("google.api_core.retry")

    for exc_type in (
        InternalServerError,
        BadGateway,
        ServiceUnavailable,
        GatewayTimeout,
        TooManyRequests,
        DeadlineExceeded,
        ResourceExhausted,
    ):
        setattr(exceptions_module, exc_type.__name__, exc_type)

    retry_module.Retry = _FakeRetry
    retry_module.if_exception_type = _fake_if_exception_type
    api_core_module.exceptions = exceptions_module
    api_core_module.retry = retry_module
    google_module.api_core = api_core_module

    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.api_core", api_core_module)
    monkeypatch.setitem(sys.modules, "google.api_core.exceptions", exceptions_module)
    monkeypatch.setitem(sys.modules, "google.api_core.retry", retry_module)

    logger = MagicMock()
    client = GCPComputeClient(config=_config(), logger=logger)

    client._build_retry_policy("read")

    assert captured["predicate"] == (
        InternalServerError,
        BadGateway,
        ServiceUnavailable,
        GatewayTimeout,
        TooManyRequests,
        DeadlineExceeded,
    )
    assert captured["initial"] == 0.5
    assert captured["maximum"] == 5.0
    assert captured["multiplier"] == 1.5
    assert captured["timeout"] == 72.0

    on_error = captured["on_error"]
    assert callable(on_error)
    retry_error = DeadlineExceeded("retry me")
    on_error(retry_error)  # type: ignore[operator]
    logger.warning.assert_called_once_with(
        "Retrying GCP %s operation after %s: %s",
        "read",
        "DeadlineExceeded",
        retry_error,
    )


def test_retry_profile_rejects_unknown_operation() -> None:
    client = GCPComputeClient(config=_config(), logger=MagicMock())

    try:
        client._retry_profile_for("unknown")
    except ValueError as exc:
        assert "Unsupported GCP retry operation profile" in str(exc)
    else:
        raise AssertionError("expected ValueError for unknown retry profile")


def test_compute_client_blocks_real_calls_when_dry_run_is_active() -> None:
    client = GCPComputeClient(config=_config(), logger=MagicMock())

    with dry_run_context(True):
        with pytest.raises(GCPDryRunBlockedError, match="create_instance"):
            client.create_instance(zone="us-central1-a", body=SimpleNamespace(name="vm-1"))

        with pytest.raises(GCPDryRunBlockedError, match="get_image_from_family"):
            client.get_image_from_family(image_project="debian-cloud", family="debian-12")
