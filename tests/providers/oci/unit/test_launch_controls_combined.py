"""Saved launch controls reach actual CLI JSON after defaults and preflight."""

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
from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.oci.configuration.config import OCIProviderConfig
from orb.providers.oci.configuration.instance_options import preserve_launch_controls_overrides
from orb.providers.oci.configuration.template_extension import OCITemplateExtensionConfig
from orb.providers.oci.handlers.oci_compute_handler import OCIComputeHandler
from orb.providers.oci.provider_plugin import OCIPlugin
from orb.providers.registry import ProviderRegistry

AD = "test:PHX-AD-1"
SHAPE = "VM.Standard.E5.Flex"
IMDS = "areLegacyImdsEndpointsDisabled"


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("ORB_CONFIG_DIR", str(tmp_path))
    logger = MagicMock()
    extensions = TemplateExtensionRegistryAdapter()
    factory = TemplateFactory(extension_registry=extensions, logger=logger)
    OCIPlugin.reset_for_testing()
    plugin = OCIPlugin()
    plugin.initialize_provider(template_factory=factory)
    plugin.register_services_with_di(MagicMock())
    registry = ProviderRegistry()
    plugin.register_provider(registry, logger)

    async def save_reload(
        options, *, imds_default=True, public_ip=False, capacity_type="preemptible"
    ):
        config = ConfigurationManager(
            config_dict={
                "provider": {
                    "providers": [
                        {
                            "name": "oci-test",
                            "type": "oci",
                            "enabled": True,
                            "config": {
                                "credential_source": "instance_principal",
                                "launch_preflight": True,
                            },
                            "template_defaults": {
                                "instance_options": {IMDS: imds_default},
                                "public_ip_assignment": not public_ip,
                                "boot_volume_vpus_per_gb": 20,
                                "capacity_type": (
                                    "ondemand" if capacity_type == "preemptible" else "preemptible"
                                ),
                            },
                        }
                    ]
                }
            }
        )
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
        authored = {
            "template_id": "combined-controls",
            "provider_type": "oci",
            "provider_name": "oci-test",
            "machine_image": "image",
            "machine_types": {SHAPE: 1},
            "shape_config": {"ocpus": 4, "memoryInGBs": 32},
            "subnet_ids": ["subnet"],
            "security_group_ids": ["nsg-one", "nsg-two"],
            "compartment_id": "compartment",
            "public_ip_assignment": public_ip,
            "boot_volume_gbs": 200,
            "boot_volume_vpus_per_gb": 120,
            "capacity_type": capacity_type,
            "fallback_to_ondemand": True,
            "tags": {"workload": "combined-test"},
            "ssh_authorized_keys": "test-public-key",
            **options,
        }
        original = deepcopy(authored)
        model = factory.create_template(authored)
        dto = TemplateDTOFactory().from_domain(model)
        await manager.save_template(dto)
        saved_path = Path(scheduler.get_template_paths()[0])
        saved = json.loads(saved_path.read_text(encoding="utf-8"))["templates"][0]
        assert "instance_options" not in saved
        assert saved["provider_config"]["boot_volume_vpus_per_gb"] == 120
        loaded = await manager.load_templates(force_refresh=True)
        assert len(loaded) == 1
        assert authored == original
        return saved, loaded[0].to_dict()

    yield save_reload
    OCIPlugin.reset_for_testing()


class LaunchCLI:
    """Exercise CLI encoding and parse request files while they still exist."""

    def __init__(self, *, capacity_failure=False, malformed=False):
        self.calls = []
        self.bodies = []
        self.paths = []
        self.tokens = []
        self.capacity_failure = capacity_failure
        self.malformed = malformed

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd[1:])
        command = cmd[1:4]
        if cmd[1] == "raw-request":
            assert cmd[cmd.index("--http-method") + 1] == "POST"
            path = Path(cmd[cmd.index("--request-body") + 1].removeprefix("file://"))
            self.paths.append(path)
            self.bodies.append(json.loads(path.read_text(encoding="utf-8")))
            headers = json.loads(cmd[cmd.index("--request-headers") + 1])
            self.tokens.append(headers["opc-retry-token"])
            if self.capacity_failure and len(self.bodies) == 1:
                result = {"status": "400 Error", "data": {"code": "OutOfHostCapacity"}}
            else:
                result = {
                    "status": "200 OK",
                    "data": {
                        "id": f"instance-{len(self.bodies)}",
                        "lifecycleState": "PROVISIONING",
                    },
                }
        elif command == ["compute", "image", "get"]:
            result = {"data": {"id": "image", "lifecycle-state": "AVAILABLE"}}
        elif command == ["network", "subnet", "get"]:
            result = {
                "data": {
                    "id": "subnet",
                    "lifecycle-state": "AVAILABLE",
                    "availability-domain": None,
                }
            }
        elif command == ["iam", "availability-domain", "list"]:
            result = {"data": [{"name": AD}]}
        elif command == ["compute", "shape", "list"]:
            shape = {
                "shape": SHAPE,
                "is-flexible": True,
                "ocpu-options": {"min": 1, "max": 64},
                "memory-options": {
                    "min-in-g-bs": 1,
                    "max-in-g-bs": 1024,
                    "min-per-ocpu-in-gbs": 1,
                    "max-per-ocpu-in-gbs": 64,
                },
            }
            result = {"data": [shape, None] if self.malformed else [shape]}
        elif command == ["compute", "image-shape-compatibility-entry", "get"]:
            result = {"data": {"image-id": "image", "shape": SHAPE}}
        else:
            raise AssertionError(f"Unexpected OCI command: {cmd}")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(result))


