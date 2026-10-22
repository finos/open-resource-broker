"""Offline smoke test through the installed CLI and real application bootstrap."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scheduler", ["default", "hostfactory"])
def test_init_generate_list_with_oci_defaults(tmp_path, scheduler):
    config_dir = tmp_path / "config"
    config_path = config_dir / "config.json"
    orb = Path(sys.executable).parent / ("orb.exe" if os.name == "nt" else "orb")
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "ORB_CONFIG_DIR": str(config_dir),
        "ORB_WORK_DIR": str(tmp_path / "work"),
        "ORB_LOG_DIR": str(tmp_path / "logs"),
    }
    # Retain pytest's environment: only an explicit dry-run request may
    # simulate OCI operations, including in a child CLI process.
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    invoked = tmp_path / "unexpected-oci-call"
    fake_cli = fake_bin / ("oci.cmd" if os.name == "nt" else "oci")
    if os.name == "nt":
        fake_cli.write_text(f'@echo off\necho invoked > "{invoked}"\nexit /b 97\n')
    else:
        import shlex

        fake_cli.write_text(f"#!/bin/sh\necho invoked > {shlex.quote(str(invoked))}\nexit 97\n")
        fake_cli.chmod(0o755)
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")

    def run(*args):
        result = subprocess.run(
            [str(orb), *args],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(
        "init",
        "--non-interactive",
        "--scheduler",
        scheduler,
        "--provider-type",
        "oci",
        "--oci-credential-source",
        "instance_principal",
        "--oci-region",
        "us-ashburn-1",
        "--config-dir",
        str(config_dir),
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    provider = config["provider"]["providers"][0]
    assert provider["type"] == "oci"
    assert provider["config"] == {
        "region": "us-ashburn-1",
        "credential_source": "instance_principal",
    }
    provider["template_defaults"] = {
        "image_id": "ocid1.image.oc1..example",
        "subnet_ids": ["ocid1.subnet.oc1..example"],
        "compartment_id": "ocid1.compartment.oc1..example",
        "availability_domain": "AD-1",
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    run("--config", str(config_path), "templates", "generate")
    output = run("--config", str(config_path), "templates", "list")
    payload = json.loads(output)
    assert len(payload["templates"]) == 25
    # Both scheduler representations must carry resolved deployment defaults.
    assert "ocid1.image.oc1..example" in output
    if scheduler == "default":
        assert "ocid1.subnet.oc1..example" in output
        assert "ocid1.compartment.oc1..example" in output
    assert "VM.Standard.E6.Flex" in output

    # Exercise the real provisioning orchestrator, which supplies both a domain
    # Template object and a serialized template_config to the provider.
    acquired = json.loads(
        run(
            "--config",
            str(config_path),
            "machines",
            "request",
            "oci-vm-standard-e5-ondemand",
            "1",
            "--dry-run",
        )
    )
    request_id = acquired.get("request_id") or acquired["requestId"]
    status = json.loads(run("--config", str(config_path), "requests", "show", request_id))
    request = status["requests"][0]
    assert request["status"] in {"complete", "completed"}, request
    if scheduler == "default":
        assert request["provider_type"] == "oci"
        assert request["provider_api"] == "OCICompute"
    assert not invoked.exists(), "Offline workflow invoked OCI CLI"
