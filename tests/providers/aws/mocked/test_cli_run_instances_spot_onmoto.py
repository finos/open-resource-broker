"""The default configuration accepts and launches a spot RunInstances template."""

import sys
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.providers.aws.mocked import test_cli_cancel_onmoto as _cli

_run_cli = _cli._run_cli
initialize = _cli.initialize  # the moto-backed Application.initialize fixture

pytestmark = [pytest.mark.moto, pytest.mark.cli]


def test_runinstances_spot_template_launches_spot_with_the_default_config(initialize, moto_aws):
    from orb.bootstrap import Application

    with patch.object(Application, "initialize", initialize):
        result, exit_code = _run_cli(
            ["machines", "request", "--template", "RunInstances-Spot", "--count", "1"]
        )

    assert "does not support spot" not in str(result), result
    assert exit_code == 0, result
    reservations = boto3.client("ec2", region_name="eu-west-2").describe_instances()["Reservations"]
    lifecycles = {i.get("InstanceLifecycle") for r in reservations for i in r["Instances"]}
    assert lifecycles == {"spot"}