async def launch(template, runner, *, count=2):
    config = OCIProviderConfig(credential_source="instance_principal", launch_preflight=True)
    handler = OCIComputeHandler(
        MagicMock(),
        config.region,
        credential_source=config.credential_source,
        auth_config=config.model_dump(),
        run_command=runner,
    )
    return await handler.create_instances(
        ProviderOperation(
            operation_type=ProviderOperationType.CREATE_INSTANCES,
            parameters={
                "template_config": template,
                "count": count,
                "request_id": "combined-controls-request",
            },
        )
    )


def assert_launch_controls(body, *, imds, public_ip):
    assert body["instanceOptions"] == {IMDS: imds}
    assert body["instanceOptions"][IMDS] is imds
    assert body["createVnicDetails"] == {
        "subnetId": "subnet",
        "nsgIds": ["nsg-one", "nsg-two"],
        "assignPublicIp": public_ip,
    }
    assert body["createVnicDetails"]["assignPublicIp"] is public_ip
    assert body["sourceDetails"] == {
        "sourceType": "image",
        "imageId": "image",
        "bootVolumeSizeInGBs": 200,
        "bootVolumeVpusPerGB": 120,
    }
    assert body["availabilityDomain"] == AD
    assert body["shapeConfig"] == {"ocpus": 4, "memoryInGBs": 32}
    assert body["metadata"] == {"ssh_authorized_keys": "test-public-key"}
    assert body["freeformTags"]["workload"] == "combined-test"


@pytest.mark.parametrize("imds", [False, True])
@pytest.mark.parametrize("public_ip", [False, True])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.asyncio
async def test_saved_controls_override_defaults_in_actual_preflight_batch(
    repository, imds, public_ip, nested
):
    options = {"instance_options": {IMDS: imds}}
    if nested:
        options = {"provider_config": options}
    saved, loaded = await repository(options, imds_default=not imds, public_ip=public_ip)
    assert saved["provider_config"]["instance_options"] == {
        "are_legacy_imds_endpoints_disabled": imds
    }
    runner = LaunchCLI()

    result = await launch(loaded, runner)

    assert result.success, result.error_message
    assert len(runner.bodies) == 2
    for body in runner.bodies:
        assert_launch_controls(body, imds=imds, public_ip=public_ip)
    assert sum(call[:3] == ["network", "subnet", "get"] for call in runner.calls) == 1
    assert sum(call[:3] == ["iam", "availability-domain", "list"] for call in runner.calls) == 1
    assert all(call[0] != "raw-request" for call in runner.calls[:5])
    assert len(set(runner.tokens)) == 2
    assert all(not path.exists() for path in runner.paths)


@pytest.mark.parametrize("imds", [False, True])
@pytest.mark.asyncio
async def test_capacity_fallback_retains_all_saved_launch_controls(repository, imds):
    saved, loaded = await repository({"instance_options": {IMDS: imds}}, imds_default=not imds)
    assert loaded["fallback_to_ondemand"] is True
    assert saved["provider_config"]["capacity_type"] == "preemptible"
    assert loaded["provider_config"]["capacity_type"] == "preemptible"
    runner = LaunchCLI(capacity_failure=True)

    result = await launch(loaded, runner)

    assert result.success, result.error_message
    assert len(runner.bodies) == 3
    for body in runner.bodies:
        assert_launch_controls(body, imds=imds, public_ip=False)
    first = deepcopy(runner.bodies[0])
    first.pop("preemptibleInstanceConfig")
    assert first == runner.bodies[1]
    assert "preemptibleInstanceConfig" in runner.bodies[2]
    assert len(set(runner.tokens)) == 3
    assert sum(call[:3] == ["compute", "shape", "list"] for call in runner.calls) == 1
    assert all(not path.exists() for path in runner.paths)


@pytest.mark.asyncio
async def test_malformed_preflight_with_saved_controls_never_posts_or_falls_back(repository):
    _, loaded = await repository({"instance_options": {IMDS: False}})
    runner = LaunchCLI(capacity_failure=True, malformed=True)

    result = await launch(loaded, runner)

    assert not result.success
    assert result.error_code == "OCI_PREFLIGHT_UNAVAILABLE"
    assert runner.bodies == []
    assert all(call[0] != "raw-request" for call in runner.calls)
    assert all(call[:2] != ["compute", "instance"] for call in runner.calls)


