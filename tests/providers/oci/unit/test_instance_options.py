"""Instance metadata options survive OCI configuration and template conversion."""

import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.mapping import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from tests.providers.oci.live.gate import _load_resolved_template


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
        "template_id": "metadata-options",
        "provider_type": "oci",
        "image_id": "image",
        "machine_types": {"VM.Standard.E5.Flex": 1},
        "subnet_ids": ["subnet"],
        "compartment_id": "compartment",
    }


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize(
    "field", ["areLegacyImdsEndpointsDisabled", "are_legacy_imds_endpoints_disabled"]
)
def test_instance_options_survive_nested_config_dto_and_json_round_trip(
    oci_plugin, template, field, value
):
    template["provider_config"] = {"instance_options": {field: value}}
    model = OCITemplate.model_validate(template)
    serialized = TemplateDTOFactory().from_domain(model).to_dict()
    restored = OCITemplate.model_validate_json(json.dumps(serialized))

    payload = OCITemplateMapper.build_launch_payload(restored.model_dump(), "worker")

    assert payload["instance_options"] == {"areLegacyImdsEndpointsDisabled": value}
    assert payload["instance_options"]["areLegacyImdsEndpointsDisabled"] is value


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"instance_options": None},
        {"instance_options": {}},
        {"instance_options": {"areLegacyImdsEndpointsDisabled": None}},
    ],
)
def test_unspecified_instance_options_do_not_change_existing_launches(template, options):
    payload = OCITemplateMapper.build_launch_payload({**template, **options}, "worker")

    assert "instance_options" not in payload


@pytest.mark.parametrize(
    "options",
    [
        "true",
        True,
        [],
        {"areLegacyImdsEndpointsDisabled": "false"},
        {"areLegacyImdsEndpointsDisabled": 1},
        {"areLegacyEndpointsDisabled": True},
        {"areLegacyImdsEndpointsDisabled": True, "unsupportedOption": True},
    ],
)
def test_invalid_instance_options_are_rejected_by_models_and_mapper(template, options):
    with pytest.raises(ValidationError):
        OCITemplateExtensionConfig(instance_options=options)
    with pytest.raises(ValueError):
        OCITemplateMapper.build_launch_payload({**template, "instance_options": options}, "worker")


def test_explicit_false_overrides_lower_priority_options_without_mutating_input(template):
    template.update(
        instance_options={"areLegacyImdsEndpointsDisabled": False},
        provider_config={"instance_options": {"areLegacyImdsEndpointsDisabled": True}},
    )
    original = deepcopy(template)

    payload = OCITemplateMapper.build_launch_payload(template, "worker")

    assert payload["instance_options"] == {"areLegacyImdsEndpointsDisabled": False}
    assert template == original


@pytest.mark.parametrize("override", [None, False])
def test_provider_template_defaults_reach_launch_payload(tmp_path, oci_plugin, template, override):
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
                            "template_defaults": {
                                "instance_options": {"areLegacyImdsEndpointsDisabled": True}
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    if override is not None:
        template["instance_options"] = {"areLegacyImdsEndpointsDisabled": override}
    catalog = tmp_path / "templates.json"
    catalog.write_text(json.dumps({"templates": [template]}), encoding="utf-8")

    resolved = _load_resolved_template(config_file, catalog, template["template_id"], "oci-test")
    model = OCITemplate.model_validate(resolved)
    payload = OCITemplateMapper.build_launch_payload(
        TemplateDTOFactory().from_domain(model).to_dict(), "worker"
    )

    assert payload["instance_options"] == {
        "areLegacyImdsEndpointsDisabled": True if override is None else override
    }
