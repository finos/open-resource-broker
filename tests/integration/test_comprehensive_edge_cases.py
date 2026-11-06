"""AWSTemplate validation of launch template references."""

import pytest
from pydantic import ValidationError

from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.aws.domain.template.value_objects import ProviderApi

_BASE_FIELDS = {
    "template_id": "test-template",
    "provider_api": ProviderApi.SPOT_FLEET,
    "image_id": "ami-12345678",
    "machine_types": {"t2.micro": 1},
    "subnet_ids": ["subnet-12345678"],
    "security_group_ids": ["sg-12345678"],
}


@pytest.mark.parametrize(
    ("launch_template_id", "launch_template_version"),
    [
        pytest.param("lt-12345678", "3", id="existing-id-with-version"),
        pytest.param("lt-12345678", None, id="existing-id-without-version"),
        pytest.param("lt-12345678", "$Latest", id="existing-id-latest"),
        pytest.param("lt-12345678", "$Default", id="existing-id-default"),
        pytest.param(None, None, id="no-launch-template"),
    ],
)
def test_launch_template_reference_is_accepted(launch_template_id, launch_template_version):
    """Valid launch template references are kept as given."""
    template = AWSTemplate.model_validate(
        {
            **_BASE_FIELDS,
            "launch_template_id": launch_template_id,
            "launch_template_version": launch_template_version,
        }
    )

    assert template.launch_template_id == launch_template_id
    assert template.launch_template_version == launch_template_version


@pytest.mark.parametrize(
    "launch_template_version",
    [
        pytest.param("invalid-version", id="non-numeric"),
        pytest.param("0", id="zero"),
        pytest.param("-1", id="negative"),
    ],
)
def test_invalid_launch_template_version_is_rejected(launch_template_version):
    with pytest.raises(ValidationError, match="launch_template_version must be"):
        AWSTemplate.model_validate(
            {
                **_BASE_FIELDS,
                "launch_template_id": "lt-12345678",
                "launch_template_version": launch_template_version,
            }
        )
