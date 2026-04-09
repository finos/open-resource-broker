"""Tests for GCP service-account scope wiring."""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock

from orb.providers.gcp.configuration.config import GCPProviderConfig
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.gcp.infrastructure.handlers.mig_handler import GCPManagedInstanceGroupHandler
from orb.providers.gcp.infrastructure.handlers.single_vm_handler import GCPSingleVMHandler


class _FakeAttachedDiskInitializeParams:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeAttachedDisk:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeTags:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeNetworkInterface:
    def __init__(self):
        self.network = None
        self.subnetwork = None


class _FakeServiceAccount:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeScheduling:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeInstance:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeInstanceProperties:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeInstanceTemplate:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _ComputeClientStub:
    pass


def _install_fake_compute_v1(monkeypatch) -> None:
    fake_compute_v1 = ModuleType("google.cloud.compute_v1")
    fake_compute_v1.AttachedDiskInitializeParams = _FakeAttachedDiskInitializeParams
    fake_compute_v1.AttachedDisk = _FakeAttachedDisk
    fake_compute_v1.Tags = _FakeTags
    fake_compute_v1.NetworkInterface = _FakeNetworkInterface
    fake_compute_v1.ServiceAccount = _FakeServiceAccount
    fake_compute_v1.Scheduling = _FakeScheduling
    fake_compute_v1.Instance = _FakeInstance
    fake_compute_v1.InstanceProperties = _FakeInstanceProperties
    fake_compute_v1.InstanceTemplate = _FakeInstanceTemplate

    fake_cloud = ModuleType("google.cloud")
    fake_cloud.compute_v1 = fake_compute_v1

    fake_google = ModuleType("google")
    fake_google.cloud = fake_cloud

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.compute_v1", fake_compute_v1)


def _config() -> GCPProviderConfig:
    return GCPProviderConfig(
        project_id="orb-example-12345",
        region="us-central1",
        zones=["us-central1-a"],
    )


def _template(provider_api: str) -> GCPTemplate:
    zones = ["us-central1-a"] if provider_api == "SingleVM" else ["us-central1-a", "us-central1-b"]
    return GCPTemplate.model_validate(
        {
            "template_id": f"gcp-{provider_api.lower()}",
            "provider_type": "gcp",
            "provider_api": provider_api,
            "project_id": "orb-example-12345",
            "region": "us-central1",
            "zones": zones,
            "instance_type": "e2-standard-4",
            "max_instances": 1 if provider_api == "SingleVM" else 2,
            "mig_scope": "regional",
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
            "service_account_email": "orb@example.iam.gserviceaccount.com",
            "service_account_scopes": [
                "https://www.googleapis.com/auth/compute.readonly",
                "https://www.googleapis.com/auth/devstorage.read_only",
            ],
        }
    )


def test_single_vm_payload_uses_configured_service_account_scopes(monkeypatch) -> None:
    _install_fake_compute_v1(monkeypatch)
    handler = GCPSingleVMHandler(
        compute_client=_ComputeClientStub(),
        config=_config(),
        logger=MagicMock(),
    )

    payload = handler._build_instance_payload("vm-1", _template("SingleVM"))

    assert payload.service_accounts[0].scopes == [
        "https://www.googleapis.com/auth/compute.readonly",
        "https://www.googleapis.com/auth/devstorage.read_only",
    ]


def test_mig_template_payload_uses_configured_service_account_scopes(monkeypatch) -> None:
    _install_fake_compute_v1(monkeypatch)
    handler = GCPManagedInstanceGroupHandler(
        compute_client=_ComputeClientStub(),
        config=_config(),
        logger=MagicMock(),
    )

    payload = handler._build_instance_template_payload(_template("MIG"), "tmpl-1")

    assert payload.properties.service_accounts[0].scopes == [
        "https://www.googleapis.com/auth/compute.readonly",
        "https://www.googleapis.com/auth/devstorage.read_only",
    ]
