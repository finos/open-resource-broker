"""Unit tests for ``KubernetesTemplateExtensionConfig`` and the matching DTO config."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orb.providers.kubernetes.configuration.template_extension import (
    KubernetesTemplateExtensionConfig,
)
from orb.providers.kubernetes.domain.template.kubernetes_template_dto_config import (
    KubernetesTemplateDTOConfig,
)


class TestKubernetesTemplateExtensionConfig:
    """Tests for the operator-facing extension config."""

    def test_defaults_round_trip_empty(self) -> None:
        """All fields default to ``None`` so the defaults dict is empty."""
        config = KubernetesTemplateExtensionConfig()
        assert config.to_template_defaults() == {}

    def test_populated_fields_appear_in_defaults(self) -> None:
        """Only non-None fields appear in the flat defaults dict."""
        config = KubernetesTemplateExtensionConfig(
            replicas=3,
            namespace="orb",
            resource_requests={"cpu": "500m"},
            labels={"team": "ml"},
        )
        defaults = config.to_template_defaults()
        assert defaults["replicas"] == 3
        assert defaults["namespace"] == "orb"
        assert defaults["resource_requests"] == {"cpu": "500m"}
        assert defaults["labels"] == {"team": "ml"}
        # None-valued fields are dropped
        assert "completions" not in defaults
        assert "node_selector" not in defaults

    @pytest.mark.parametrize("field", ["replicas", "completions", "parallelism"])
    def test_workload_counts_must_be_positive(self, field: str) -> None:
        """Zero / negative workload counts are rejected at validation time."""
        with pytest.raises(ValidationError):
            KubernetesTemplateExtensionConfig(**{field: 0})
        with pytest.raises(ValidationError):
            KubernetesTemplateExtensionConfig(**{field: -1})

    def test_namespace_rejects_blank_string(self) -> None:
        """An empty namespace string is rejected; ``None`` remains the unset sentinel."""
        with pytest.raises(ValidationError):
            KubernetesTemplateExtensionConfig(namespace=" ")

    def test_extra_fields_are_ignored(self) -> None:
        """Unknown fields are silently ignored so future schema changes remain compatible."""
        config = KubernetesTemplateExtensionConfig(unknown_field="surprise")  # type: ignore[call-arg]
        assert "unknown_field" not in config.to_template_defaults()


class TestKubernetesTemplateDTOConfig:
    """Tests for the typed DTO config registered with ``TemplateExtensionRegistry``."""

    def test_defaults_to_empty_flat_dict(self) -> None:
        """An empty config materialises to an empty defaults dict."""
        config = KubernetesTemplateDTOConfig()
        assert config.to_template_defaults() == {}

    def test_populated_dto_round_trips_to_defaults(self) -> None:
        config = KubernetesTemplateDTOConfig(
            container_image="ghcr.io/example/worker:1.2.3",
            namespace="prod",
            replicas=4,
            resource_requests={"cpu": "1", "memory": "2Gi"},
            resource_limits={"cpu": "2", "memory": "4Gi"},
            environment_variables={"DEBUG": "1"},
            command=["/bin/run"],
            args=["--workers", "4"],
        )
        defaults = config.to_template_defaults()
        assert defaults["container_image"] == "ghcr.io/example/worker:1.2.3"
        assert defaults["namespace"] == "prod"
        assert defaults["replicas"] == 4
        assert defaults["resource_requests"] == {"cpu": "1", "memory": "2Gi"}
        assert defaults["resource_limits"] == {"cpu": "2", "memory": "4Gi"}
        assert defaults["environment_variables"] == {"DEBUG": "1"}
        assert defaults["command"] == ["/bin/run"]
        assert defaults["args"] == ["--workers", "4"]

    def test_container_image_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            KubernetesTemplateDTOConfig(container_image="")

    def test_namespace_rejects_blank(self) -> None:
        with pytest.raises(ValidationError):
            KubernetesTemplateDTOConfig(namespace="")

    @pytest.mark.parametrize("field", ["replicas", "completions", "parallelism"])
    def test_workload_counts_must_be_positive(self, field: str) -> None:
        with pytest.raises(ValidationError):
            KubernetesTemplateDTOConfig(**{field: 0})


class TestExtensionRegistration:
    """Verify the DTO config is wired into ``TemplateExtensionRegistry``."""

    def test_extension_registered_after_bootstrap(self) -> None:
        """Importing the kubernetes provider auto-registers the DTO config."""
        # Importing registration triggers the auto-register block at module bottom.
        from orb.infrastructure.registry.template_extension_registry import (
            TemplateExtensionRegistry,
        )
        from orb.providers.kubernetes import registration  # noqa: F401

        extension_class = TemplateExtensionRegistry.get_extension_class("kubernetes")
        assert extension_class is KubernetesTemplateDTOConfig

    def test_get_extension_defaults_returns_extension_baseline(self) -> None:
        """``get_kubernetes_extension_defaults`` round-trips an empty baseline."""
        from orb.providers.kubernetes.registration import get_kubernetes_extension_defaults

        # The extension config has all-None defaults, so the baseline is empty.
        assert get_kubernetes_extension_defaults() == {}
