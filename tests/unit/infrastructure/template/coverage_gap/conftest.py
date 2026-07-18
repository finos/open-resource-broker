"""Fixtures for template coverage_gap tests.

Registers AWS template extension so TemplateDTOFactory round-trips work in
any xdist worker that runs these tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _register_aws_ext_for_coverage_gap() -> None:
    from orb.providers.aws.registration import register_aws_extensions

    register_aws_extensions()
