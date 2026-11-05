"""The ``orb`` CLI as a real process against a throwaway config directory.

Each test starts ``python -m orb`` in a subprocess with a scrubbed environment
(no ORB_*, AWS_* or BRIDGE_* variables from the caller), a temporary HOME and
working directory, and a hard timeout.  Nothing here reaches a cloud API: the
commands only read configuration and the local JSON store.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.cli]

TIMEOUT_SECONDS = 60
UNKNOWN_REQUEST = "req-00000000-0000-0000-0000-000000000000"
TEMPLATE_ID = "cli-process-tpl"


@pytest.fixture
def orb_home(tmp_path: Path) -> Path:
    """Config and storage directories for one test; nothing is shared between tests."""
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    (tmp_path / "home").mkdir()

    config = {
        "scheduler": {"type": "default", "config_root": str(config_dir)},
        "provider": {
            "providers": [
                {
                    "name": "aws_cli_process",
                    "type": "aws",
                    "enabled": True,
                    "default": True,
                    "config": {"region": "eu-west-2", "profile": "default"},
                    "template_defaults": {
                        "subnet_ids": ["subnet-12345678"],
                        "security_group_ids": ["sg-12345678"],
                    },
                }
            ]
        },
        "storage": {
            "strategy": "json",
            "default_storage_path": str(data_dir),
            "json_strategy": {
                "storage_type": "single_file",
                "base_path": str(data_dir),
                "filenames": {"single_file": "request_database.json"},
            },
        },
    }
    templates = {
        "scheduler_type": "default",
        "templates": [
            {
                "template_id": TEMPLATE_ID,
                "max_instances": 4,
                "provider_api": "RunInstances",
                "instance_type": "t3.micro",
                "image_id": "ami-12345678",
                "subnet_ids": ["subnet-12345678"],
                "security_group_ids": ["sg-12345678"],
                "price_type": "ondemand",
            }
        ],
    }
    (config_dir / "config.json").write_text(json.dumps(config))
    (config_dir / "aws_templates.json").write_text(json.dumps(templates))
    return tmp_path


def _run_orb(orb_home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Build the environment from nothing so no ORB_*, AWS_* or BRIDGE_* value
    # (credentials, hook URLs, tokens) can leak into the child.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(orb_home / "home"),
        "ORB_CONFIG_DIR": str(orb_home / "config"),
        "PYTHONWARNINGS": "ignore",
    }
    return subprocess.run(
        [sys.executable, "-m", "orb", *args],
        env=env,
        cwd=orb_home,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        check=False,
    )


def _json_from(text: str) -> Any:
    start = min(i for i in (text.find("{"), text.find("[")) if i >= 0)
    return json.loads(text[start:])


def test_templates_list_prints_configured_templates(orb_home: Path) -> None:
    result = _run_orb(orb_home, "templates", "list")

    assert result.returncode == 0, result.stderr
    templates = _json_from(result.stdout)["templates"]
    assert [t["template_id"] for t in templates] == [TEMPLATE_ID]


def test_status_of_unknown_request_reports_not_found(orb_home: Path) -> None:
    result = _run_orb(orb_home, "requests", "status", UNKNOWN_REQUEST)

    assert result.returncode != 0
    entry = _json_from(result.stdout)["requests"][0]
    assert entry["request_id"] == UNKNOWN_REQUEST
    assert entry["not_found"] is True


def test_cancel_of_unknown_request_fails(orb_home: Path) -> None:
    result = _run_orb(orb_home, "requests", "cancel", UNKNOWN_REQUEST)

    assert result.returncode != 0
    error = _json_from(result.stderr)
    assert error["error"] is True
    assert error["type"] == "RequestNotFoundError"
    assert UNKNOWN_REQUEST in error["message"]


def test_list_returns_is_empty_on_fresh_storage(orb_home: Path) -> None:
    result = _run_orb(orb_home, "requests", "list-returns")

    assert result.returncode == 0, result.stderr
    assert _json_from(result.stdout) == {"return_requests": []}
