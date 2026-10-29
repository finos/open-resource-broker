"""Exercise failure recovery without provisioning cloud resources."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def gate():
    from tests.providers.oci.live import gate

    return gate


def test_discovery_only_selects_this_runs_tag(gate, monkeypatch):
    monkeypatch.setattr(
        gate,
        "_run_oci_json",
        MagicMock(
            return_value={
                "data": [
                    {"id": "ours", "freeform-tags": {"orb_canary_run": "run-1"}},
                    {"id": "other-run", "freeform-tags": {"orb_canary_run": "run-2"}},
                    {"id": "unrelated", "freeform-tags": {}},
                ]
            }
        ),
    )
    assert [i["id"] for i in gate._owned_instances({}, "compartment", "run-1")] == ["ours"]


def test_polling_stops_on_nested_request_errors(gate, monkeypatch):
    run = MagicMock(
        return_value={
            "requests": [
                {"request_id": "request-1", "status": "unknown", "error": "missing fulfilment"}
            ]
        }
    )
    monkeypatch.setattr(gate, "_run_orb_json", run)
    with pytest.raises(RuntimeError, match="missing fulfilment"):
        gate._poll_request(Path("config"), "orb", "oci-test", "request-1", 1, 10)
    run.assert_called_once()


def test_failed_orb_return_uses_cloud_cleanup_and_keeps_gate_failed(gate, monkeypatch):
    monkeypatch.setattr(
        gate,
        "_owned_instances",
        MagicMock(return_value=[{"id": "ours", "lifecycle-state": "RUNNING"}]),
    )
    monkeypatch.setattr(gate, "_run_orb_json", MagicMock(side_effect=RuntimeError("return failed")))
    cloud = MagicMock(return_value={})
    monkeypatch.setattr(gate, "_run_oci_json", cloud)
    terminated = MagicMock()
    monkeypatch.setattr(gate, "_wait_terminated", terminated)
    args = SimpleNamespace(
        config=Path("config.json"), orb_bin="orb", provider="oci-test", timeout=10, poll_interval=1
    )
    result = gate._cleanup_owned(args, {}, "compartment", "run-1")
    assert result["fallback_ids"] == ["ours"]
    assert result["terminated_ids"] == ["ours"]
    assert result["errors"]
    assert cloud.call_args.args[1] == [
        "compute",
        "instance",
        "terminate",
        "--instance-id",
        "ours",
        "--preserve-boot-volume",
        "false",
        "--force",
    ]
    terminated.assert_called_once()


@pytest.mark.parametrize("failure", ["launch", "poll", "network"])
def test_failure_always_cleans_up_and_writes_hold_report(gate, monkeypatch, tmp_path, failure):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "provider": {
                    "providers": [
                        {"name": "oci-test", "type": "oci", "config": {"region": "us-phoenix-1"}}
                    ]
                }
            }
        )
    )
    catalog_path = tmp_path / "templates.json"
    catalog_path.write_text(
        json.dumps({"templates": [{"template_id": "oci-vm-flex-ondemand-small"}]})
    )
    original_catalog = catalog_path.read_bytes()
    report = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gate",
            "--config",
            str(config),
            "--provider",
            "oci-test",
            "--template-file",
            str(catalog_path),
            "--report-file",
            str(report),
        ],
    )
    monkeypatch.setattr(
        gate,
        "_load_resolved_template",
        MagicMock(
            return_value={
                "template_id": "oci-vm-flex-ondemand-small",
                "image_id": "image",
                "subnet_ids": ["subnet"],
                "compartment_id": "compartment",
                "machine_types": {"VM.Standard.E4.Flex": 1},
            }
        ),
    )
    monkeypatch.setattr(gate, "_owned_instances", MagicMock(return_value=[]))
    cleanup = MagicMock(return_value={"terminated_ids": [], "fallback_ids": [], "errors": []})
    monkeypatch.setattr(gate, "_cleanup_owned", cleanup)

    def orb(_config, _bin, args):
        from orb.cli.args import build_parser

        parser, _ = build_parser()
        parser.parse_args(args)
        if args[:2] == ["templates", "validate"]:
            return {"status": "validated", "validation_errors": []}
        if args[:2] == ["templates", "show"]:
            return json.loads(catalog_path.read_text())
        if args[:2] == ["machines", "request"]:
            if failure == "launch":
                raise RuntimeError("launch response lost")
            return {"request_id": "request-1"}
        return {"machines": [{"private_ip": None}]}

    monkeypatch.setattr(gate, "_run_orb_json", orb)
    poll = MagicMock(
        return_value={
            "status": "complete",
            "provider_type": "oci",
            "provider_api": "OCICompute",
            "machine_ids": ["ocid1.instance.oc1.phx.canary"],
        }
    )
    if failure == "poll":
        poll.side_effect = TimeoutError("poll timeout")
    monkeypatch.setattr(gate, "_poll_request", poll)
    assert gate.main() == 1
    cleanup.assert_called_once()
    payload = json.loads(report.read_text())
    assert payload["decision"] == "hold"
    assert payload["error"]
    expected = {
        "launch": "launch response lost",
        "poll": "poll timeout",
        "network": "private IP",
    }
    assert expected[failure] in payload["error"]
    assert "final_cleanup" in payload
    assert catalog_path.read_bytes() == original_catalog


@pytest.mark.parametrize("flag", ["--skip-terminate", "--allow-mock-ocids"])
def test_bypassing_lifecycle_checks_cannot_pass(gate, monkeypatch, flag):
    monkeypatch.setattr(
        sys, "argv", ["gate", "--config", "unused", "--template-file", "unused", flag]
    )
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2
