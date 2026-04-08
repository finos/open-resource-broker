"""Tests for the GCP compute client wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.infrastructure.compute_client import (
    GCPComputeClient,
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
    monkeypatch.setattr(client, "_build_retry_policy", lambda: "retry-policy")

    body = SimpleNamespace(name="vm-1")
    client.create_instance(zone="us-central1-a", body=body)

    assert fake_instances_client.insert_calls == [
        {
            "project": "orb-example-12345",
            "zone": "us-central1-a",
            "instance_resource": body,
            "retry": "retry-policy",
            "timeout": (7.0, 11.0),
        }
    ]


def test_get_image_from_family_passes_configured_retry_and_timeout(monkeypatch) -> None:
    fake_images_client = _FakeImagesClient()
    fake_compute_v1 = SimpleNamespace(ImagesClient=lambda: fake_images_client)
    client = GCPComputeClient(config=_config(), logger=MagicMock())

    monkeypatch.setattr(client, "_compute_v1", lambda: fake_compute_v1)
    monkeypatch.setattr(client, "_build_retry_policy", lambda: "retry-policy")

    client.get_image_from_family(image_project="debian-cloud", family="debian-12")

    assert fake_images_client.get_from_family_calls == [
        {
            "project": "debian-cloud",
            "family": "debian-12",
            "retry": "retry-policy",
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
