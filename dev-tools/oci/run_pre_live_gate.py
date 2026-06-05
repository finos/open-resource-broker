#!/usr/bin/env python3
"""Run OCI pre-live deployment gate checks through ORB CLI."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        raise RuntimeError(f"Command failed ({rc}): {' '.join(cmd)}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
    return _extract_json(out)


def _find_request(payload: dict[str, Any], request_id: str) -> dict[str, Any]:
    for req in payload.get("requests", []):
        if req.get("request_id") == request_id:
            return req
    raise ValueError(f"Request {request_id} not found in payload")


def _poll_request(
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
            ["requests", "show", "--request-id", request_id, "--provider", provider],
        )
        req = _find_request(payload, request_id)
        last_req = req
        status = str(req.get("status", "")).lower()
        if status in TERMINAL_OK | TERMINAL_FAIL:
            return req
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for request {request_id}. Last status: {last_req.get('status')}")


def _is_real_instance_ocid(instance_id: str) -> bool:
    return instance_id.startswith("ocid1.instance.") and ".mock" not in instance_id and "mock" not in instance_id


def _infer_region_from_ocid(ocid: str) -> str | None:
    match = re.match(r"^ocid1\.[^.]+\.oc1\.([a-z0-9-]+)\..+$", ocid)
    if match:
        return match.group(1)
    return None


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


def _verify_oci_cli_instance(
    instance_id: str,
    profile: str | None = None,
    credential_source: str | None = None,
) -> None:
    from orb.providers.oci.oci_cli_auth import build_oci_cli_extra_args

    cmd = ["oci", "compute", "instance", "get", "--instance-id", instance_id]
    region = _infer_region_from_ocid(instance_id)
    if region:
        cmd.extend(["--region", region])
    cmd.extend(
        build_oci_cli_extra_args(profile=profile, credential_source=credential_source)
    )
    rc, out, err = _run_command(cmd, timeout=120)
    if rc != 0:
        raise RuntimeError(
            f"OCI CLI verification failed for {instance_id}\nCMD: {' '.join(cmd)}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
        )


@dataclass
class CycleResult:
    count: int
    acquire_request_id: str
    acquire_status: str
    provider_type: str
    provider_api: str
    instance_ids: list[str] = field(default_factory=list)
    return_request_ids: list[str] = field(default_factory=list)
    return_statuses: list[str] = field(default_factory=list)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OCI pre-live deployment gate via ORB")
    parser.add_argument("--config", required=True, type=Path, help="Path to ORB config json")
    parser.add_argument("--template-file", required=True, type=Path, help="Path to OCI template json")
    parser.add_argument("--provider", default="oci-default", help="Provider instance name")
    parser.add_argument(
        "--template-id",
        default="oci-vm-flex-ondemand-small",
        help="Template ID",
    )
    parser.add_argument("--orb-bin", default="orb", help="ORB executable")
    parser.add_argument("--counts", default="1,2", help="Comma-separated machine counts (example: 1,2)")
    parser.add_argument("--poll-interval", type=int, default=5, help="Polling interval seconds")
    parser.add_argument("--timeout", type=int, default=420, help="Request poll timeout seconds")
    parser.add_argument("--skip-terminate", action="store_true", help="Skip terminate/return path")
    parser.add_argument(
        "--allow-mock-ocids",
        action="store_true",
        help="Allow mock instance IDs (not recommended for real deployment gate)",
    )
    parser.add_argument(
        "--verify-oci-cli",
        action="store_true",
        help="Verify each instance OCID exists via OCI CLI",
    )
    parser.add_argument(
        "--report-file",
        type=Path,
        default=None,
        help="Optional report file path (default: data/oci_pre_live_gate_<timestamp>.json)",
    )
    args = parser.parse_args()
    config_data = json.loads(args.config.read_text(encoding="utf-8"))
    provider_profile = None
    provider_credential_source = None
    for provider_entry in config_data.get("provider", {}).get("providers", []):
        if provider_entry.get("name") == args.provider:
            provider_cfg = provider_entry.get("config") or {}
            provider_profile = provider_cfg.get("profile")
            provider_credential_source = provider_cfg.get("credential_source")
            break

    counts = [int(x.strip()) for x in args.counts.split(",") if x.strip()]
    if not counts:
        raise ValueError("No counts provided")

    print("[1/3] Validate and sync template...")
    validate_payload = _run_orb_json(
        args.config,
        args.orb_bin,
        [
            "templates",
            "validate",
            "--template-id",
            args.template_id,
            "--file",
            str(args.template_file),
            "--provider",
            args.provider,
        ],
    )
    update_payload = _run_orb_json(
        args.config,
        args.orb_bin,
        [
            "templates",
            "update",
            "--template-id",
            args.template_id,
            "--file",
            str(args.template_file),
            "--provider",
            args.provider,
        ],
    )

    cycles: list[CycleResult] = []
    print("[2/3] Execute acquire/return cycles...")
    for count in counts:
        print(f"  - Acquire count={count}")
        acquire = _run_orb_json(
            args.config,
            args.orb_bin,
            [
                "machines",
                "request",
                "--template-id",
                args.template_id,
                "--count",
                str(count),
                "--provider",
                args.provider,
            ],
        )
        acquire_request_id = str(acquire.get("request_id") or "")
        if not acquire_request_id:
            raise RuntimeError(f"No request_id returned for count={count}: {acquire}")

        acquire_req = _poll_request(
            args.config,
            args.orb_bin,
            args.provider,
            acquire_request_id,
            args.poll_interval,
            args.timeout,
        )
        acquire_status = str(acquire_req.get("status", ""))
        if acquire_status.lower() not in TERMINAL_OK:
            raise RuntimeError(f"Acquire request {acquire_request_id} failed with status={acquire_status}")

        provider_type = str(acquire_req.get("provider_type", ""))
        provider_api = str(acquire_req.get("provider_api", ""))
        if provider_type.lower() != "oci" or provider_api != "OCICompute":
            raise RuntimeError(
                f"Cross-provider symptom for {acquire_request_id}: provider_type={provider_type}, provider_api={provider_api}"
            )

        instance_ids = _extract_instance_ids(acquire_req)
        if not instance_ids:
            raise RuntimeError(f"No machine/resource IDs found for request {acquire_request_id}")

        if not args.allow_mock_ocids:
            bad = [x for x in instance_ids if not _is_real_instance_ocid(x)]
            if bad:
                raise RuntimeError(f"Non-real/mocked instance IDs detected: {bad}")

        if args.verify_oci_cli:
            for instance_id in instance_ids:
                _verify_oci_cli_instance(
                    instance_id,
                    profile=provider_profile,
                    credential_source=provider_credential_source,
                )

        cycle = CycleResult(
            count=count,
            acquire_request_id=acquire_request_id,
            acquire_status=acquire_status,
            provider_type=provider_type,
            provider_api=provider_api,
            instance_ids=instance_ids,
        )

        if not args.skip_terminate:
            for instance_id in instance_ids:
                terminate = _run_orb_json(
                    args.config,
                    args.orb_bin,
                    [
                        "machines",
                        "terminate",
                        "--machine-id",
                        instance_id,
                        "--provider",
                        args.provider,
                        "--force",
                    ],
                )
                return_request_id = str(terminate.get("request_id") or "")
                if not return_request_id:
                    raise RuntimeError(f"No return request_id returned for instance {instance_id}: {terminate}")
                return_req = _poll_request(
                    args.config,
                    args.orb_bin,
                    args.provider,
                    return_request_id,
                    args.poll_interval,
                    args.timeout,
                )
                return_status = str(return_req.get("status", ""))
                if return_status.lower() not in TERMINAL_OK:
                    raise RuntimeError(
                        f"Return request {return_request_id} failed for {instance_id} status={return_status}"
                    )
                cycle.return_request_ids.append(return_request_id)
                cycle.return_statuses.append(return_status)

        cycles.append(cycle)

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "config_file": str(args.config),
        "template_file": str(args.template_file),
        "provider": args.provider,
        "template_id": args.template_id,
        "counts": counts,
        "validate_payload": validate_payload,
        "update_payload": update_payload,
        "cycles": [c.__dict__ for c in cycles],
        "decision": "go",
    }

    report_file = args.report_file
    if report_file is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = Path("data") / f"oci_pre_live_gate_{ts}.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print("[3/3] Completed successfully.")
    print(f"Report written: {report_file}")
    for cycle in cycles:
        print(
            f"  count={cycle.count} acquire={cycle.acquire_request_id}:{cycle.acquire_status} "
            f"returns={','.join(cycle.return_request_ids) if cycle.return_request_ids else 'skipped'}"
        )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
