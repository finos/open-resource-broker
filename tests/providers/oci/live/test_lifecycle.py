"""One worker acquire/status/return cycle using an installed ORB build."""

import os
import sys

import pytest

from tests.providers.oci.live import gate

pytestmark = pytest.mark.serial


def test_acquire_status_return(monkeypatch, tmp_path, request):
    if not request.config.getoption("--run-oci") and os.environ.get("ORB_RUN_OCI_LIVE") != "1":
        pytest.skip("Use --run-oci or ORB_RUN_OCI_LIVE=1 to provision a canary")
    config = os.environ.get("ORB_OCI_LIVE_CONFIG")
    templates = os.environ.get("ORB_OCI_LIVE_TEMPLATES")
    assert config and templates, "Set ORB_OCI_LIVE_CONFIG and ORB_OCI_LIVE_TEMPLATES"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "oci-live",
            "--config",
            config,
            "--template-file",
            templates,
            "--template-id",
            os.environ.get("ORB_OCI_LIVE_TEMPLATE", "oci-vm-standard-e5-ondemand"),
            "--provider",
            os.environ.get("ORB_OCI_LIVE_PROVIDER", "oci-default"),
            "--orb-bin",
            os.environ.get("ORB_OCI_LIVE_ORB", "orb"),
            "--counts",
            "1",
            "--timeout",
            "600",
            "--report-file",
            str(tmp_path / "canary.json"),
        ],
    )
    assert gate.main() == 0
