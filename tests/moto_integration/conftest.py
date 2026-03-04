"""Shared fixtures for moto-based full-pipeline integration tests."""

import json
import os
import shutil
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.utilities.reset_singletons import reset_all_singletons

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_CONFIG_SOURCE = _PROJECT_ROOT / "config"

REGION = "eu-west-2"


# ---------------------------------------------------------------------------
# Moto context
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def moto_aws():
    """Start moto mock_aws context for the duration of each test."""
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")  # nosec B105
    os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_SESSION_TOKEN", "testing")  # nosec B105
    os.environ.setdefault("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        yield


# ---------------------------------------------------------------------------
# VPC / subnet / SG resources
# ---------------------------------------------------------------------------


@pytest.fixture
def moto_vpc_resources(moto_aws):
    """Create a VPC, 2 subnets, and 1 security group in moto eu-west-2."""
    ec2 = boto3.client("ec2", region_name=REGION)

    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]

    subnet_a = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a"
    )
    subnet_b = ec2.create_subnet(
        VpcId=vpc_id, CidrBlock="10.0.2.0/24", AvailabilityZone=f"{REGION}b"
    )
    subnet_ids = [subnet_a["Subnet"]["SubnetId"], subnet_b["Subnet"]["SubnetId"]]

    sg = ec2.create_security_group(
        GroupName="orb-test-sg", Description="ORB moto integration test SG", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]

    return {"vpc_id": vpc_id, "subnet_ids": subnet_ids, "sg_id": sg_id}


# ---------------------------------------------------------------------------
# ORB config directory
# ---------------------------------------------------------------------------


@pytest.fixture
def orb_config_dir(tmp_path, moto_vpc_resources):
    """Generate a complete ORB config directory pointing at moto VPC resources.

    Writes config.json, aws_templates.json, and default_config.json into
    tmp_path/config/, sets ORB_CONFIG_DIR, and returns the config dir path.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    subnet_ids = moto_vpc_resources["subnet_ids"]
    sg_id = moto_vpc_resources["sg_id"]

    # --- config.json ---
    config_data = {
        "scheduler": {
            "type": "hostfactory",
            "config_root": str(config_dir),
        },
        "provider": {
            "providers": [
                {
                    "name": f"aws_moto_{REGION}",
                    "type": "aws",
                    "enabled": True,
                    "default": True,
                    "config": {
                        "region": REGION,
                        "profile": "default",
                    },
                    "template_defaults": {
                        "subnet_ids": subnet_ids,
                        "security_group_ids": [sg_id],
                    },
                }
            ]
        },
        "storage": {
            "strategy": "json",
            "default_storage_path": str(tmp_path / "data"),
            "json_strategy": {
                "storage_type": "single_file",
                "base_path": str(tmp_path / "data"),
                "filenames": {"single_file": "request_database.json"},
            },
        },
    }
    with open(config_dir / "config.json", "w") as f:
        json.dump(config_data, f, indent=2)

    # --- aws_templates.json ---
    # Load via the real scheduler pipeline (HF camelCase source → HF wire format)
    try:
        from tests.onaws.template_processor import TemplateProcessor

        templates_data = TemplateProcessor.generate_templates_programmatically("hostfactory")
    except Exception:
        # Fallback: copy the source file directly if programmatic generation fails
        src = _CONFIG_SOURCE / "aws_templates.json"
        if src.exists():
            shutil.copy2(src, config_dir / "aws_templates.json")
        templates_data = None

    if templates_data is not None:
        with open(config_dir / "aws_templates.json", "w") as f:
            json.dump(templates_data, f, indent=2)

    # --- default_config.json ---
    default_src = _CONFIG_SOURCE / "default_config.json"
    if default_src.exists():
        shutil.copy2(default_src, config_dir / "default_config.json")

    # Point ORB at this config directory
    os.environ["ORB_CONFIG_DIR"] = str(config_dir)

    yield config_dir

    # Cleanup env var after test
    os.environ.pop("ORB_CONFIG_DIR", None)


# ---------------------------------------------------------------------------
# Singleton reset
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset DI container and all singletons before and after each test."""
    from infrastructure.di.container import reset_container

    reset_container()
    reset_all_singletons()
    yield
    reset_container()
    reset_all_singletons()


# ---------------------------------------------------------------------------
# AWS clients
# ---------------------------------------------------------------------------


@pytest.fixture
def ec2_client(moto_aws):
    """boto3 EC2 client backed by moto, eu-west-2."""
    return boto3.client("ec2", region_name=REGION)


@pytest.fixture
def autoscaling_client(moto_aws):
    """boto3 AutoScaling client backed by moto, eu-west-2."""
    return boto3.client("autoscaling", region_name=REGION)
