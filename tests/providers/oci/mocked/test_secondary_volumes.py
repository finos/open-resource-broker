"""Run acquire, status, and return in fresh broker processes against a stateful OCI fixture."""

import json
import os
import subprocess
import sys
from functools import partialmethod
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

import orb
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.volume_journal import OCIVolumeJournal
from orb.run import cli_main

WORKER = "ocid1.instance.oc1.phx.testworker"
AD = "TEST:PHX-AD-1"
COMPARTMENT = "ocid1.compartment.oc1..test"
SHAPE = "VM.Standard.E5.Flex"


def cloud_response(args, **kwargs):
    """Serve only supported CLI commands. The fixture never invokes an OCI executable."""
    state_path = Path(os.environ["VOLUME_TEST_CLOUD"])
    state = json.loads(state_path.read_text(encoding="utf-8"))

    def option(name):
        return args[args.index(name) + 1]

    def body():
        flag = "--request-body" if "--request-body" in args else "--from-json"
        return json.loads(Path(option(flag).removeprefix("file://")).read_text(encoding="utf-8"))

    args = list(args[1:])
    prefix = args[:3]
    data = {}
    headers = {}
    interrupt = False
    if args[0] == "raw-request":
        route = urlsplit(option("--target-uri")).path.rsplit("/", 1)[-1]
        payload = body()
        if route == "instances":
            state["worker"] = {
                "id": WORKER,
                "shape": SHAPE,
                "compartment-id": COMPARTMENT,
                "availability-domain": AD,
                "lifecycle-state": "RUNNING",
                "display-name": payload["displayName"],
                "freeform-tags": payload["freeformTags"],
            }
            data = {**state["worker"], "lifecycleState": "PROVISIONING"}
        elif route == "volumes":
            token = json.loads(option("--request-headers"))["opc-retry-token"]
            volume_id = state["tokens"].setdefault(
                token, f"ocid1.volume.oc1.phx.test{len(state['tokens'])}"
            )
            data = {
                "id": volume_id,
                "display-name": payload["displayName"],
                "compartment-id": COMPARTMENT,
                "availability-domain": AD,
                "lifecycle-state": "AVAILABLE",
            }
            state["volumes"].setdefault(volume_id, data)
            state.setdefault("create_attempts", []).append([token, payload])
            if os.environ["VOLUME_TEST_CRASH"] == "disk" and not state.get("interrupted"):
                state["interrupted"] = True
                interrupt = True
        elif route == "volumeAttachments":
            volume_id = payload["volumeId"]
            assert payload["instanceId"] == WORKER and volume_id in state["volumes"]
            attached = {
                "id": volume_id.replace(".volume.", ".volumeattachment."),
                "volume-id": volume_id,
                "instance-id": WORKER,
                "compartment-id": COMPARTMENT,
                "availability-domain": AD,
                "lifecycle-state": "ATTACHED",
            }
            state["attachments"][attached["id"]] = attached
            data = attached
        else:
            raise AssertionError(args)
    elif prefix == ["compute", "instance", "get"]:
        assert option("--instance-id") == WORKER
        data = state["worker"]
    elif prefix == ["compute", "instance", "list-vnics"]:
        data = []
    elif prefix == ["compute", "instance", "terminate"]:
        assert option("--instance-id") == WORKER
        state["worker"]["lifecycle-state"] = "TERMINATED"
        for attachment in state["attachments"].values():
            attachment["lifecycle-state"] = "DETACHED"
    elif prefix == ["compute", "volume-attachment", "list"]:
        data = list(state["attachments"].values())
        for option_name, field in (("--volume-id", "volume-id"), ("--instance-id", "instance-id")):
            if option_name in args:
                data = [row for row in data if row[field] == option(option_name)]
    elif prefix == ["compute", "volume-attachment", "get"]:
        data = state["attachments"][option("--volume-attachment-id")]
    elif prefix == ["compute", "volume-attachment", "detach"]:
        state["attachments"][option("--volume-attachment-id")]["lifecycle-state"] = "DETACHED"
    elif prefix == ["bv", "volume", "get"]:
        data = state["volumes"][option("--volume-id")]
        headers = {"etag": "test-etag"}
    elif prefix == ["bv", "volume", "delete"]:
        volume_id = option("--volume-id")
        assert all(
            a["lifecycle-state"] == "DETACHED"
            for a in state["attachments"].values()
            if a["volume-id"] == volume_id
        )
        state["volumes"][volume_id]["lifecycle-state"] = "TERMINATED"
    else:
        raise AssertionError(args)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    if interrupt:
        os._exit(75)  # OCI accepted the create, the broker did not receive a receipt.
    response = {"data": data, "etag": "test-etag", "headers": headers, "status": "200 OK"}
    return subprocess.CompletedProcess(args, 0, json.dumps(response), "")


