"""OCI contracts exercised through the current shared ORB interfaces."""

import argparse
from importlib.metadata import entry_points
from unittest.mock import MagicMock

import pytest

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.template_example_generator_registry import (
    TemplateExampleGeneratorRegistry,
)
from orb.infrastructure.template.factories import TemplateDTOFactory
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.cli.oci_cli_spec import OCICLISpec
from orb.providers.oci.defaults_loader import OCIDefaultsLoader
from orb.providers.oci.domain.template import OCITemplate
from orb.providers.oci.mapping.template_mapper import OCITemplateMapper
from orb.providers.oci.provider_plugin import OCIPlugin
from orb.providers.oci.registration import create_oci_strategy, register_oci_template_factory
from orb.providers.oci.strategy.oci_provider_strategy import OCIProviderStrategy


@pytest.fixture
def plugin():
    OCIPlugin.reset_for_testing()
    plugin = OCIPlugin()
    plugin.initialize_provider()
    plugin.register_services_with_di(MagicMock())
    yield plugin
    OCIPlugin.reset_for_testing()


def test_installed_entry_point_discovers_oci():
    entries = entry_points(group="orb.providers")
    entry = next(ep for ep in entries if ep.name == "oci")
    entry.load()()
    from orb.providers.registry import get_provider_registry

    assert get_provider_registry().is_provider_registered("oci")


def test_plugin_populates_all_required_satellites(plugin):
    from orb.infrastructure.registry.cli_spec_registry import CLISpecRegistry
    from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
    from orb.infrastructure.scheduler.hostfactory.field_mapping_registry import FieldMappingRegistry
    from orb.providers.registry.defaults_loader_registry import DefaultsLoaderRegistry

    for registry in (
        CLISpecRegistry,
        TemplateExtensionRegistry,
        FieldMappingRegistry,
        DefaultsLoaderRegistry,
        TemplateExampleGeneratorRegistry,
    ):
        assert registry.get_or_none("oci") is not None


def test_defaults_do_not_enable_an_oci_account_implicitly():
    defaults = OCIDefaultsLoader().load_defaults()["provider"]
    assert "providers" not in defaults
    assert "OCICompute" in defaults["provider_defaults"]["oci"]["handlers"]


def test_class_level_credential_contract(monkeypatch):
    monkeypatch.setattr(
        "orb.providers.oci.strategy.oci_provider_strategy.run_oci",
        lambda *args, **kwargs: {"data": "namespace"},
    )
    for source in OCIProviderStrategy.get_available_credential_sources():
        assert isinstance(source["config_delta"], dict)
        result = OCIProviderStrategy.test_credentials(source["name"])
        assert result["success"], result
    assert (
        OCIProviderStrategy.generate_provider_name({"region": "us-ashburn-1", "profile": "TEAM"})
        == "oci_TEAM_us-ashburn-1"
    )


def test_cli_options_roundtrip_instance_principal():
    parser = argparse.ArgumentParser()
    spec = OCICLISpec()
    spec.add_arguments(parser)
    args = parser.parse_args(
        ["--oci-region", "us-ashburn-1", "--oci-credential-source", "instance_principal"]
    )
    assert spec.validate_add(args) == []
    assert OCIProviderStrategy.get_cli_provider_config(args) == {
        "region": "us-ashburn-1",
        "credential_source": "instance_principal",
    }
    assert spec.extract_partial_config(parser.parse_args([])) == {}


def test_template_factory_retains_oci_fields_after_early_initialization(plugin):
    factory = TemplateFactory()
    register_oci_template_factory(factory)
    template = factory.create_template(
        {
            "template_id": "oci-test",
            "provider_type": "oci",
            "machine_image": "ocid1.image.oc1..image",
            "machine_types": {"VM.Standard.E4.Flex": 1},
            "subnet_ids": ["ocid1.subnet.oc1..subnet"],
            "compartment_id": "ocid1.compartment.oc1..compartment",
            "shape_config": {"ocpus": 2, "memoryInGBs": 16},
            "capacity_type": "preemptible",
            "fallback_to_ondemand": True,
        }
    )
    assert isinstance(template, OCITemplate)
    dto = TemplateDTOFactory().from_domain(template)
    serialized = dto.to_dict()
    normalized = OCITemplateMapper.normalize_template_fields(serialized)
    assert OCITemplateMapper.validate_required_fields(serialized) == []
    assert normalized["shape_config"] == {"ocpus": 2, "memoryInGBs": 16}
    assert normalized["capacity_type"] == "preemptible"
    assert normalized["fallback_to_ondemand"] is True


def test_example_generation_uses_current_registry(plugin):
    generator = TemplateExampleGeneratorRegistry.get_or_none("oci")
    templates = generator.generate_example_templates("oci-team", "OCICompute")
    assert len(templates) == 25
    assert all(t["provider_name"] == "oci-team" for t in templates)
    assert generator.generate_example_templates("oci-team", "NotOCI") == []


