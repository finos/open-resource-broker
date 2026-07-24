"""Tests for TemplateDefaultsService — registry delegation and default-API chain."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.application.services.template_defaults_service import TemplateDefaultsService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(
    provider_defaults: dict | None = None,
    global_config: dict | None = None,
    provider_registry: object | None = None,
) -> TemplateDefaultsService:
    """Build a TemplateDefaultsService with controllable config mocks."""
    logger = MagicMock()
    config_manager = MagicMock()

    # Mimic get_template_config() returning a dict (no model_dump needed).
    config_manager.get_template_config.return_value = global_config or {}

    # Mimic get_provider_config().provider_defaults.get(provider_type).
    if provider_defaults is not None:
        mock_provider_config = MagicMock()
        mock_provider_config.provider_defaults = provider_defaults
        mock_provider_config.providers = []
    else:
        mock_provider_config = MagicMock(provider_defaults={}, providers=[])
    config_manager.get_provider_config.return_value = mock_provider_config

    return TemplateDefaultsService(
        config_manager=config_manager,
        logger=logger,
        provider_registry=provider_registry,  # type: ignore[arg-type]
    )


def _make_provider_defaults_with_api(provider_api: str | None) -> MagicMock:
    """Create a provider_defaults mock with template_defaults.provider_api set."""
    defaults_obj = MagicMock()
    template_defaults: dict = {}
    if provider_api is not None:
        template_defaults["provider_api"] = provider_api
    defaults_obj.template_defaults = template_defaults
    return defaults_obj


# ---------------------------------------------------------------------------
# _get_provider_type_defaults — registry delegation
# ---------------------------------------------------------------------------


class TestGetProviderTypeDefaultsDelegatesRegistry:
    """When template_defaults has no provider_api, registry is consulted."""

    def test_registry_default_api_fills_missing_provider_api(self):
        """Registry.get_default_api() result is injected when template_defaults lacks provider_api."""
        registry = MagicMock()
        registry.get_default_api.return_value = "EC2Fleet"

        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert result.get("provider_api") == "EC2Fleet"
        registry.get_default_api.assert_called_once_with("aws")

    def test_registry_not_called_when_template_defaults_has_provider_api(self):
        """Registry is NOT consulted when template_defaults already has provider_api."""
        registry = MagicMock()
        registry.get_default_api.return_value = "SpotFleet"

        provider_defaults_obj = _make_provider_defaults_with_api("EC2Fleet")
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert result.get("provider_api") == "EC2Fleet"
        registry.get_default_api.assert_not_called()

    def test_no_registry_injected_returns_empty_provider_api(self):
        """Without registry, if template_defaults has no provider_api, result has none."""
        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=None,
        )

        result = svc._get_provider_type_defaults("aws")

        assert "provider_api" not in result

    def test_registry_returns_none_leaves_provider_api_absent(self):
        """When registry returns None, provider_api is not added to result."""
        registry = MagicMock()
        registry.get_default_api.return_value = None

        provider_defaults_obj = _make_provider_defaults_with_api(None)
        svc = _make_service(
            provider_defaults={"aws": provider_defaults_obj},
            provider_registry=registry,
        )

        result = svc._get_provider_type_defaults("aws")

        assert "provider_api" not in result

    def test_unknown_provider_type_returns_empty_dict(self):
        """A provider type with no registration returns {}."""
        registry = MagicMock()
        registry.get_default_api.return_value = None
        svc = _make_service(provider_defaults={}, provider_registry=registry)

        result = svc._get_provider_type_defaults("unknown-provider")

        assert result == {}


# ---------------------------------------------------------------------------
# _coalesce_merge — alias-aware override precedence
# ---------------------------------------------------------------------------


class TestCoalesceMergeAliasAwareness:
    """A caller value under any field alias must supersede a default under
    a *different* alias of the same field.

    Regression: the shipped aws_defaults block carries the canonical
    ``machine_image`` name while a caller may still pass the deprecated
    ``image_id`` (or vice versa).  Before the fix both keys survived the merge
    and the domain model's AliasChoices precedence silently picked the default,
    discarding the caller's value.
    """

    def test_override_legacy_alias_supersedes_default_canonical(self):
        svc = _make_service()

        merged = svc._coalesce_merge(
            {"machine_image": "ami-default-ssm", "machine_type": "t2.micro"},
            {"image_id": "ami-caller-choice"},
        )

        # Only the caller's alias survives for that field — the default's
        # sibling alias is dropped so it cannot win via AliasChoices.
        assert merged.get("image_id") == "ami-caller-choice"
        assert "machine_image" not in merged
        # Unrelated default fields are untouched.
        assert merged["machine_type"] == "t2.micro"

    def test_override_canonical_alias_supersedes_default_legacy(self):
        svc = _make_service()

        merged = svc._coalesce_merge(
            {"image_id": "ami-default-legacy"},
            {"machine_image": "ami-caller-choice"},
        )

        assert merged.get("machine_image") == "ami-caller-choice"
        assert "image_id" not in merged

    def test_non_aliased_field_still_overrides_normally(self):
        svc = _make_service()

        merged = svc._coalesce_merge(
            {"provider_api": "EC2Fleet"},
            {"provider_api": "SpotFleet"},
        )

        assert merged["provider_api"] == "SpotFleet"

    def test_empty_collection_override_leaves_default(self):
        svc = _make_service()

        merged = svc._coalesce_merge(
            {"subnet_ids": ["subnet-abc"]},
            {"subnet_ids": []},
        )

        assert merged["subnet_ids"] == ["subnet-abc"]


def _make_service_with_layers(
    factory: object | None = None,
    global_config: dict | None = None,
    type_defaults: dict | None = None,
    instance_defaults: dict | None = None,
    provider_name: str = "aws-primary",
    provider_type: str = "aws",
) -> TemplateDefaultsService:
    """Build a service wired with distinct global/type/instance default layers."""
    logger = MagicMock()
    config_manager = MagicMock()
    config_manager.get_template_config.return_value = global_config or {}

    type_defaults_obj = MagicMock()
    type_defaults_obj.template_defaults = type_defaults or {}

    instance = MagicMock()
    instance.name = provider_name
    instance.type = provider_type
    instance.template_defaults = instance_defaults or {}

    mock_provider_config = MagicMock()
    mock_provider_config.provider_defaults = {provider_type: type_defaults_obj}
    mock_provider_config.providers = [instance]
    config_manager.get_provider_config.return_value = mock_provider_config

    return TemplateDefaultsService(
        config_manager=config_manager,
        logger=logger,
        template_factory=factory,  # type: ignore[arg-type]
    )


class TestInterLayerAliasAwareMerge:
    """Inter-layer (global -> type -> instance) merges must be alias-aware.

    Regression: the layering previously used plain ``dict.update`` keyed by raw
    name.  A canonical name in the type layer and its legacy alias in the
    instance layer both survived, so the domain model's canonical-first
    ``AliasChoices`` precedence silently let the *lower*-priority type layer
    override the higher-priority instance layer.
    """

    def test_canonical_type_default_legacy_instance_override_instance_wins(self):
        svc = _make_service_with_layers(
            type_defaults={"machine_image": "ami-TYPE", "provider_api": "EC2Fleet"},
            instance_defaults={"image_id": "ami-INSTANCE"},
        )

        merged = svc.resolve_template_defaults({"template_id": "t1"}, "aws-primary")

        assert merged.get("image_id") == "ami-INSTANCE"
        assert "machine_image" not in merged

    def test_legacy_type_default_canonical_instance_override_instance_wins(self):
        svc = _make_service_with_layers(
            type_defaults={"image_id": "ami-TYPE"},
            instance_defaults={"machine_image": "ami-INSTANCE"},
        )

        merged = svc.resolve_template_defaults({"template_id": "t1"}, "aws-primary")

        assert merged.get("machine_image") == "ami-INSTANCE"
        assert "image_id" not in merged

    def test_get_effective_defaults_is_alias_aware(self):
        svc = _make_service_with_layers(
            type_defaults={"machine_image": "ami-TYPE"},
            instance_defaults={"image_id": "ami-INSTANCE"},
        )

        eff = svc.get_effective_template_defaults("aws-primary")

        assert eff.get("image_id") == "ami-INSTANCE"
        assert "machine_image" not in eff


class TestProviderSubclassAliasCoverage:
    """Alias groups must cover top-level AliasChoices fields on provider
    subclasses, not just the base ``Template``.

    Regression: ``AWSTemplate.abis_instance_requirements`` declares
    ``AliasChoices('abis_instance_requirements', 'abisInstanceRequirements')``.
    That field does not exist on the base ``Template``, so a merge that only
    knew base-model aliases left both keys alive and the caller's camelCase
    value was silently discarded.
    """

    @staticmethod
    def _aws_factory() -> object:
        from orb.domain.template.factory import TemplateFactory
        from orb.providers.aws.domain.template.aws_template_aggregate import AWSTemplate

        factory = TemplateFactory()
        factory.register_provider_template_class("aws", AWSTemplate)
        return factory

    def test_subclass_alias_caller_supersedes_default_coalesce(self):
        svc = _make_service_with_layers(factory=self._aws_factory())

        merged = svc._coalesce_merge(
            {
                "abis_instance_requirements": {
                    "VCpuCount": {"Min": 1, "Max": 2},
                    "MemoryMiB": {"Min": 1, "Max": 2},
                }
            },
            {
                "abisInstanceRequirements": {
                    "VCpuCount": {"Min": 8, "Max": 16},
                    "MemoryMiB": {"Min": 100, "Max": 200},
                }
            },
        )

        assert "abis_instance_requirements" not in merged
        assert merged["abisInstanceRequirements"]["VCpuCount"]["Min"] == 8

    def test_subclass_alias_uncovered_without_factory(self):
        """Without a factory the subclass alias is unknown, so both keys survive.

        This pins the mechanism: subclass coverage comes from the injected
        factory, not from the base model.
        """
        svc = _make_service_with_layers(factory=None)

        merged = svc._coalesce_merge(
            {"abis_instance_requirements": {"a": 1}},
            {"abisInstanceRequirements": {"b": 2}},
        )

        assert "abis_instance_requirements" in merged
        assert "abisInstanceRequirements" in merged


class TestExtensionLayerAliasAwareMerge:
    """The type -> instance *extension* merge must be alias-aware too.

    Regression: ``_get_extension_defaults`` layered type-then-instance
    extension defaults with plain ``dict.update``.  ``K8sTemplateExtensionConfig``
    declares ``env`` = AliasChoices('env', 'environment_variables'); a type-layer
    default under one alias plus an instance-layer override under the other left
    both keys alive, so the model's canonical-first precedence silently lost the
    higher-priority instance value.
    """

    @staticmethod
    def _k8s_extension_registry() -> object:
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
            TemplateExtensionRegistryAdapter,
        )
        from orb.providers.k8s.configuration.template_extension import (
            K8sTemplateExtensionConfig,
        )

        TemplateExtensionRegistry.register_extension("k8s", K8sTemplateExtensionConfig)
        return TemplateExtensionRegistryAdapter()

    def _make_service(
        self, instance_extensions: dict, instance_name: str = "k8s-primary"
    ) -> TemplateDefaultsService:
        logger = MagicMock()
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}

        instance = MagicMock()
        instance.name = instance_name
        instance.type = "k8s"
        instance.template_defaults = {}
        instance.extensions = instance_extensions

        provider_config = MagicMock()
        provider_config.provider_defaults = {}
        provider_config.providers = [instance]
        config_manager.get_provider_config.return_value = provider_config

        return TemplateDefaultsService(
            config_manager=config_manager,
            logger=logger,
            extension_registry=self._k8s_extension_registry(),  # type: ignore[arg-type]
        )

    def test_instance_env_supersedes_type_environment_variables(self):
        """Type-layer legacy ``environment_variables`` + instance-layer canonical
        ``env`` → instance value wins, only one key survives.

        Exercises the exact merge the extension path now performs: type layer
        keyed under the legacy alias, instance layer under the canonical name,
        merged with the extension model's alias groups.
        """
        svc = self._make_service(instance_extensions={"env": {"B": "2"}})
        groups = svc._extension_alias_groups("k8s")

        type_layer = svc._merge_layer({}, {"environment_variables": {"A": "1"}}, groups)
        merged = svc._merge_layer(type_layer, {"env": {"B": "2"}}, groups)

        assert merged.get("env") == {"B": "2"}
        assert "environment_variables" not in merged

        # And the reverse direction: canonical type default, legacy instance override.
        type_layer2 = svc._merge_layer({}, {"env": {"A": "1"}}, groups)
        merged2 = svc._merge_layer(type_layer2, {"environment_variables": {"B": "2"}}, groups)
        assert merged2.get("environment_variables") == {"B": "2"}
        assert "env" not in merged2

    def test_full_extension_resolution_instance_override_wins(self):
        """End-to-end through ``_get_extension_defaults``: the instance extension
        override (canonical ``env``) resolves through K8sTemplateExtensionConfig."""
        svc = self._make_service(instance_extensions={"env": {"B": "2"}})

        merged = svc._get_extension_defaults("k8s", "k8s-primary")

        assert merged.get("env") == {"B": "2"}
        assert "environment_variables" not in merged

    def test_extension_to_template_boundary_is_alias_aware(self):
        """The extension-layer -> resolved-template merge must not leave sibling
        aliases alive across the two models' alias namespaces.

        A template value under one alias must drop the lower-priority extension
        default carried under a *different* alias of the same logical field.
        """
        svc = self._make_service(instance_extensions={"env": {"B": "2"}})
        groups = svc._template_and_extension_alias_groups("k8s")

        # env / environment_variables belong to one combined slot.
        assert set(groups.get("env", ())) == {"env", "environment_variables"}

        # Lower-priority extension carries legacy alias; template carries canonical.
        merged = svc._merge_layer(
            {"environment_variables": {"A": "1"}},  # extension layer (base)
            {"env": {"B": "2"}},  # resolved template (overlay, wins)
            groups,
        )
        assert merged.get("env") == {"B": "2"}
        assert "environment_variables" not in merged

    def test_extension_alias_groups_cover_env_field(self):
        svc = self._make_service(instance_extensions={"env": {"B": "2"}})
        groups = svc._extension_alias_groups("k8s")
        assert set(groups.get("env", ())) == {"env", "environment_variables"}
        assert set(groups.get("environment_variables", ())) == {"env", "environment_variables"}


# ---------------------------------------------------------------------------
# TemplateDefaultsService init — provider_registry optional
# ---------------------------------------------------------------------------


class TestTemplateDefaultsServiceInit:
    def test_no_registry_param_accepted(self):
        """Service can be instantiated without provider_registry (backward compat)."""
        logger = MagicMock()
        config_manager = MagicMock()
        config_manager.get_template_config.return_value = {}
        config_manager.get_provider_config.return_value = MagicMock(
            provider_defaults={}, providers=[]
        )

        svc = TemplateDefaultsService(config_manager=config_manager, logger=logger)

        assert svc.provider_registry is None

    def test_registry_stored_on_instance(self):
        """Injected provider_registry is accessible via attribute."""
        registry = MagicMock()
        svc = _make_service(provider_registry=registry)
        assert svc.provider_registry is registry
