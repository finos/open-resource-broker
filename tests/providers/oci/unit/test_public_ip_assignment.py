"""Public-IP choices survive OCI conversion and reach the signed VNIC body."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from tests.providers.oci.live.gate import _load_resolved_template
from tests.providers.oci.unit.test_command_execution import make_operation


@pytest.fixture
def oci_plugin():
    OCIPlugin.reset_for_testing()
    plugin = OCIPlugin()
    plugin.initialize_provider()
    plugin.register_services_with_di(MagicMock())
    yield plugin
    OCIPlugin.reset_for_testing()


@pytest.fixture
def template():
    return {
        "template_id": "public-ip-options",
        "provider_type": "oci",
        "image_id": "image",
        "machine_types": {"VM.Standard.E5.Flex": 1},
        "subnet_ids": ["subnet", "unused-subnet"],
        "security_group_ids": ["nsg-one", "nsg-two"],
        "compartment_id": "compartment",
    }


@pytest.mark.parametrize("value", [None, False, True])
def test_public_ip_survives_canonical_dto_json_round_trip(oci_plugin, template, value):
    template["public_ip_assignment"] = value
    model = OCITemplate.model_validate(template)
    serialized = TemplateDTOFactory().from_domain(model).to_dict()
    if value is None:
        assert "public_ip_assignment" not in serialized
    else:
        assert serialized["public_ip_assignment"] is value
    assert "public_ip_assignment" not in serialized["provider_config"]
    restored = OCITemplate.model_validate_json(json.dumps(serialized))

    payload = OCITemplateMapper.build_launch_payload(restored.model_dump(), "worker")

    expected_vnic = {"subnet_id": "subnet", "nsg_ids": ["nsg-one", "nsg-two"]}
    if value is not None:
        expected_vnic["assign_public_ip"] = value
        assert payload["create_vnic_details"]["assign_public_ip"] is value
    assert payload["create_vnic_details"] == expected_vnic


@pytest.mark.parametrize("options", [{}, {"public_ip_assignment": None}])
def test_unspecified_public_ip_leaves_the_subnet_default(template, options):
    payload = OCITemplateMapper.build_launch_payload({**template, **options}, "worker")

    assert payload["create_vnic_details"] == {
        "subnet_id": "subnet",
        "nsg_ids": ["nsg-one", "nsg-two"],
    }


@pytest.mark.parametrize("source", ["top_level", "configuration"])
def test_explicit_false_overrides_lower_priority_sources_without_mutation(template, source):
    template.update(
        provider_config={"public_ip_assignment": True},
        metadata={"public_ip_assignment": True},
    )
    if source == "top_level":
        template["public_ip_assignment"] = False
    else:
        template["configuration"] = {"public_ip_assignment": False}
    original = deepcopy(template)

    payload = OCITemplateMapper.build_launch_payload(template, "worker")

    assert payload["create_vnic_details"]["assign_public_ip"] is False
    assert template == original


@pytest.mark.parametrize("value", ["false", "true", 0, 1, 0.0, [], {}])
def test_non_boolean_public_ip_is_rejected_by_models_and_raw_mapper(template, value):
    with pytest.raises(ValidationError):
        OCITemplate.model_validate({**template, "public_ip_assignment": value})
    with pytest.raises(ValueError, match="public_ip_assignment"):
        OCITemplateMapper.build_launch_payload(
            {**template, "public_ip_assignment": value}, "worker"
        )


@pytest.mark.parametrize(
    ("default", "override", "expected"),
    [(True, None, True), (False, None, False), (True, False, False), (False, True, True)],
)
def test_provider_defaults_and_top_level_overrides_survive_saving_and_reloading(
    tmp_path, oci_plugin, template, default, override, expected
):
    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "provider": {
                    "providers": [
                        {
                            "name": "oci-test",
                            "type": "oci",
                            "enabled": True,
                            "config": {"credential_source": "instance_principal"},
                            "template_defaults": {"public_ip_assignment": default},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    if override is not None:
        template["public_ip_assignment"] = override
    catalog = tmp_path / "templates.json"
    catalog.write_text(json.dumps({"templates": [template]}), encoding="utf-8")

    resolved = _load_resolved_template(config_file, catalog, template["template_id"], "oci-test")
    model = OCITemplate.model_validate(resolved)
    serialized = TemplateDTOFactory().from_domain(model).to_dict()
    assert serialized["public_ip_assignment"] is expected
    # Reapply defaults to saved DTO output, including saved false against default true.
    catalog.write_text(json.dumps({"templates": [serialized]}), encoding="utf-8")
    reloaded = _load_resolved_template(config_file, catalog, template["template_id"], "oci-test")
    restored = OCITemplate.model_validate_json(json.dumps(reloaded))
    payload = OCITemplateMapper.build_launch_payload(restored.model_dump(), "worker")

    assert payload["create_vnic_details"]["assign_public_ip"] is expected


@pytest.mark.parametrize("value", [None, False, True])
@pytest.mark.parametrize("capacity_type", ["ondemand", "preemptible"])
@pytest.mark.asyncio
async def test_public_ip_choice_reaches_actual_signed_request_body(value, capacity_type):
    bodies = []
    paths = []

    def run(cmd, **kwargs):
        assert cmd[1] == "raw-request"
        path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
        paths.append(path)
        bodies.append(json.loads(path.read_text()))
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"200 OK","data":{"id":"instance","lifecycleState":"PROVISIONING"}}'
        )

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run)
    operation = make_operation()
    operation.parameters.update(
        capacity_type=capacity_type,
        public_ip_assignment=value,
        security_group_ids=["nsg-one", "nsg-two"],
    )

    result = await handler.create_instances(operation)

    assert result.success, result.error_message
    assert len(bodies) == 1
    expected_vnic = {"subnetId": "subnet", "nsgIds": ["nsg-one", "nsg-two"]}
    if value is not None:
        expected_vnic["assignPublicIp"] = value
        assert bodies[0]["createVnicDetails"]["assignPublicIp"] is value
    assert bodies[0]["createVnicDetails"] == expected_vnic
    assert ("preemptibleInstanceConfig" in bodies[0]) is (capacity_type == "preemptible")
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("value", [None, False, True])
@pytest.mark.asyncio
async def test_capacity_fallback_preserves_the_public_ip_and_existing_network(value):
    bodies = []

    def run(cmd, **kwargs):
        assert cmd[1] == "raw-request"
        path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
        bodies.append(json.loads(path.read_text()))
        if len(bodies) == 1:
            return subprocess.CompletedProcess(
                cmd, 0, '{"status":"400 Error","data":{"code":"OutOfHostCapacity"}}'
            )
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"200 OK","data":{"id":"instance","lifecycleState":"PROVISIONING"}}'
        )

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run)
    operation = make_operation()
    operation.parameters.update(
        capacity_type="preemptible",
        fallback_to_ondemand=True,
        public_ip_assignment=value,
        security_group_ids=["nsg-one", "nsg-two"],
    )

    result = await handler.create_instances(operation)

    assert result.success, result.error_message
    assert len(bodies) == 2
    assert "preemptibleInstanceConfig" in bodies[0]
    assert "preemptibleInstanceConfig" not in bodies[1]
    expected_vnic = {"subnetId": "subnet", "nsgIds": ["nsg-one", "nsg-two"]}
    if value is not None:
        expected_vnic["assignPublicIp"] = value
    assert all(body["createVnicDetails"] == expected_vnic for body in bodies)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, 0.0, [], {}])
@pytest.mark.asyncio
async def test_invalid_public_ip_cannot_reach_any_oci_call(value):
    runner = MagicMock(side_effect=AssertionError("Invalid public-IP option reached OCI"))
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)
    operation = make_operation()
    operation.parameters["public_ip_assignment"] = value

    result = await handler.create_instances(operation)

    assert not result.success
    assert result.error_code == "INVALID_TEMPLATE"
    assert "public_ip_assignment" in result.error_message
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_subnet_public_ip_rejection_is_a_launch_error_without_capacity_fallback():
    bodies = []

    def run(cmd, **kwargs):
        assert cmd[1] == "raw-request"
        path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
        bodies.append(json.loads(path.read_text()))
        return subprocess.CompletedProcess(
            cmd,
            0,
            json.dumps(
                {
                    "status": "400 Error",
                    "data": {
                        "code": "InvalidParameter",
                        "message": "Public IP assignment is prohibited on this subnet",
                    },
                }
            ),
        )

    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run)
    operation = make_operation()
    operation.parameters.update(
        public_ip_assignment=True, capacity_type="preemptible", fallback_to_ondemand=True
    )

    result = await handler.create_instances(operation)

    assert not result.success
    assert result.error_code == "OCI_LAUNCH_FAILED"
    assert "status=400, code=InvalidParameter" in result.error_message
    assert result.metadata["fallback_attempted"] is False
    assert result.metadata["launched_instance_ids"] == []
    assert len(bodies) == 1
    assert bodies[0]["createVnicDetails"]["assignPublicIp"] is True
