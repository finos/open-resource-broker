"""Unit tests for ``K8sTemplateExtensionConfig`` and the matching DTO config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orb.providers.k8s.configuration.template_extension import (
    K8sTemplateExtensionConfig,
)
from orb.providers.k8s.domain.template.k8s_template_dto_config import (
    K8sTemplateDTOConfig,
)


class TestK8sTemplateExtensionConfig:
    """Tests for the operator-facing extension config."""

    def test_defaults_round_trip_empty(self) -> None:
        """All fields default to ``None`` so the defaults dict is empty."""
        config = K8sTemplateExtensionConfig()
        assert config.to_template_defaults() == {}

    def test_populated_fields_appear_in_defaults(self) -> None:
        """Only non-None fields appear in the flat defaults dict."""
        config = K8sTemplateExtensionConfig(
            namespace="orb",
            resource_requests={"cpu": "500m"},
            annotations={"orb.io/note": "hi"},
        )
        defaults = config.to_template_defaults()
        assert defaults["namespace"] == "orb"
        assert defaults["resource_requests"] == {"cpu": "500m"}
        assert defaults["annotations"] == {"orb.io/note": "hi"}
        # None-valued fields are dropped
        assert "completions" not in defaults
        assert "node_selector" not in defaults

    @pytest.mark.parametrize("field", ["completions", "parallelism"])
    def test_workload_counts_must_be_positive(self, field: str) -> None:
        """Zero / negative workload counts are rejected at validation time."""
        with pytest.raises(ValidationError):
            K8sTemplateExtensionConfig(**{field: 0})
        with pytest.raises(ValidationError):
            K8sTemplateExtensionConfig(**{field: -1})

    def test_namespace_rejects_blank_string(self) -> None:
        """An empty namespace string is rejected; ``None`` remains the unset sentinel."""
        with pytest.raises(ValidationError):
            K8sTemplateExtensionConfig(namespace=" ")

    def test_extra_fields_are_ignored(self) -> None:
        """Unknown fields are silently ignored so future schema changes remain compatible."""
        config = K8sTemplateExtensionConfig(unknown_field="surprise")  # type: ignore[call-arg]
        assert "unknown_field" not in config.to_template_defaults()

    def test_shadow_fields_are_gone(self) -> None:
        """``container_image`` / ``labels`` / ``replicas`` no longer live here."""
        fields = K8sTemplateExtensionConfig.model_fields
        assert "container_image" not in fields
        assert "labels" not in fields
        assert "replicas" not in fields

    def test_environment_variables_alias_accepted(self) -> None:
        """Back-compat: the old ``environment_variables`` spelling is still accepted."""
        config = K8sTemplateExtensionConfig.model_validate({"environment_variables": {"X": "1"}})
        assert config.env == {"X": "1"}
        defaults = config.to_template_defaults()
        assert defaults["env"] == {"X": "1"}
        assert "environment_variables" not in defaults

    def test_env_field_in_defaults(self) -> None:
        """The canonical ``env`` key appears in the defaults dict when set."""
        config = K8sTemplateExtensionConfig(env={"FOO": "bar"})
        defaults = config.to_template_defaults()
        assert defaults["env"] == {"FOO": "bar"}


class TestK8sTemplateDTOConfig:
    """Tests for the typed DTO config registered with ``TemplateExtensionRegistry``."""

    def test_defaults_to_empty_flat_dict(self) -> None:
        """An empty config materialises to an empty defaults dict."""
        config = K8sTemplateDTOConfig()
        assert config.to_template_defaults() == {}

    def test_populated_dto_round_trips_to_defaults(self) -> None:
        config = K8sTemplateDTOConfig(
            namespace="prod",
            resource_requests={"cpu": "1", "memory": "2Gi"},
            resource_limits={"cpu": "2", "memory": "4Gi"},
            env={"DEBUG": "1"},
            command=["/bin/run"],
            args=["--workers", "4"],
        )
        defaults = config.to_template_defaults()
        assert defaults["namespace"] == "prod"
        assert defaults["resource_requests"] == {"cpu": "1", "memory": "2Gi"}
        assert defaults["resource_limits"] == {"cpu": "2", "memory": "4Gi"}
        assert defaults["env"] == {"DEBUG": "1"}
        assert defaults["command"] == ["/bin/run"]
        assert defaults["args"] == ["--workers", "4"]

    def test_environment_variables_alias_accepted(self) -> None:
        """Back-compat: the old ``environment_variables`` spelling is still accepted."""
        config = K8sTemplateDTOConfig.model_validate({"environment_variables": {"LEGACY": "1"}})
        assert config.env == {"LEGACY": "1"}
        defaults = config.to_template_defaults()
        assert defaults["env"] == {"LEGACY": "1"}
        # The old key must not appear in the output dict.
        assert "environment_variables" not in defaults

    def test_namespace_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            K8sTemplateDTOConfig(namespace="")

    @pytest.mark.parametrize("field", ["completions", "parallelism"])
    def test_workload_counts_must_be_positive(self, field: str) -> None:
        with pytest.raises(ValidationError):
            K8sTemplateDTOConfig(**{field: 0})

    def test_shadow_fields_are_gone(self) -> None:
        """``container_image`` / ``labels`` / ``replicas`` no longer live here."""
        fields = K8sTemplateDTOConfig.model_fields
        assert "container_image" not in fields
        assert "labels" not in fields
        assert "replicas" not in fields


class TestExtensionRegistration:
    """Verify the DTO config is wired into ``TemplateExtensionRegistry``."""

    def test_extension_registered_after_bootstrap(self) -> None:
        """Importing the kubernetes provider auto-registers the DTO config."""
        # Importing registration triggers the auto-register block at module bottom.
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )
        from orb.providers.k8s import registration  # noqa: F401

        extension_class = TemplateExtensionRegistry.get_extension_class("k8s")
        assert extension_class is K8sTemplateDTOConfig

    def test_get_extension_defaults_returns_extension_baseline(self) -> None:
        """``get_k8s_extension_defaults`` round-trips an empty baseline."""
        from orb.providers.k8s.registration import get_k8s_extension_defaults

        # The extension config has all-None defaults, so the baseline is empty.
        assert get_k8s_extension_defaults() == {}
