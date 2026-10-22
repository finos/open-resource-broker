"""OCI-only command execution with structured errors and private JSON input."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from orb.providers.oci.oci_cli_auth import build_oci_cli_env, build_oci_cli_extra_args


class OCICommandError(RuntimeError):
    """Safe diagnostics plus machine-readable failure classification."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        reason: str = "service",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.reason = reason

    @property
    def ambiguous(self) -> bool:
        return self.reason in {"timeout", "transport", "invalid_response"} or (
            self.status is not None and self.status >= 500
        )


def service_error_details(stderr: str) -> tuple[str | None, int | None]:
    raw = stderr.strip().removeprefix("ServiceError:").strip()
    try:
        error = json.loads(raw)
    except (ValueError, TypeError):
        return None, None
    if not isinstance(error, dict):
        return None, None
    code, status = error.get("code"), error.get("status")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", code):
        code = None
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    return code, status


def service_error_summary(stderr: str) -> str:
    code, status = service_error_details(stderr)
    parts = ([f"code={code}"] if code else []) + ([f"status={status}"] if status else [])
    return ": " + ", ".join(parts) if parts else ""


def _restrict_windows_file(path: str) -> None:
    """Replace inherited permissions with a protected, owner-only DACL."""
    from ctypes import wintypes

    # getattr: WinDLL is exposed only on Windows; this helper is called only there.
    load_library = getattr(ctypes, "WinDLL")
    security = load_library("advapi32", use_last_error=True)
    kernel = load_library("kernel32", use_last_error=True)
    convert = security.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
    ]
    convert.restype = wintypes.BOOL
    set_security = security.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    if not convert("D:P(A;;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise OSError("Could not create private OCI input permissions")
    try:
        if not set_security(path, 4, descriptor):  # DACL_SECURITY_INFORMATION
            raise OSError("Could not protect OCI command input")
    finally:
        kernel.LocalFree(descriptor)


@contextmanager
def private_json_input(payload: dict[str, Any]):
    # mkstemp creates a 0600 file on POSIX. On Windows apply an owner-only
    # DACL before writing any content; chmod alone does not restrict its ACL.
    fd, path = tempfile.mkstemp(prefix="orb-oci-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            if os.name == "nt":
                _restrict_windows_file(path)
            json.dump(payload, stream)
        yield path
    finally:
        Path(path).unlink(missing_ok=True)


def run_oci(
    args: list[str],
    *,
    region: str,
    profile: str | None = None,
    credential_source: str | None = None,
    auth_config: dict[str, Any] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    executable = shutil.which("oci")
    if executable is None and runner is None:
        raise OCICommandError(
            "OCI CLI is unavailable; install oci-cli and add oci to PATH", reason="unavailable"
        )
    cmd = [executable or "oci", *args, "--region", region]
    cmd.extend(build_oci_cli_extra_args(profile=profile, credential_source=credential_source))
    operation = " ".join(
        args[: next((i for i, arg in enumerate(args) if arg.startswith("--")), len(args))]
    )

    def execute(command: list[str]) -> dict[str, Any]:
        try:
            completed = (runner or subprocess.run)(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=build_oci_cli_env(auth_config or {}),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            raise OCICommandError(
                f"OCI CLI timed out during {operation} (120 seconds)", reason="timeout"
            ) from None
        except (OSError, subprocess.SubprocessError):
            raise OCICommandError(
                f"OCI CLI could not execute {operation}", reason="transport"
            ) from None
        if completed.returncode != 0:
            code, status = service_error_details(completed.stderr)
            raise OCICommandError(
                f"OCI CLI command failed ({completed.returncode}) during {operation}{service_error_summary(completed.stderr)}",
                code=code,
                status=status,
                reason="service" if code or status else "transport",
            )
        try:
            parsed = json.loads(completed.stdout) if completed.stdout.strip() else {}
            if not isinstance(parsed, dict):
                raise ValueError
            return parsed
        except (ValueError, TypeError):
            raise OCICommandError(
                f"OCI CLI returned invalid JSON during {operation}", reason="invalid_response"
            ) from None

    if payload is None:
        return execute(cmd)
    try:
        with private_json_input(payload) as path:
            return execute([*cmd, "--from-json", "file://" + path])
    except OSError:
        raise OCICommandError(
            "Could not create or remove private OCI command input", reason="input"
        ) from None
