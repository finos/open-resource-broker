"""HostFactory template network fields: input mapping and output shape.

``subnetIds`` / ``securityGroupIds`` are neutral template fields that every
provider maps to its own network concept, and external HostFactory consumers
read them. These goldens pin the HostFactory spelling in both directions for
an AWS-shaped template and a non-AWS one.
"""

from __future__ import annotations

from typing import Any

import pytest

from orb.infrastructure.scheduler.hostfactory.hostfactory_strategy import (
    HostFactorySchedulerStrategy,
)

_CASES: dict[str, dict[str, Any]] = {
    "aws": {
        "input": {
            "templateId": "aws-1",
            "vmType": "t3.micro",
            "imageId": "ami-0123456789abcdef0",
            "maxNumber": 3,
            "subnetIds": ["subnet-1", "subnet-2"],
            "securityGroupIds": ["sg-1"],
            "priceType": "ondemand",
            "providerApi": "EC2Fleet",
        },
        "domain": {
            "template_id": "aws-1",
            "subnet_ids": ["subnet-1", "subnet-2"],
            "security_group_ids": ["sg-1"],
            "max_instances": 3,
            "provider_api": "EC2Fleet",
        },
        "output": {
            "templateId": "aws-1",
            "maxNumber": 3,
            "subnetId": "subnet-1,subnet-2",
            "securityGroupIds": ["sg-1"],
            "providerApi": "EC2Fleet",
        },
    },
    "non_aws": {
        "input": {
            "templateId": "az-1",
            "vmType": "Standard_D2s_v3",
            "imageId": "/subscriptions/s/images/img",
            "maxNumber": 2,
            "subnetIds": ["/subscriptions/s/subnets/default"],
            "securityGroupIds": ["/subscriptions/s/nsgs/web"],
            "priceType": "ondemand",
            "providerApi": "VMSS",
        },
        "domain": {
            "template_id": "az-1",
            "subnet_ids": ["/subscriptions/s/subnets/default"],
            "security_group_ids": ["/subscriptions/s/nsgs/web"],
            "max_instances": 2,
            "provider_api": "VMSS",
        },
        "output": {
            "templateId": "az-1",
            "maxNumber": 2,
            "subnetId": "/subscriptions/s/subnets/default",
            "securityGroupIds": ["/subscriptions/s/nsgs/web"],
            "providerApi": "VMSS",
        },
    },
}


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(_CASES))
def test_input_maps_network_fields_to_domain_names(case: str) -> None:
    dto = HostFactorySchedulerStrategy().parse_template_config(_CASES[case]["input"])
    dumped = dto.model_dump()
    for key, expected in _CASES[case]["domain"].items():
        assert dumped[key] == expected, key


@pytest.mark.unit
@pytest.mark.parametrize("case", sorted(_CASES))
def test_output_uses_hostfactory_network_spelling(case: str) -> None:
    strategy = HostFactorySchedulerStrategy()
    dto = strategy.parse_template_config(_CASES[case]["input"])
    template = strategy.format_templates_response([dto])["templates"][0]
    for key, expected in _CASES[case]["output"].items():
        assert template[key] == expected, key
