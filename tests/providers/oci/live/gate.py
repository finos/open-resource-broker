#!/usr/bin/env python3
"""Run OCI pre-live deployment gate checks through ORB CLI."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

TERMINAL_OK = {"complete", "completed"}
TERMINAL_FAIL = {"failed", "error", "cancelled", "canceled", "timeout", "partial"}


def _extract_json(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    end = stdout.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON payload found in output:\n{stdout}")
    return json.loads(stdout[start : end + 1])


def _run_command(args: list[str], timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _run_orb_json(cfg: Path, orb: str, orb_args: list[str], timeout: int = 300) -> dict[str, Any]:
    cmd = [orb, "--config", str(cfg), *orb_args]
    rc, out, err = _run_command(cmd, timeout=timeout)
    if rc != 0:
        raise RuntimeError(
            f"Command failed ({rc}): {' '.join(cmd)}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
        )
    payload = _extract_json(out)
    if payload.get("error") or payload.get("success") is False:
        raise RuntimeError(f"ORB command returned an error: {payload}")
    return payload


def _find_request(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    for req in payload.get("requests", []):
        if req.get("request_id") == request_id:
            return req
    raise ValueError(f"Request {request_id} not found in payload")


def _poll_request(  # noqa: PLR0917 - CLI polling inputs are explicit
    cfg: Path,
    orb: str,
    provider: str,
    request_id: str,
    poll_interval: int,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    last_req: dict[str, Any] = {}
    while time.time() < deadline:
        payload = _run_orb_json(
            cfg,
            orb,
            ["requests", "show", "--request-id", request_id, "--provider-name", provider],
        )
        req = _find_request(payload, request_id)
        last_req = req
        if req.get("error"):
            raise RuntimeError(f"ORB status failed for {request_id}: {req['error']}")
        status = str(req.get("status", "")).lower()
        if status in TERMINAL_OK | TERMINAL_FAIL:
            return req
        time.sleep(poll_interval)
    raise TimeoutError(
        f"Timed out waiting for request {request_id}. Last status: {last_req.get('status')}"
    )


def _is_real_instance_ocid(instance_id: str) -> bool:
    return (
        instance_id.startswith("ocid1.instance.")
        and ".mock" not in instance_id
        and "mock" not in instance_id
    )


def _extract_instance_ids(req: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("machine_ids", "resource_ids"):
        values = req.get(key) or []
        if isinstance(values, list):
            ids.extend(str(v) for v in values if isinstance(v, str))
    # keep order, remove duplicates
    seen: set[str] = set()
    deduped: list[str] = []
    for value in ids:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def _run_oci_json(config: dict[str, Any], args: list[str]) -> dict[str, Any]:
    from orb.providers.oci.oci_cli_auth import build_oci_cli_env, build_oci_cli_extra_args

    executable = shutil.which("oci")
    if not executable:
        raise RuntimeError("OCI CLI must be on PATH for independent verification and cleanup")
    cmd = [executable, *args, "--region", config["region"]]
    cmd.extend(
        build_oci_cli_extra_args(
            profile=config.get("profile"), credential_source=config.get("credential_source")
        )
    )
    proc = subprocess.run(
        cmd,
        env=build_oci_cli_env(config),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError(f"OCI verification/cleanup failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


def _owned_instances(config: dict, compartment: str, run_id: str) -> list[dict]:
    data = _run_oci_json(
        config, ["compute", "instance", "list", "--compartment-id", compartment, "--all"]
    ).get("data", [])
    return [
        item for item in data if (item.get("freeform-tags") or {}).get("orb_canary_run") == run_id
    ]


def _wait_terminated(config: dict, instance_id: str, timeout: int, interval: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        instance = _run_oci_json(
            config, ["compute", "instance", "get", "--instance-id", instance_id]
        ).get("data", {})
        if instance.get("lifecycle-state") == "TERMINATED":
            return
        time.sleep(interval)
    raise TimeoutError(f"OCI did not confirm termination of {instance_id}")


def _cleanup_owned(args: Any, config: dict, compartment: str, run_id: str) -> dict:
    """Recover only resources carrying this run's unique tag, including partial launches."""
    result: dict[str, Any] = {"terminated_ids": [], "fallback_ids": [], "errors": []}
    for instance in _owned_instances(config, compartment, run_id):
        instance_id = instance["id"]
        if instance.get("lifecycle-state") == "TERMINATED":
            result["terminated_ids"].append(instance_id)
            continue
        try:
            try:
                returned = _run_orb_json(
                    args.config,
                    args.orb_bin,
                    ["machines", "return", instance_id, "--provider-name", args.provider],
                )
                request_id = returned.get("request_id")
                if not request_id:
                    raise RuntimeError("ORB return did not provide a request ID")
                request = _poll_request(
                    args.config,
                    args.orb_bin,
                    args.provider,
                    request_id,
                    args.poll_interval,
                    args.timeout,
                )
                if str(request.get("status", "")).lower() not in TERMINAL_OK:
                    raise RuntimeError(f"ORB return failed: {request.get('status')}")
            except Exception as exc:
                result["errors"].append(f"ORB return for {instance_id}: {exc}")
                _run_oci_json(
                    config,
                    [
                        "compute",
                        "instance",
                        "terminate",
                        "--instance-id",
                        instance_id,
                        "--preserve-boot-volume",
                        "false",
                        "--force",
                    ],
                )
                result["fallback_ids"].append(instance_id)
            _wait_terminated(config, instance_id, args.timeout, args.poll_interval)
            result["terminated_ids"].append(instance_id)
        except Exception as exc:
            result["errors"].append(f"Cleanup for {instance_id}: {exc}")
    return result