@pytest.mark.parametrize("imds_default", [False, True])
@pytest.mark.parametrize("unset", [None, {}, {IMDS: None}])
@pytest.mark.asyncio
async def test_saved_unset_metadata_options_inherit_provider_default(
    repository, unset, imds_default
):
    _, loaded = await repository({"instance_options": unset}, imds_default=imds_default)
    runner = LaunchCLI()

    result = await launch(loaded, runner, count=1)

    assert result.success, result.error_message
    assert_launch_controls(runner.bodies[0], imds=imds_default, public_ip=False)


@pytest.mark.parametrize("capacity_type", ["preemptible", "ondemand"])
@pytest.mark.parametrize("options", [None, {}])
@pytest.mark.asyncio
async def test_saved_capacity_overrides_opposite_default_with_unset_imds(
    repository, capacity_type, options
):
    saved, loaded = await repository({"instance_options": options}, capacity_type=capacity_type)
    assert saved["provider_config"]["capacity_type"] == capacity_type
    assert loaded["provider_config"]["capacity_type"] == capacity_type
    runner = LaunchCLI()

    result = await launch(loaded, runner, count=1)

    assert result.success, result.error_message
    assert_launch_controls(runner.bodies[0], imds=True, public_ip=False)
    assert ("preemptibleInstanceConfig" in runner.bodies[0]) is (capacity_type == "preemptible")


@pytest.mark.parametrize("field", ["capacity_type", "capacityType"])
@pytest.mark.parametrize("source", ["top", "configuration", "provider_config", "metadata"])
def test_capacity_row_override_retains_existing_source_and_alias_support(field, source):
    row = {field: "preemptible"} if source == "top" else {source: {field: "preemptible"}}
    lower = {"capacity_type": "ondemand"}
    result, _ = preserve_launch_controls_overrides(row=row, defaults=lower, merged={**lower, **row})
    assert result["capacity_type"] == "preemptible"


@pytest.mark.parametrize(
    "row,expected",
    [
        (
            {"capacityType": "ondemand", "provider_config": {"capacity_type": "preemptible"}},
            "ondemand",
        ),
        (
            {
                "configuration": {"capacity_type": "ondemand"},
                "provider_config": {"capacity_type": "preemptible"},
            },
            "ondemand",
        ),
        (
            {
                "configuration": {"capacityType": "preemptible"},
                "provider_config": {"capacity_type": "ondemand"},
            },
            "ondemand",
        ),
        (
            {"capacity_type": None, "provider_config": {"capacity_type": "preemptible"}},
            "preemptible",
        ),
    ],
)
def test_capacity_row_override_keeps_mapper_source_and_key_precedence(row, expected):
    result, _ = preserve_launch_controls_overrides(
        row=row, defaults={"capacity_type": "ondemand"}, merged=row
    )
    assert result["capacity_type"] == expected


@pytest.mark.parametrize("unset", [None, "", {}, []])
@pytest.mark.parametrize("nested", [False, True])
def test_unset_capacity_choice_inherits_lower_default(unset, nested):
    row = {"capacity_type": unset}
    if nested:
        row = {"provider_config": row}
    lower = {"capacity_type": "preemptible"}
    merged = {**lower, **row} if unset is not None else {**row, **lower}
    result, _ = preserve_launch_controls_overrides(row=row, defaults=lower, merged=merged)
    assert result["capacity_type"] == "preemptible"


@pytest.mark.parametrize("invalid", ["spot", "PREEMPTIBLE", True, False, 0, 1, ["preemptible"]])
def test_invalid_saved_capacity_is_validated_instead_of_masked_by_defaults(invalid):
    row = {"provider_config": {"capacity_type": invalid}}
    lower = {"capacity_type": "ondemand"}
    result, _ = preserve_launch_controls_overrides(row=row, defaults=lower, merged={**lower, **row})
    with pytest.raises(ValidationError):
        OCITemplateExtensionConfig.model_validate(result)


@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, 0.0, [], {}])
def test_saved_imds_override_stays_strict(invalid):
    with pytest.raises(ValidationError):
        preserve_launch_controls_overrides(
            row={"provider_config": {"instance_options": {IMDS: invalid}}},
            defaults={"instance_options": {IMDS: True}},
            merged={"instance_options": {IMDS: True}},
        )


def test_launch_row_hook_preserves_boot_helper_and_does_not_mutate_inputs():
    row = {"provider_config": {"instance_options": {IMDS: False}, "bootVolumeVpusPerGB": 120}}
    defaults = {"instance_options": {IMDS: True}, "boot_volume_vpus_per_gb": 20}
    merged = {**defaults, **row}
    original = deepcopy((row, defaults, merged))

    result, message = preserve_launch_controls_overrides(row=row, defaults=defaults, merged=merged)

    assert result["instance_options"] == {IMDS: False}
    assert result["boot_volume_vpus_per_gb"] == 120
    assert message is None
    assert (row, defaults, merged) == original
