"""The installed CLI against a moto server: acquire, wait, return, wait."""

from __future__ import annotations

import zipfile

from tests.package_smoke.conftest import ON_DEMAND_TEMPLATE, SPOT_TEMPLATE, Flow, Smoke


def _wheel_version(wheel) -> str:
    with zipfile.ZipFile(wheel) as zf:
        metadata = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        for line in zf.read(metadata).decode().splitlines():
            if line.startswith("Version:"):
                return line.partition(":")[2].strip()
    raise AssertionError("the wheel METADATA has no Version")


def test_cli_reports_the_wheel_version(smoke: Smoke):
    out = smoke.run("--version").stdout
    assert _wheel_version(smoke.wheel) in out


def test_templates_include_run_instances_on_demand_and_spot(cli_flow: Flow):
    ids = {t["template_id"] for t in cli_flow.templates}
    assert {ON_DEMAND_TEMPLATE, SPOT_TEMPLATE} <= ids, sorted(ids)


def test_acquired_machine_exists_in_the_cloud_and_is_gone_after_return(
    smoke: Smoke, cli_flow: Flow
):
    """The ids ORB reported are the cloud's own, so the requests did reach the moto endpoint."""
    ec2 = smoke.ec2()
    reservations = ec2.describe_instances(InstanceIds=[cli_flow.machine_id])["Reservations"]
    instances = [i for r in reservations for i in r["Instances"]]
    assert [i["InstanceId"] for i in instances] == [cli_flow.machine_id]
    tags = {t["Key"]: t["Value"] for t in instances[0].get("Tags", [])}
    assert cli_flow.request_id in tags.values(), tags
    assert instances[0]["State"]["Name"] in ("shutting-down", "terminated")
