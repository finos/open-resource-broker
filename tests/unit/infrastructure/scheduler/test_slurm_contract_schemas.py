"""SLURM-specific JSON schema contract tests.

Covers open-resource-broker-2706.3: `expected_*_schema_slurm` in
plugin_io_schemas.py were plain aliases of the default-scheduler schemas and
asserted nothing SLURM-specific (partition_name, node_list, max_instances,
node_name-keyed machines, etc.), so contract drift in SLURM-specific fields
would go undetected. These tests validate real SlurmSchedulerStrategy output
against dedicated SLURM schemas.
"""

from datetime import datetime, timezone

import pytest
from jsonschema import validate as validate_json_schema

from orb.application.request.dto import MachineReferenceDTO, RequestDTO
from orb.infrastructure.scheduler.slurm.slurm_strategy import SlurmSchedulerStrategy
from orb.infrastructure.template.dtos import TemplateDTO
from tests.providers.aws.live.plugin_io_schemas import (
    expected_get_available_templates_schema_default,
    expected_get_available_templates_schema_slurm,
    expected_request_machines_schema_default,
    expected_request_machines_schema_slurm,
    expected_request_status_schema_default,
    expected_request_status_schema_slurm,
)


@pytest.fixture
def strategy() -> SlurmSchedulerStrategy:
    return SlurmSchedulerStrategy()


# ---------------------------------------------------------------------------
# Sanity: SLURM schemas are genuinely distinct from "default" (not aliases)
# ---------------------------------------------------------------------------


def test_slurm_template_schema_is_not_an_alias_of_default():
    assert (
        expected_get_available_templates_schema_slurm
        is not expected_get_available_templates_schema_default
    )
    assert (
        expected_get_available_templates_schema_slurm
        != expected_get_available_templates_schema_default
    )


def test_slurm_request_machines_schema_is_not_an_alias_of_default():
    assert expected_request_machines_schema_slurm is not expected_request_machines_schema_default
    assert expected_request_machines_schema_slurm != expected_request_machines_schema_default


def test_slurm_request_status_schema_is_not_an_alias_of_default():
    assert expected_request_status_schema_slurm is not expected_request_status_schema_default
    assert expected_request_status_schema_slurm != expected_request_status_schema_default


def test_slurm_template_schema_requires_slurm_specific_fields():
    """partition_name/max_instances are SLURM-specific — absent from "default"."""
    required = expected_get_available_templates_schema_slurm["properties"]["templates"]["items"][
        "required"
    ]
    assert "partition_name" in required
    assert "max_instances" in required

    default_required = expected_get_available_templates_schema_default["properties"]["templates"][
        "items"
    ]["required"]
    assert "partition_name" not in default_required
    assert "max_instances" not in default_required


def test_slurm_request_status_schema_covers_full_domain_status_vocabulary():
    """SLURM passes every domain RequestStatus value through unchanged (including
    "acquiring"/"partial_pending"), unlike "default"'s narrower enum — a genuine
    SLURM-specific contract point even though the two schemas share machine shape."""
    status_enum = expected_request_status_schema_slurm["properties"]["requests"]["items"][
        "properties"
    ]["status"]["enum"]
    default_status_enum = expected_request_status_schema_default["properties"]["requests"]["items"][
        "properties"
    ]["status"]["enum"]

    assert "acquiring" in status_enum
    assert "acquiring" not in default_status_enum


# ---------------------------------------------------------------------------
# Real strategy output validates against the SLURM-specific schema
# ---------------------------------------------------------------------------


def test_format_templates_response_matches_slurm_schema(strategy):
    templates = [
        TemplateDTO(
            template_id="gpu",
            max_instances=4,
            machine_types={"p3.2xlarge": 1},
            is_active=True,
        ),
        TemplateDTO(
            template_id="compute",
            max_instances=10,
            machine_types={"c5.xlarge": 1},
            is_active=True,
        ),
    ]

    response = strategy.format_templates_response(templates)

    validate_json_schema(instance=response, schema=expected_get_available_templates_schema_slurm)


def test_format_request_response_matches_slurm_schema(strategy):
    response = strategy.format_request_response(
        {
            "request_id": None,
            "status": "pending",
            "message": "Provisioning 2 nodes for partition gpu",
        }
    )

    validate_json_schema(instance=response, schema=expected_request_machines_schema_slurm)


def test_handle_resume_request_response_matches_slurm_schema(strategy):
    class _FakeClient:
        def get_node(self, node_name):
            return {"Partitions": "gpu"}

    strategy._slurm_client = _FakeClient()

    response = strategy.handle_resume_request(["gpu-001", "gpu-002"])

    validate_json_schema(instance=response, schema=expected_request_machines_schema_slurm)


def test_format_request_status_response_matches_slurm_schema(strategy):
    machine_ref = MachineReferenceDTO(
        machine_id="i-0abc123",
        name="compute-001",
        result="succeed",
        status="running",
        instance_type="c5.xlarge",
        private_ip_address="10.0.1.5",
    )
    dto = RequestDTO(
        request_id="req-abc",
        status="complete",
        requested_count=1,
        created_at=datetime.now(timezone.utc),
        machine_references=[machine_ref],
    )

    response = strategy.format_request_status_response([dto])

    validate_json_schema(instance=response, schema=expected_request_status_schema_slurm)


def test_format_request_status_response_passes_through_acquiring_status(strategy):
    """ "acquiring" isn't in default's enum but is a valid domain RequestStatus
    SLURM must pass through unchanged (see test_slurm_contract.py)."""
    dto = RequestDTO(
        request_id="req-xyz",
        status="acquiring",
        requested_count=1,
        created_at=datetime.now(timezone.utc),
    )

    response = strategy.format_request_status_response([dto])

    validate_json_schema(instance=response, schema=expected_request_status_schema_slurm)


def test_default_scheduler_template_response_fails_slurm_schema():
    """Cross-check: a default-scheduler-shaped template response is rejected
    by the SLURM-specific schema (proving the schemas are not equivalent)."""
    from jsonschema import ValidationError

    from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy

    default_strategy = DefaultSchedulerStrategy()
    response = default_strategy.format_templates_response(
        [TemplateDTO(template_id="tpl-1", max_instances=5, subnet_ids=["subnet-1"])]
    )

    with pytest.raises(ValidationError):
        validate_json_schema(
            instance=response, schema=expected_get_available_templates_schema_slurm
        )