@pytest.mark.parametrize("crash_point", ["disk", "worker"])
def test_secondary_volumes_through_installed_cli(tmp_path, crash_point):
    cfg = tmp_path / "config"
    cloud = tmp_path / "cloud.json"
    cloud.write_text(json.dumps({"volumes": {}, "attachments": {}, "tokens": {}}), encoding="utf-8")
    source = str(Path(orb.__file__).resolve().parent.parent)
    env = {
        **os.environ,
        "PYTHONPATH": source,
        "EXPECTED_ORB_SOURCE": source,
        "PYTHONUTF8": "1",
        "ORB_CONFIG_DIR": str(cfg),
        "ORB_LOG_DIR": str(tmp_path / "logs"),
        "ORB_WORK_DIR": str(tmp_path / "work"),
        "VOLUME_TEST_CLOUD": str(cloud),
        "VOLUME_TEST_CRASH": crash_point,
    }

    def run(*args):
        cmd = [sys.executable, str(Path(__file__).resolve()), *args]
        result = subprocess.run(
            cmd,
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if result.returncode == 75:
            assert json.loads(cloud.read_text(encoding="utf-8"))["interrupted"]
            if "show" in args:
                return run(*args)
            # A crash during acquisition loses the command output. Read the ID
            # of the request already submitted, without submitting another worker.
            assert "request" in args
            worker = json.loads(cloud.read_text(encoding="utf-8"))["worker"]
            return json.dumps({"request_id": worker["freeform-tags"]["orb_request_id"]})
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(
        "init",
        "--non-interactive",
        "--scheduler",
        "default",
        "--provider-type",
        "oci",
        "--oci-credential-source",
        "instance_principal",
        "--oci-region",
        "us-phoenix-1",
        "--config-dir",
        str(cfg),
    )
    config_path = cfg / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["storage"] = {
        "strategy": "sql",
        "sql_strategy": {"type": "sqlite", "name": str(tmp_path / "orb.db")},
    }
    provider = config["provider"]["providers"][0]
    provider["template_defaults"] = {
        "image_id": "ocid1.image.oc1.phx.test",
        "subnet_ids": ["ocid1.subnet.oc1.phx.test"],
        "compartment_id": COMPARTMENT,
        "availability_domain": AD,
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    run("--config", str(config_path), "templates", "generate")
    template_file = cfg / "oci_templates.json"
    catalog = json.loads(template_file.read_text(encoding="utf-8"))
    template_id = "oci-vm-standard-e5-ondemand"
    target = next(t for t in catalog["templates"] if t["template_id"] == template_id)
    target["secondary_volumes"] = [
        {"size_in_gbs": 50, "display_name": "scratch", "delete_on_return": True},
        {"size_in_gbs": 50, "display_name": "results", "delete_on_return": False},
    ]
    template_file.write_text(json.dumps(catalog), encoding="utf-8")

    def operation(*args):
        return json.loads(run("--config", str(config_path), *args))

    def wait_for_request(request):
        for _ in range(8):
            row = operation("requests", "show", request["request_id"])["requests"][0]
            if row["status"] in {"complete", "completed"}:
                return
            assert row["status"] not in {"failed", "timeout", "partial"}, row
        raise AssertionError(row)

    acquire = operation("machines", "request", template_id, "1", "--provider-name", "oci-default")
    wait_for_request(acquire)
    attached = json.loads(cloud.read_text(encoding="utf-8"))
    assert attached["interrupted"]
    assert len(attached["volumes"]) == 2
    attempts = attached["create_attempts"]
    if crash_point == "disk":
        assert len(attempts) == 3 and attempts[0] == attempts[1]
    else:
        assert len(attempts) == 2
    assert attempts[-1][0] != attempts[0][0]
    assert {
        a["volume-id"]
        for a in attached["attachments"].values()
        if a["lifecycle-state"] == "ATTACHED"
    } == set(attached["volumes"])
    wait_for_request(operation("machines", "return", WORKER, "--provider-name", "oci-default"))
    final = json.loads(cloud.read_text(encoding="utf-8"))
    assert final["worker"]["lifecycle-state"] == "TERMINATED"
    assert {v["display-name"]: v["lifecycle-state"] for v in final["volumes"].values()} == {
        "scratch": "TERMINATED",
        "results": "AVAILABLE",
    }

    request = operation("requests", "show", acquire["request_id"])["requests"][0]
    data = request["provider_data"]
    for slot, disposition in enumerate(("deleted", "retained")):
        prefix = f"oci_secondary_volumes/{WORKER}/plan/{slot}"
        assert data[f"{prefix}/outcome"] == disposition
        disk = final["volumes"][data[f"{prefix}/volume"]]
        assert disk["display-name"] == ("scratch" if slot == 0 else "results")


if __name__ == "__main__":
    # Inject only command execution. The real CLI, provider construction, command
    # serialization, durable request store and status/return paths run in each child.
    assert Path(orb.__file__).resolve().parent.parent == Path(os.environ["EXPECTED_ORB_SOURCE"])
    original_begin = OCIVolumeJournal.begin

    def checkpoint_then_interrupt(journal, *args, **kwargs):
        result = original_begin(journal, *args, **kwargs)
        if os.environ["VOLUME_TEST_CRASH"] == "worker":
            path = Path(os.environ["VOLUME_TEST_CLOUD"])
            state = json.loads(path.read_text(encoding="utf-8"))
            assert state["worker"]["id"] == WORKER and not state["volumes"]
            state["interrupted"] = True
            path.write_text(json.dumps(state), encoding="utf-8")
            os._exit(75)
        return result

    with (
        patch.object(OCIVolumeJournal, "begin", checkpoint_then_interrupt),
        patch.object(
            OCIComputeHandler,
            "__init__",
            partialmethod(OCIComputeHandler.__init__, run_command=cloud_response),
        ),
    ):
        cli_main()
