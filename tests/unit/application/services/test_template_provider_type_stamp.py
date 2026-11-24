"""Untyped template rows take the provider type of the instance they resolve against."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.domain.template.factory import TemplateFactory
from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate
from orb.providers.azure.domain.template.azure_template_aggregate import AzureTemplate
from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.oci.domain.template import OCITemplate


def _service(
    instances: dict[str, str] | None = None,
    registered: set[str] | None = None,
    registry: Any = "build",
    config_error: bool = False,
    active: list[str] | None = None,
) -> TemplateDefaultsService:
    """Service whose config lists ``instances`` (name -> type)."""
    config = MagicMock()
    config.get_template_config.return_value = {}
    provider_config = MagicMock(provider_defaults={})
    provider_config.providers = [
        SimpleNamespace(name=n, type=t, template_defaults={}, enabled=True)
        for n, t in (instances or {}).items()
    ]
    provider_config.get_active_providers.return_value = [
        p for p in provider_config.providers if p.name in (active or instances or {})
    ]
    if config_error:
        config.get_provider_config.side_effect = RuntimeError("boom")
    else:
        config.get_provider_config.return_value = provider_config
    if registry == "build":
        registry = MagicMock()
        registry.is_provider_registered.side_effect = lambda t: t in (registered or set())
        registry.get_default_api.return_value = None
        registry.get_template_row_migration.return_value = None
    return TemplateDefaultsService(
        config_manager=config, logger=MagicMock(), provider_registry=registry
    )


ROW = {"template_id": "t1", "provider_api": "EC2Fleet"}


class TestStamp:
    def test_row_pointing_at_inactive_instance_is_not_stamped(self):
        service = _service({"aws-prod": "aws", "k8s-b": "k8s"}, active=["aws-prod"])
        row = {**ROW, "provider_name": "k8s-b"}
        assert "provider_type" not in service.resolve_template_defaults(row, "aws-prod")

    def test_inactive_row_instance_still_loads_through_the_filter(self):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )
        from orb.infrastructure.template.template_cache_service import (
            create_template_cache_service,
        )

        service = _service({"aws-prod": "aws", "k8s-b": "k8s"}, active=["aws-prod"])
        provider_config = service.config_manager.get_provider_config()  # type: ignore[attr-defined]
        row = service.resolve_template_defaults({**ROW, "provider_name": "k8s-b"}, "aws-prod")
        config = ConfigurationManager(config_dict={})
        config.get_provider_config = MagicMock(return_value=provider_config)  # type: ignore[method-assign]
        logger = MagicMock()
        manager = TemplateConfigurationManager(
            config_manager=config,
            scheduler_strategy=MagicMock(),
            logger=logger,
            cache_service=create_template_cache_service("noop", logger),
        )
        assert manager._filter_templates_by_active_providers([row]) == [row]

    def test_override_instance_type_is_stamped_although_inactive(self):
        service = _service({"aws-prod": "aws", "k8s-b": "k8s"}, active=["aws-prod"])
        out = service.resolve_template_defaults(dict(ROW), "k8s-b")
        assert out["provider_type"] == "k8s"

    def test_untyped_row_takes_configured_instance_type(self):
        out = _service({"aws-prod": "aws"}).resolve_template_defaults(dict(ROW), "aws-prod")
        assert out["provider_type"] == "aws"

    def test_declared_type_wins_over_instance(self):
        row = {**ROW, "provider_type": "k8s"}
        out = _service({"aws-prod": "aws"}).resolve_template_defaults(row, "aws-prod")
        assert out["provider_type"] == "k8s"

    def test_no_instance_no_stamp(self):
        out = _service({"aws-prod": "aws"}).resolve_template_defaults(dict(ROW), None)
        assert "provider_type" not in out

    def test_camel_case_only_row_gets_snake_case_type(self):
        row = {**ROW, "providerType": "aws"}
        out = _service({"aws-prod": "aws"}).resolve_template_defaults(row, None)
        assert out["provider_type"] == "aws"

    def test_camel_case_row_of_inactive_type_is_not_stamped(self):
        row = {**ROW, "providerType": "azure"}
        out = _service({"aws-prod": "aws"}).resolve_template_defaults(row, None)
        assert "provider_type" not in out

    def test_instance_prefix_of_registered_type_is_stamped(self):
        out = _service({"aws-prod": "aws"}, registered={"aws"}).resolve_template_defaults(
            dict(ROW), "aws-prod-2"
        )
        assert out["provider_type"] == "aws"

    @pytest.mark.parametrize("name", ["default", "My_Cluster", "Prod-East", "aws-prod-2"])
    def test_unknown_instance_is_never_guessed(self, name):
        out = _service({"aws-prod": "aws"}, registered=set()).resolve_template_defaults(
            dict(ROW), name
        )
        assert "provider_type" not in out

    @pytest.mark.parametrize("name", ["default", "My_Cluster", "Prod-East"])
    def test_unregistered_prefix_not_stamped_even_when_other_types_registered(self, name):
        out = _service({"aws-prod": "aws"}, registered={"aws"}).resolve_template_defaults(
            dict(ROW), name
        )
        assert "provider_type" not in out

    def test_registry_none_means_no_guess(self):
        out = _service({"aws-prod": "aws"}, registry=None).resolve_template_defaults(
            dict(ROW), "aws-prod-2"
        )
        assert "provider_type" not in out

    def test_config_access_error_means_no_stamp(self):
        out = _service(config_error=True, registry=None).resolve_template_defaults(
            dict(ROW), "aws-prod"
        )
        assert "provider_type" not in out

    def test_configured_instance_stamped_even_without_registry(self):
        out = _service({"aws-prod": "aws"}, registry=None).resolve_template_defaults(
            dict(ROW), "aws-prod"
        )
        assert out["provider_type"] == "aws"


# One valid untyped row per provider plus a field only that provider's class keeps.
_PROVIDERS: dict[str, tuple[type, dict[str, Any], str, Any]] = {
    "aws": (
        AWSTemplate,
        {
            "provider_api": "EC2Fleet",
            "machine_image": "ami-12345678",
            "machine_types": {"t3.micro": 1},
            "subnet_ids": ["subnet-1"],
            "security_group_ids": ["sg-1"],
            "fleet_role": "arn:aws:iam::123456789012:role/spot-fleet",
        },
        "fleet_role",
        "arn:aws:iam::123456789012:role/spot-fleet",
    ),
    "azure": (
        AzureTemplate,
        {
            "provider_api": "CycleCloud",
            "resource_group": "rg",
            "location": "eastus",
            "cluster_name": "c1",
            "node_array": "hpc",
            "vm_size": "Standard_D2s_v3",
        },
        "node_array",
        "hpc",
    ),
    "gcp": (
        GCPTemplate,
        {
            "provider_api": "MIG",
            "project_id": "my-project-123",
            "region": "us-central1",
            "zones": ["us-central1-a", "us-central1-b"],
            "machine_type": "e2-small",
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        },
        "source_image_family",
        "debian-12",
    ),
    "oci": (
        OCITemplate,
        {"compartment_id": "ocid1.compartment.x", "shape": "VM.Standard.E4.Flex"},
        "compartment_id",
        "ocid1.compartment.x",
    ),
    "k8s": (
        K8sTemplate,
        {"provider_api": "Pod", "image_id": "nginx", "namespace": "team-a"},
        "namespace",
        "team-a",
    ),
}


@pytest.mark.parametrize("ptype", sorted(_PROVIDERS))
def test_untyped_row_loads_as_provider_template(ptype):
    cls, fields, kept_field, kept_value = _PROVIDERS[ptype]
    factory = TemplateFactory()
    for name, (template_cls, *_rest) in _PROVIDERS.items():
        factory.register_provider_template_class(name, template_cls)

    row = {"template_id": "t1", **fields}
    resolved = _service({f"{ptype}-main": ptype}).resolve_template_defaults(row, f"{ptype}-main")
    template = factory.create_template(resolved)

    assert isinstance(template, cls)
    assert getattr(template, kept_field) == kept_value


class TestSelectedInstanceIsInActiveSet:
    """A row stamped with the selected instance's type survives the active-provider filter.

    Uses the real selection service and the real active-provider filter over a
    config that holds a disabled instance.
    """

    @pytest.mark.parametrize(
        "policy,active",
        [("FIRST_AVAILABLE", "k8s-b"), ("FIRST_AVAILABLE", None), ("ROUND_ROBIN", None)],
    )
    def test_no_row_is_lost_against_today(self, policy, active):
        from orb.config.managers.configuration_manager import ConfigurationManager
        from orb.config.schemas.provider_strategy_schema import (
            ProviderConfig,
            ProviderInstanceConfig,
        )
        from orb.infrastructure.services.provider_selection_service import (
            ProviderSelectionService,
        )
        from orb.infrastructure.template.configuration_manager import (
            TemplateConfigurationManager,
        )
        from orb.infrastructure.template.template_cache_service import (
            create_template_cache_service,
        )

        provider_config = ProviderConfig(
            default_provider_type="aws",
            default_provider_instance="aws-a",
            health_check_interval=30,
            selection_policy=policy,
            active_provider=active,
            providers=[
                ProviderInstanceConfig.model_validate(
                    {"name": "aws-a", "type": "aws", "enabled": True}
                ),
                ProviderInstanceConfig.model_validate(
                    {"name": "k8s-b", "type": "k8s", "enabled": False}
                ),
            ],
        )
        config = ConfigurationManager(config_dict={})
        config.get_provider_config = MagicMock(return_value=provider_config)  # type: ignore[method-assign]
        selected = ProviderSelectionService(
            registry=MagicMock(),
            config_port=config,  # type: ignore[arg-type]
        ).select_active_provider()

        defaults = _service({p.name: p.type for p in provider_config.providers})
        defaults.config_manager.get_provider_config.return_value = provider_config  # type: ignore[attr-defined]
        stamped = defaults.resolve_template_defaults(dict(ROW), selected.provider_name)

        logger = MagicMock()
        manager = TemplateConfigurationManager(
            config_manager=config,
            scheduler_strategy=MagicMock(),
            logger=logger,
            cache_service=create_template_cache_service("noop", logger),
        )
        kept = manager._filter_templates_by_active_providers([stamped])
        assert kept == [stamped]
