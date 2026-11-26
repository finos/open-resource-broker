"""Boot performance survives configuration and reaches the signed OCI request."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.config.manager import ConfigurationManager
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.adapters.configuration_adapter import ConfigurationAdapter
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistryAdapter
from orb.infrastructure.scheduler.default.default_strategy import DefaultSchedulerStrategy
from orb.infrastructure.template.configuration_manager import TemplateConfigurationManager
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.oci.configuration.boot_volume import preserve_boot_volume_performance_override
from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from orb.providers.registry import ProviderRegistry
from tests.providers.oci.live.gate import _load_resolved_template
from tests.providers.oci.unit.test_command_execution import make_operation

FIELDS = ("boot_volume_vpus_per_gb", "bootVolumeVpusPerGB")
INVALID_VALUES = [
    True,
    False,
    "20",
    "30",
    "",
    20.0,
    30.5,
    float("nan"),
    float("inf"),
    0,
    -10,
    9,
    11,
    19,
    21,
    29,
    121,
    [],
    {},
]


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
        "template_id": "boot-performance",
        "provider_type": "oci",
        "image_id": "image",
        "machine_types": {"VM.Standard.E5.Flex": 1},
        "subnet_ids": ["subnet"],
        "compartment_id": "compartment",
        "boot_volume_gbs": 200,
    }


@pytest.mark.parametrize("value", [10, 20, 30, 31, 119, 120])
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("nested", [False, True])
def test_boot_performance_survives_model_dto_and_json(oci_plugin, template, field, value, nested):
    if nested:
        template["provider_config"] = {field: value}
    else:
        template[field] = value
    original = deepcopy(template)

    model = OCITemplate.model_validate(template)
    serialized = TemplateDTOFactory().from_domain(model).to_dict()
    restored = OCITemplate.model_validate_json(json.dumps(serialized))
    payload = OCITemplateMapper.build_launch_payload(restored.model_dump(), "worker")

    assert model.boot_volume_vpus_per_gb == value
    assert payload["boot_volume_vpus_per_gb"] == value
    assert type(payload["boot_volume_vpus_per_gb"]) is int
    assert payload["boot_volume_gbs"] == 200
    assert payload["source_details"] == {"source_type": "image", "image_id": "image"}
    assert template == original


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("value", INVALID_VALUES)
def test_invalid_performance_rejected_by_extension_domain_and_mapper(template, field, value):
    with pytest.raises(ValidationError):
        OCITemplateExtensionConfig.model_validate({field: value})
    with pytest.raises(ValidationError):
        OCITemplate.model_validate({**template, "provider_config": {field: value}})
    with pytest.raises(ValueError):
        OCITemplateMapper.build_launch_payload({**template, field: value}, "worker")


@pytest.mark.parametrize("field", FIELDS)
def test_extension_defaults_preserve_alias_and_omit_unset(field):
    assert "boot_volume_vpus_per_gb" not in OCITemplateExtensionConfig().to_template_defaults()
    assert (
        OCITemplateExtensionConfig.model_validate({field: 20}).to_template_defaults()[
            "boot_volume_vpus_per_gb"
        ]
        == 20
    )


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("override", [None, 10])
@pytest.mark.parametrize("override_field", FIELDS)
@pytest.mark.parametrize("saved", [False, True])
def test_provider_defaults_and_template_override_reach_launch(
    tmp_path, oci_plugin, template, field, override, override_field, saved
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
                            "template_defaults": {field: 20},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    if override is not None:
        template[override_field] = override
    if saved:
        template = TemplateDTOFactory().from_domain(OCITemplate.model_validate(template)).to_dict()
        assert "boot_volume_vpus_per_gb" not in template
        assert template["provider_config"]["boot_volume_vpus_per_gb"] == override
    catalog = tmp_path / "templates.json"
    catalog.write_text(json.dumps({"templates": [template]}), encoding="utf-8")

    resolved = _load_resolved_template(config_file, catalog, template["template_id"], "oci-test")
    model = OCITemplate.model_validate(resolved)
    payload = OCITemplateMapper.build_launch_payload(
        TemplateDTOFactory().from_domain(model).to_dict(), "worker"
    )

    assert payload["boot_volume_vpus_per_gb"] == (20 if override is None else override)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.asyncio
async def test_saved_repository_override_wins_flat_defaults(
    tmp_path, monkeypatch, oci_plugin, template, field
):
    monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
    config = ConfigurationManager(
        config_dict={
            "provider": {
                "providers": [
                    {
                        "name": "oci-test",
                        "type": "oci",
                        "enabled": True,
                        "config": {"credential_source": "instance_principal"},
                        "template_defaults": {"boot_volume_vpus_per_gb": 20},
                    }
                ]
            }
        }
    )
    logger = MagicMock()
    extensions = TemplateExtensionRegistryAdapter()
    factory = TemplateFactory(extension_registry=extensions, logger=logger)
    factory.register_provider_template_class("oci", OCITemplate)
    registry = ProviderRegistry()
    oci_plugin.register_provider(registry, logger)
    defaults = TemplateDefaultsService(
        ConfigurationAdapter(config, logger),
        logger,
        template_factory=factory,
        extension_registry=extensions,
        provider_registry=registry,
    )
    scheduler = DefaultSchedulerStrategy(defaults, config_port=config, logger=logger)
    manager = TemplateConfigurationManager(
        config,
        scheduler,
        logger,
        template_defaults_service=defaults,
        template_factory=factory,
    )
    template[field] = 10
    dto = TemplateDTOFactory().from_domain(OCITemplate.model_validate(template))

    await manager.save_template(dto)
    saved_path = Path(scheduler.get_template_paths()[0])
    saved = json.loads(saved_path.read_text(encoding="utf-8"))["templates"][0]
    assert "boot_volume_vpus_per_gb" not in saved
    assert saved["provider_config"]["boot_volume_vpus_per_gb"] == 10
    loaded = await manager.load_templates(force_refresh=True)

    assert len(loaded) == 1
    assert loaded[0].to_dict()["provider_config"]["boot_volume_vpus_per_gb"] == 10
    assert (
        OCITemplateMapper.build_launch_payload(loaded[0].to_dict(), "worker")[
            "boot_volume_vpus_per_gb"
        ]
        == 10
    )


def test_row_hook_does_not_change_other_fields_or_inputs():
    row = {"provider_config": {"bootVolumeVpusPerGB": 10, "boot_volume_gbs": 50}}
    lower = {"boot_volume_vpus_per_gb": 20, "boot_volume_gbs": 200}
    merged = {**lower, **row}
    original = deepcopy((row, lower, merged))

    result, message = preserve_boot_volume_performance_override(
        row=row, defaults=lower, merged=merged
    )

    assert result == {**merged, "boot_volume_vpus_per_gb": 10}
    assert result["boot_volume_gbs"] == 200
    assert message is None
    assert (row, lower, merged) == original
    unchanged, _ = preserve_boot_volume_performance_override(row={}, defaults=lower, merged=merged)
    assert unchanged is merged


@pytest.fixture
def flat_performance_defaults(tmp_path):
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
                            "template_defaults": {"boot_volume_vpus_per_gb": 50},
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return config_file


def _assert_authored_precedence_round_trips(tmp_path, config_file, template, options, expected):
    authored = {**template, **options}
    original = deepcopy(authored)
    assert (
        OCITemplateMapper.build_launch_payload(authored, "worker").get("boot_volume_vpus_per_gb")
        == expected
    )
    model = OCITemplate.model_validate(authored)
    assert model.boot_volume_vpus_per_gb == expected
    saved = TemplateDTOFactory().from_domain(model).to_dict()
    assert (
        OCITemplateMapper.build_launch_payload(saved, "worker").get("boot_volume_vpus_per_gb")
        == expected
    )

    catalog = tmp_path / "templates.json"
    for row in (authored, saved):
        catalog.write_text(json.dumps({"templates": [row]}), encoding="utf-8")
        resolved = _load_resolved_template(config_file, catalog, row["template_id"], "oci-test")
        payload = OCITemplateMapper.build_launch_payload(resolved, "worker")
        restored = OCITemplate.model_validate(resolved)
        selected = 50 if expected is None else expected
        assert payload["boot_volume_vpus_per_gb"] == selected
        assert restored.boot_volume_vpus_per_gb == selected
    assert authored == original


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    "sources,expected",
    [
        ({"top": 10, "configuration": 20, "provider_config": 30, "metadata": 40}, 10),
        ({"configuration": 20, "provider_config": 30, "metadata": 40}, 20),
        ({"provider_config": 30, "metadata": 40}, 30),
        ({"metadata": 40}, 40),
        ({"top": None, "configuration": 20, "provider_config": 30}, 20),
        ({"configuration": None, "provider_config": 30, "metadata": 40}, 40),
        ({"configuration": None, "provider_config": 30}, None),
        ({"top": None, "configuration": None, "provider_config": None, "metadata": None}, None),
    ],
)
def test_authored_source_order_survives_defaults_and_saved_reload(
    tmp_path, flat_performance_defaults, oci_plugin, template, field, sources, expected
):
    options = {}
    for source, value in sources.items():
        if source == "top":
            options[field] = value
        else:
            options[source] = {field: value}
    _assert_authored_precedence_round_trips(
        tmp_path, flat_performance_defaults, template, options, expected
    )


@pytest.mark.parametrize(
    "options,expected",
    [
        (
            {
                "provider_config": {"boot_volume_vpus_per_gb": 10},
                "configuration": {"bootVolumeVpusPerGB": 20},
            },
            10,
        ),
        (
            {
                "provider_config": {"bootVolumeVpusPerGB": 10},
                "configuration": {"boot_volume_vpus_per_gb": 20},
            },
            20,
        ),
        ({"bootVolumeVpusPerGB": 10, "configuration": {"boot_volume_vpus_per_gb": 20}}, 10),
        ({"boot_volume_vpus_per_gb": None, "bootVolumeVpusPerGB": 10}, 10),
        (
            {
                "configuration": {"boot_volume_vpus_per_gb": None},
                "provider_config": {"bootVolumeVpusPerGB": 10},
            },
            10,
        ),
        (
            {
                "configuration": {"boot_volume_vpus_per_gb": None},
                "provider_config": {"boot_volume_vpus_per_gb": 30},
                "metadata": {"bootVolumeVpusPerGB": 20},
            },
            20,
        ),
    ],
)
def test_cross_alias_order_matches_existing_mapper_contract(
    tmp_path, flat_performance_defaults, oci_plugin, template, options, expected
):
    _assert_authored_precedence_round_trips(
        tmp_path, flat_performance_defaults, template, options, expected
    )


@pytest.mark.parametrize("field", FIELDS)
def test_alias_precedence_changes_no_other_fields(template, field):
    template.update(
        {
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "shape": "VM.Standard.E5.Flex",
            "ocpus": 2,
            "memory_gbs": 16,
            "provider_config": {"boot_volume_gbs": 50, "ocpus": 8, "memory_gbs": 96},
        }
    )
    baseline = OCITemplate.model_validate(template).model_dump()
    template[field] = 10
    template["provider_config"]["bootVolumeVpusPerGB"] = 120
    original = deepcopy(template)

    model = OCITemplate.model_validate(template)
    payload = OCITemplateMapper.build_launch_payload(template, "worker")

    assert model.boot_volume_vpus_per_gb == payload["boot_volume_vpus_per_gb"] == 10
    assert model.model_dump(exclude={"boot_volume_vpus_per_gb"}) == {
        k: v for k, v in baseline.items() if k != "boot_volume_vpus_per_gb"
    }
    assert template == original


def test_null_canonical_key_does_not_mask_alias(template):
    template.update(boot_volume_vpus_per_gb=None, bootVolumeVpusPerGB=20)
    assert OCITemplate.model_validate(template).boot_volume_vpus_per_gb == 20
    assert (
        OCITemplateMapper.build_launch_payload(template, "worker")["boot_volume_vpus_per_gb"] == 20
    )


def _capturing_handler(*, fail_first=False):
    bodies = []

    def run(cmd, **kwargs):
        assert cmd[1] == "raw-request"
        path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
        bodies.append(json.loads(path.read_text()))
        if fail_first and len(bodies) == 1:
            return subprocess.CompletedProcess(
                cmd, 0, '{"status":"400 Error","data":{"code":"OutOfHostCapacity"}}'
            )
        return subprocess.CompletedProcess(
            cmd, 0, '{"status":"200 OK","data":{"id":"instance","lifecycleState":"PROVISIONING"}}'
        )

    return OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=run), bodies


@pytest.mark.parametrize("value", [None, 10, 20, 31, 120])
@pytest.mark.parametrize("capacity_type", ["ondemand", "preemptible"])
@pytest.mark.asyncio
async def test_performance_reaches_actual_source_details_and_keeps_size(value, capacity_type):
    handler, bodies = _capturing_handler()
    operation = make_operation()
    operation.parameters.update(boot_volume_gbs=200, capacity_type=capacity_type)
    if value is not None:
        operation.parameters["boot_volume_vpus_per_gb"] = value

    result = await handler.create_instances(operation)

    assert result.success, result.error_message
    assert len(bodies) == 1
    expected_source = {"sourceType": "image", "imageId": "image", "bootVolumeSizeInGBs": 200}
    if value is not None:
        expected_source["bootVolumeVpusPerGB"] = value
    assert bodies[0]["sourceDetails"] == expected_source
    assert "bootVolumeVpusPerGB" not in bodies[0]
    assert ("preemptibleInstanceConfig" in bodies[0]) is (capacity_type == "preemptible")


@pytest.mark.asyncio
async def test_performance_does_not_set_boot_size_or_resize_denseio_nvme():
    handler, bodies = _capturing_handler()
    operation = make_operation()
    operation.parameters.update(
        instance_type="VM.DenseIO.E5.Flex", ocpus=8, memory_gbs=96, bootVolumeVpusPerGB=20
    )

    result = await handler.create_instances(operation)

    assert result.success, result.error_message
    assert bodies[0]["sourceDetails"] == {
        "sourceType": "image",
        "imageId": "image",
        "bootVolumeVpusPerGB": 20,
    }
    assert bodies[0]["shape"] == "VM.DenseIO.E5.Flex"
    assert bodies[0]["shapeConfig"] == {"ocpus": 8, "memoryInGBs": 96}


@pytest.mark.parametrize("value", [None, 20, 120])
@pytest.mark.asyncio
async def test_ondemand_fallback_keeps_performance_image_and_size(value):
    handler, bodies = _capturing_handler(fail_first=True)
    operation = make_operation()
    operation.parameters.update(
        capacity_type="preemptible",
        fallback_to_ondemand=True,
        boot_volume_gbs=200,
        boot_volume_vpus_per_gb=value,
    )

    result = await handler.create_instances(operation)

    assert result.success, result.error_message
    assert len(bodies) == 2
    assert "preemptibleInstanceConfig" in bodies[0]
    assert "preemptibleInstanceConfig" not in bodies[1]
    assert bodies[0]["sourceDetails"] == bodies[1]["sourceDetails"]
    assert bodies[0]["sourceDetails"]["bootVolumeSizeInGBs"] == 200
    if value is None:
        assert "bootVolumeVpusPerGB" not in bodies[0]["sourceDetails"]
    else:
        assert bodies[0]["sourceDetails"]["bootVolumeVpusPerGB"] == value


@pytest.mark.parametrize("value", INVALID_VALUES)
@pytest.mark.asyncio
async def test_invalid_performance_is_rejected_before_any_oci_call(value):
    runner = MagicMock(side_effect=AssertionError("Invalid performance reached OCI"))
    handler = OCIComputeHandler(MagicMock(), "us-phoenix-1", run_command=runner)
    operation = make_operation()
    operation.parameters["boot_volume_vpus_per_gb"] = value

    result = await handler.create_instances(operation)

    assert not result.success
    assert result.error_code == "INVALID_TEMPLATE"
    runner.assert_not_called()


def test_performance_does_not_invent_a_premium_price(template):
    baseline = OCITemplateMapper.estimate_hourly_cost(template)
    assert (
        OCITemplateMapper.estimate_hourly_cost({**template, "boot_volume_vpus_per_gb": 120})
        == baseline
    )
