"""Render-level checks for the Helm chart (skipped when helm, yq or jq is missing)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(
        any(shutil.which(tool) is None for tool in ("helm", "yq", "jq")),
        reason="helm, yq and jq are required",
    ),
]

CHART = Path(__file__).resolve().parents[3] / "deploy" / "helm" / "orb"
FIXTURES = sorted((CHART / "ci").glob("*-values.yaml"))
TAG = ["--set", "image.tag=0.0.0-ci"]


def render(*args: str) -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "orb", str(CHART), "--namespace", "orb", *TAG, *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def docker_json(docs: list[dict]) -> dict:
    cm = next(d for d in docs if d["kind"] == "ConfigMap" and "docker.json" in d.get("data", {}))
    return json.loads(cm["data"]["docker.json"])


def test_render_checks_script_passes():
    result = subprocess.run(
        ["bash", str(CHART / "ci" / "render-checks.sh")],
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda p: p.name)
def test_every_fixture_renders_a_config_without_a_wildcard_host(fixture):
    config = docker_json(render("-f", str(fixture)))
    hosts = [h.strip() for h in config["server"]["trusted_hosts"]]
    assert hosts and "*" not in hosts


def test_subdomain_wildcards_are_kept():
    config = docker_json(render("--set", "server.trustedHosts[0]=*.example.com"))
    assert "*.example.com" in config["server"]["trusted_hosts"]


def test_sql_storage_is_refused_without_driver_support():
    result = subprocess.run(
        ["helm", "template", "orb", str(CHART), *TAG, "--set", "storage.type=sql"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "requires an ORB image with PostgreSQL driver support" in result.stderr


def test_dynamodb_storage_has_no_pvc_and_names_its_tables():
    docs = render("-f", str(CHART / "ci" / "dynamodb-values.yaml"))
    assert not [d for d in docs if d["kind"] == "PersistentVolumeClaim"]
    config = docker_json(docs)
    assert config["storage"]["strategy"] == "dynamodb"
    assert config["provider"]["providers"][0]["config"]["storage"]["dynamodb"] == {
        "table_prefix": "orb-ci",
        "region": "eu-west-1",
    }
    volumes = {
        v["name"]: v
        for d in docs
        if d["kind"] == "Deployment"
        for v in d["spec"]["template"]["spec"]["volumes"]
    }
    assert "emptyDir" in volumes["orb-data"]


def test_the_api_service_selects_the_api_pod():
    docs = render()
    selector = next(d for d in docs if d["kind"] == "Service")["spec"]["selector"]
    labels = next(d for d in docs if d["kind"] == "Deployment")["spec"]["template"]["metadata"][
        "labels"
    ]
    assert all(labels.get(k) == v for k, v in selector.items())