def _load_resolved_template(
    config_file: Path, template_file: Path, template_id: str, provider: str
) -> dict[str, Any]:
    """Adapt a template catalog to the current CLI's single-template file contract."""
    from orb.application.services.template_defaults_service import TemplateDefaultsService
    from orb.config.manager import ConfigurationManager
    from orb.infrastructure.adapters.configuration_adapter import ConfigurationAdapter
    from orb.infrastructure.adapters.logging_adapter import LoggingAdapter
    from orb.infrastructure.registry.template_extension_registry import (
        TemplateExtensionRegistryAdapter,
    )
    from orb.providers.oci.registration import initialize_oci_provider

    data = json.loads(template_file.read_text(encoding="utf-8"))
    candidates = data.get("templates", [data])
    template = next(
        (
            item
            for item in candidates
            if (item.get("template_id") or item.get("templateId")) == template_id
        ),
        None,
    )
    if template is None:
        raise ValueError(f"Template {template_id!r} not found in {template_file}")
    initialize_oci_provider()
    logger = LoggingAdapter()
    config = ConfigurationAdapter(ConfigurationManager(config_file=str(config_file)), logger)
    defaults = TemplateDefaultsService(
        config, logger, extension_registry=TemplateExtensionRegistryAdapter()
    )
    return defaults.resolve_template_defaults(
        {**template, "provider_name": provider, "provider_type": "oci"}, provider
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCI pre-live deployment gate via ORB")
    parser.add_argument("--config", required=True, type=Path, help="Path to ORB config json")
    parser.add_argument(
        "--template-file", required=True, type=Path, help="Path to OCI template json"
    )
    parser.add_argument(
        "--provider-name",
        "--provider",
        dest="provider",
        default="oci-default",
        help="Provider instance name",
    )
    parser.add_argument(
        "--template-id",
        default="oci-vm-flex-ondemand-small",
        help="Template ID",
    )
    parser.add_argument("--orb-bin", default="orb", help="ORB executable")
    parser.add_argument(
        "--counts", default="1", help="Comma-separated machine counts (example: 1,2)"
    )
    parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval seconds")
    parser.add_argument("--timeout", type=int, default=420, help="Request poll timeout seconds")
    parser.add_argument("--skip-terminate", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--allow-mock-ocids",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--verify-oci-cli",
        action="store_true",
        help="Compatibility flag; independent OCI verification is always enabled",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Optional report file path (default: data/oci_pre_live_gate_<timestamp>.json)",
    )
    args = parser.parse_args()
    if args.skip_terminate or args.allow_mock_ocids:
        parser.error("A live lifecycle gate requires real instance IDs and verified cleanup")
    if args.poll_interval <= 0 or args.timeout <= 0:
        parser.error("Polling interval and timeout must be positive")
    counts = [int(x.strip()) for x in args.counts.split(",") if x.strip()]
    if not counts or any(count <= 0 for count in counts):
        parser.error("Counts must be positive")
    config_data = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if config_data.get("scheduler", {}).get("type", "default") != "default":
        parser.error(
            "Use a default-scheduler config for this gate; HostFactory needs its own acceptance test"
        )
    provider = next(
        (
            p
            for p in config_data.get("provider", {}).get("providers", [])
            if p.get("name") == args.provider and p.get("type") == "oci"
        ),
        None,
    )
    if provider is None:
        parser.error("The selected OCI provider is missing from the config")
    provider_config = provider.get("config") or {}
    if not provider_config.get("region"):
        parser.error("Set the provider region explicitly")
    resolved = _load_resolved_template(
        args.config, args.template_file, args.template_id, args.provider
    )
    from orb.providers.oci.mapping import OCITemplateMapper

    normalized = OCITemplateMapper.normalize_template_fields(resolved)
    missing = OCITemplateMapper.validate_required_fields(resolved)
    if missing:
        parser.error(f"Template deployment settings are incomplete: {missing}")
    compartment = normalized["compartment_id"]
    run_id = uuid4().hex
    resolved["tags"] = {**(resolved.get("tags") or {}), "orb_canary_run": run_id}
    report_file = args.report_file or Path("work") / f"oci_canary_{run_id}.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "provider": args.provider,
        "region": provider_config["region"],
        "template_id": args.template_id,
        "counts": counts,
        "cycles": [],
        "decision": "hold",
    }

    def save_report() -> None:
        report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    original_catalog = args.template_file.read_bytes()
    tagged_catalog = None
    save_report()
    try:
        # Prove credentials, compartment visibility, and inventory access before creating anything.
        _owned_instances(provider_config, compartment, run_id)
        with tempfile.TemporaryDirectory(prefix="orb-oci-gate-") as temporary:
            template_file = Path(temporary) / "template.json"
            template_file.write_text(json.dumps(resolved), encoding="utf-8")
            validation = _run_orb_json(
                args.config,
                args.orb_bin,
                [
                    "templates",
                    "validate",
                    "--file",
                    str(template_file),
                    "--provider-name",
                    args.provider,
                ],
            )
            if validation.get("validation_errors") or not (
                validation.get("valid") is True or validation.get("status") == "validated"
            ):
                raise RuntimeError(f"Template validation failed: {validation}")
            catalog = json.loads(original_catalog.decode("utf-8-sig"))
            entries = catalog["templates"] if isinstance(catalog, dict) else catalog
            selected = next(item for item in entries if item.get("template_id") == args.template_id)
            selected["tags"] = {**(selected.get("tags") or {}), "orb_canary_run": run_id}
            tagged_catalog = json.dumps(catalog, indent=2).encode("utf-8")
            args.template_file.write_bytes(tagged_catalog)
            shown = _run_orb_json(
                args.config,
                args.orb_bin,
                ["templates", "show", args.template_id, "--provider-name", args.provider],
            )
            if run_id not in json.dumps(shown):
                raise RuntimeError(
                    "The configured catalog does not load --template-file; no workers were requested"
                )
        for count in counts:
            cycle: dict[str, Any] = {"count": count}
            report["cycles"].append(cycle)
            save_report()
            acquired = _run_orb_json(
                args.config,
                args.orb_bin,
                [
                    "machines",
                    "request",
                    args.template_id,
                    str(count),
                    "--provider-name",
                    args.provider,
                ],
            )
            request_id = acquired.get("request_id")
            if not request_id:
                raise RuntimeError(f"ORB did not return an acquire request ID: {acquired}")
            cycle["acquire_request_id"] = request_id
            save_report()
            request = _poll_request(
                args.config,
                args.orb_bin,
                args.provider,
                request_id,
                args.poll_interval,
                args.timeout,
            )
            cycle["acquire_status"] = request.get("status")
            if str(request.get("status", "")).lower() not in TERMINAL_OK:
                raise RuntimeError(f"Acquire failed: {request.get('status')}")
            if request.get("provider_type") != "oci" or request.get("provider_api") != "OCICompute":
                raise RuntimeError("Acquire response has incorrect provider routing")
            instance_ids = _extract_instance_ids(request)
            cycle["instance_ids"] = instance_ids
            save_report()
            if len(instance_ids) != count or not all(
                _is_real_instance_ocid(i) for i in instance_ids
            ):
                raise RuntimeError("Acquire returned an incorrect count or non-real instance IDs")
            workers = _run_orb_json(
                args.config,
                args.orb_bin,
                ["machines", "list", "--request-id", request_id, "--provider-name", args.provider],
            ).get("machines", [])
            if len(workers) != count or not all(worker.get("private_ip") for worker in workers):
                raise RuntimeError("ORB did not return a private IP for every worker")
            cycle["workers"] = workers
            for instance_id in instance_ids:
                instance = _run_oci_json(
                    provider_config, ["compute", "instance", "get", "--instance-id", instance_id]
                ).get("data", {})
                if (instance.get("freeform-tags") or {}).get("orb_canary_run") != run_id:
                    raise RuntimeError("OCI instance does not belong to this canary run")
                if instance.get("lifecycle-state") != "RUNNING":
                    raise RuntimeError("OCI has not confirmed the worker is running")
            cleanup = _cleanup_owned(args, provider_config, compartment, run_id)
            cycle["cleanup"] = cleanup
            save_report()
            if cleanup["errors"] or not set(instance_ids).issubset(cleanup["terminated_ids"]):
                raise RuntimeError("ORB return or independently verified cleanup failed")
        report["decision"] = "go"
    except (Exception, KeyboardInterrupt) as exc:
        report["error"] = str(exc) or type(exc).__name__
    finally:
        # Discover tagged resources even if launch partially succeeded or a CLI response was lost.
        try:
            cleanup = _cleanup_owned(args, provider_config, compartment, run_id)
            report["final_cleanup"] = cleanup
            if cleanup["errors"] or cleanup["fallback_ids"]:
                report["decision"] = "hold"
        except Exception as exc:
            report["cleanup_error"] = str(exc)
            report["decision"] = "hold"
        if tagged_catalog is not None:
            if args.template_file.read_bytes() == tagged_catalog:
                args.template_file.write_bytes(original_catalog)
            else:
                report["catalog_restore_error"] = (
                    "Catalog changed during validation; restore manually"
                )
                report["decision"] = "hold"
        save_report()
    print(f"Decision: {report['decision']}; report: {report_file}")
    if report.get("error"):
        print(f"ERROR: {report['error']}", file=sys.stderr)
    return 0 if report["decision"] == "go" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
