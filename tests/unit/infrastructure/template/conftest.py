"""Fixtures for tests/unit/infrastructure/template/.

Ensures the AWS template extension is registered in the process-global
TemplateExtensionRegistry before any test in this directory runs,
regardless of xdist worker packing or collection order.

register_aws_extensions() is idempotent (guarded by has_extension("aws")),
so calling it here is always safe even when another test on the same worker
has already registered it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _register_aws_template_extension() -> None:
    """Register the AWS template extension for the entire template test session.

    Without this fixture, TestAWSTemplateRoundTrip tests fail when this file
    is assigned to an xdist worker that has not yet run any test that calls
    register_aws_extensions().  The TemplateDTOFactory.from_domain() path
    looks up the 'aws' extension class in TemplateExtensionRegistry to
    populate TemplateDTO.provider_config; if it finds nothing, provider_config
    is None and AWSTemplate.model_validate(dto.model_dump()) cannot promote
    fleet_role / fleet_type / etc. back onto the aggregate.
    """
    from orb.providers.aws.registration import register_aws_extensions

    register_aws_extensions()
