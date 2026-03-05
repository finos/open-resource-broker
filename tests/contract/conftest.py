"""Shared fixtures for contract tests.

Provides moto context, DI container, and scheduler strategy instances
for validating ORB output at integration boundaries.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from tests.utilities.reset_singletons import reset_all_singletons

REGION = "eu-west-2"

# ---------------------------------------------------------------------------
# Moto context
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def moto_aws():
    """Start moto mock_aws context for the duration of each test."""
    try:
        from moto import mock_aws
    except ImportError:
        pytest.skip("moto not installed")

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
    """Create a VPC, subnet, and security group in moto."""
    import boto3

    ec2 = boto3.client("ec2", region_name=REGION)
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24", AvailabilityZone=f"{REGION}a")
    subnet_id = subnet["Subnet"]["SubnetId"]
    sg = ec2.create_security_group(
        GroupName="contract-test-sg", Description="contract test SG", VpcId=vpc_id
    )
    sg_id = sg["GroupId"]
    return {"vpc_id": vpc_id, "subnet_ids": [subnet_id], "sg_id": sg_id}


# ---------------------------------------------------------------------------
# ORB config directory (hostfactory scheduler)
# ---------------------------------------------------------------------------


@pytest.fixture
def orb_config_dir_hf(tmp_path, moto_vpc_resources):
    """ORB config directory wired for the hostfactory scheduler."""
    return _write_orb_config(tmp_path, moto_vpc_resources, scheduler_type="hostfactory")


@pytest.fixture
def orb_config_dir_default(tmp_path, moto_vpc_resources):
    """ORB config directory wired for the default scheduler."""
    return _write_orb_config(tmp_path, moto_vpc_resources, scheduler_type="default")


def _write_orb_config(tmp_path: Path, vpc_resources: dict, scheduler_type: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    subnet_ids = vpc_resources["subnet_ids"]
    sg_id = vpc_resources["sg_id"]

    config_data = {
        "scheduler": {
            "type": scheduler_type,
            "config_root": str(config_dir),
        },
        "provider": {
            "providers": [
                {
                    "name": f"aws_contract_{REGION}",
                    "type": "aws",
                    "enabled": True,
                    "default": True,
                    "config": {"region": REGION, "profile": "default"},
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

    # Write a minimal templates file in the correct wire format for the scheduler
    templates_file = _build_templates_file(scheduler_type, subnet_ids, sg_id)
    with open(config_dir / "aws_templates.json", "w") as f:
        json.dump(templates_file, f, indent=2)

    os.environ["ORB_CONFIG_DIR"] = str(config_dir)
    yield config_dir
    os.environ.pop("ORB_CONFIG_DIR", None)


def _build_templates_file(scheduler_type: str, subnet_ids: list, sg_id: str) -> dict:
    """Build a minimal templates file in the correct wire format."""
    if scheduler_type == "hostfactory":
        return {
            "scheduler_type": "hostfactory",
            "templates": [
                {
                    "templateId": "contract-tpl-hf",
                    "maxNumber": 4,
                    "providerApi": "EC2Fleet",
                    "vmType": "t3.micro",
                    "imageId": "ami-12345678",
                    "subnetIds": subnet_ids,
                    "securityGroupIds": [sg_id],
                    "priceType": "ondemand",
                    "attributes": {
                        "type": ["String", "X86_64"],
                        "ncpus": ["Numeric", "2"],
                        "ncores": ["Numeric", "2"],
                        "nram": ["Numeric", "1024"],
                    },
                }
            ],
        }
    else:
        return {
            "scheduler_type": "default",
            "templates": [
                {
                    "template_id": "contract-tpl-default",
                    "max_instances": 4,
                    "provider_api": "EC2Fleet",
                    "instance_type": "t3.micro",
                    "image_id": "ami-12345678",
                    "subnet_ids": subnet_ids,
                    "security_group_ids": [sg_id],
                    "price_type": "ondemand",
                }
            ],
        }


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
# Bare strategy fixtures (no DI container — unit-level contract tests)
# ---------------------------------------------------------------------------


def _make_mock_logger() -> Any:
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def hf_strategy():
    """HostFactorySchedulerStrategy with no external dependencies."""
    from infrastructure.scheduler.hostfactory.hostfactory_strategy import (
        HostFactorySchedulerStrategy,
    )

    return HostFactorySchedulerStrategy(logger=_make_mock_logger())


@pytest.fixture
def default_strategy():
    """DefaultSchedulerStrategy with no external dependencies."""
    from infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    return DefaultSchedulerStrategy(logger=_make_mock_logger())


# ---------------------------------------------------------------------------
# Minimal RequestDTO builder
# ---------------------------------------------------------------------------


def make_request_dto(
    request_id: str = "req-00000000-0000-0000-0000-000000000001",
    status: str = "pending",
    machine_refs: list | None = None,
    request_type: str = "acquire",
) -> Any:
    """Build a minimal RequestDTO for formatter tests."""
    from datetime import datetime, timezone

    from application.request.dto import RequestDTO

    return RequestDTO(
        request_id=request_id,
        status=status,
        requested_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        machine_references=machine_refs or [],
        request_type=request_type,
    )


def make_machine_ref_dto(
    machine_id: str = "i-0abc1234def56789a",
    status: str = "running",
    result: str = "succeed",
    private_ip: str = "10.0.1.5",
) -> Any:
    """Build a minimal MachineReferenceDTO for formatter tests."""
    from application.request.dto import MachineReferenceDTO

    return MachineReferenceDTO(
        machine_id=machine_id,
        name=machine_id,
        result=result,
        status=status,
        private_ip_address=private_ip,
        launch_time=0,
        message="",
    )