@pytest.mark.asyncio
async def test_dry_run_never_invokes_installed_cli():
    strategy = create_oci_strategy({"credential_source": "instance_principal"})
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._run_oci = MagicMock(side_effect=AssertionError("dry run invoked OCI"))
    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 1,
                "template_config": {
                    "template_id": "test",
                    "machine_image": "ocid1.image.oc1..image",
                    "machine_type": "VM.Standard.E4.Flex",
                    "subnet_ids": ["subnet"],
                    "compartment_id": "compartment",
                },
            },
            context={"dry_run": True},
        )
    )
    assert result.success
    handler._run_oci.assert_not_called()


@pytest.mark.asyncio
async def test_live_launch_remains_pending_until_polled():
    strategy = create_oci_strategy({"credential_source": "instance_principal"})
    handler = strategy._compute_handler
    handler._oci_cli_available = True
    handler._run_oci = MagicMock(
        return_value={"data": {"id": "ocid1.instance.oc1..test", "lifecycle-state": "PROVISIONING"}}
    )
    result = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "count": 1,
                "template_config": {
                    "template_id": "test",
                    "image_id": "image",
                    "machine_type": "VM.Standard.E4.Flex",
                    "subnet_ids": ["subnet"],
                    "compartment_id": "compartment",
                    "availability_domain": "AD-1",
                },
            },
        )
    )
    assert result.success
    assert result.metadata["requires_async_polling"] is True
    handler._run_oci.return_value = {
        "data": {"id": "ocid1.instance.oc1..test", "lifecycle-state": "RUNNING"}
    }
    status = await strategy.execute_operation(
        ProviderOperation(
            operation_type=ProviderOperationType.DESCRIBE_RESOURCE_INSTANCES,
            parameters={"resource_ids": result.data["resource_ids"]},
        )
    )
    assert status.success
    assert status.data["instances"][0]["instance_id"] == "ocid1.instance.oc1..test"
    assert status.data["instances"][0]["status"] == "running"


@pytest.mark.parametrize("field", ["shape", "machine_type", "instance_type"])
def test_single_shape_survives_dto_conversion(plugin, field):
    template = OCITemplate(template_id="one", **{field: "VM.Standard.E4.Flex"})
    dto = TemplateDTOFactory().from_domain(template)
    assert (
        OCITemplateMapper.normalize_template_fields(dto.to_dict())["shape"] == "VM.Standard.E4.Flex"
    )


def test_explicit_api_key_auth_is_passed_to_cli_without_mutating_environment(monkeypatch):
    import os

    from orb.providers.oci.oci_cli_auth import build_oci_cli_env

    monkeypatch.setenv("OCI_CLI_USER", "ambient-user")
    env = build_oci_cli_env(
        {
            "user_ocid": "configured-user",
            "private_key_path": "key.pem",
            "tenancy_ocid": "tenancy",
            "fingerprint": "aa:bb",
        }
    )
    assert env["OCI_CLI_USER"] == "configured-user"
    assert env["OCI_CLI_KEY_FILE"] == "key.pem"
    assert env["OCI_CLI_TENANCY"] == "tenancy"
    assert env["OCI_CLI_FINGERPRINT"] == "aa:bb"
    assert os.environ["OCI_CLI_USER"] == "ambient-user"


def test_launch_passes_network_security_groups_to_cli():
    import json

    strategy = create_oci_strategy({"credential_source": "instance_principal"})
    handler = strategy._compute_handler
    handler._run_oci = MagicMock(return_value={"data": {"id": "ocid1.instance.oc1..test"}})
    handler._launch_with_cli(
        {
            "availability_domain": "AD-1",
            "compartment_id": "compartment",
            "shape": "VM.Standard.E4.Flex",
            "source_details": {"image_id": "image"},
            "create_vnic_details": {"subnet_id": "subnet", "nsg_ids": ["nsg-1"]},
        }
    )
    args = handler._run_oci.call_args.args[0]
    assert json.loads(args[args.index("--nsg-ids") + 1]) == ["nsg-1"]
    assert "--max-retries" not in args
    assert "--no-retry" not in args
    assert "--agent-config" not in args
    assert "--read-timeout" not in args


def test_pre_live_helper_resolves_one_catalog_entry(tmp_path, monkeypatch, plugin):
    import json

    from tests.providers.oci.live import gate as module

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
                                "image_id": "image",
                                "subnet_ids": ["subnet"],
                                "compartment_id": "compartment",
                            },
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    catalog = tmp_path / "templates.json"
    catalog.write_text(
        json.dumps(
            {
                "templates": [
                    {
                        "template_id": "oci-one",
                        "provider_type": "oci",
                        "machine_types": {"VM.Standard.E4.Flex": 1},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    resolved = module._load_resolved_template(config_file, catalog, "oci-one", "oci-test")
    assert resolved["template_id"] == "oci-one"
    assert resolved["provider_name"] == "oci-test"
    assert OCITemplateMapper.validate_required_fields(resolved) == []
