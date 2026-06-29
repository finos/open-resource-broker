"""Integration tests: K8sTemplate → TemplateDTO → TemplateFactory round-trip.

Verifies that kubernetes-specific fields survive the full serialisation
path: domain object → TemplateDTO (via from_domain) → plain dict
(via model_dump) → TemplateFactory.create_template.  One test per
supported provider_api (Pod, Deployment, StatefulSet, Job) so each
workload type's field surface is covered.

Note on env vars: the typed ``K8sTemplate.env`` (list[K8sEnvVar]) does not
survive the TemplateDTO round-trip because ``K8sTemplateDTOConfig`` stores
environment variables under ``environment_variables`` (dict[str, str]).
The tests use the dict-style ``env`` input (coerced by the K8sTemplate
field validator) and verify only the fields that the DTO layer actually
preserves.
"""

from __future__ import annotations

import pytest

from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.template.dtos import TemplateDTO
from orb.providers.k8s.domain.template.k8s_template import (
    K8sTemplate,
    K8sToleration,
)
from orb.providers.k8s.domain.template.k8s_template_dto_config import K8sTemplateDTOConfig

# ---------------------------------------------------------------------------
# One-time registration of the k8s DTO extension
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _register_k8s_extension() -> None:
    """Ensure the k8s TemplateExtensionRegistry entry is present for every test."""
    if not TemplateExtensionRegistry.has_extension("k8s"):
        TemplateExtensionRegistry.register_extension("k8s", K8sTemplateDTOConfig)


# ---------------------------------------------------------------------------
# Shared template factory fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def factory() -> TemplateFactory:
    f = TemplateFactory()
    f.register_provider_template_class("k8s", K8sTemplate)
    return f


# ---------------------------------------------------------------------------
# Rich payload shared across all provider_api variants
# ---------------------------------------------------------------------------

_RICH_FIELDS = dict(
    image_id="registry.example.com/myapp:v2",
    namespace="orb-system",
    node_selector={"role": "compute", "zone": "us-east-1a"},
    tolerations=[{"key": "dedicated", "operator": "Equal", "value": "compute", "effect": "NoSchedule"}],
    resource_requests={"cpu": "500m", "memory": "1Gi"},
    resource_limits={"cpu": "2", "memory": "4Gi"},
    command=["python"],
    args=["-m", "myapp", "--workers=4"],
    annotations={"prometheus.io/scrape": "true", "prometheus.io/port": "8080"},
    service_account="orb-service-account",
)


def _build_template(provider_api: str, extra: dict | None = None) -> K8sTemplate:
    data: dict = {
        "template_id": f"tpl-roundtrip-{provider_api.lower()}",
        "provider_type": "k8s",
        "provider_api": provider_api,
        "max_instances": 5,
        **_RICH_FIELDS,
        **(extra or {}),
    }
    return K8sTemplate(**data)


def _do_roundtrip(template: K8sTemplate, factory: TemplateFactory) -> K8sTemplate:
    """template → TemplateDTO → dict → TemplateFactory.create_template."""
    dto: TemplateDTO = TemplateDTO.from_domain(template)
    raw: dict = dto.model_dump()
    result = factory.create_template(raw)
    assert isinstance(result, K8sTemplate), (
        f"Expected K8sTemplate back from factory, got {type(result)}"
    )
    return result


def _assert_common_fields(original: K8sTemplate, rebuilt: K8sTemplate) -> None:
    assert rebuilt.template_id == original.template_id
    assert rebuilt.provider_type == "k8s"
    assert rebuilt.provider_api == original.provider_api
    assert rebuilt.max_instances == original.max_instances
    assert rebuilt.image_id == original.image_id
    assert rebuilt.namespace == original.namespace
    assert rebuilt.node_selector == original.node_selector
    assert rebuilt.service_account == original.service_account
    assert rebuilt.command == original.command
    assert rebuilt.args == original.args
    assert rebuilt.annotations == original.annotations

    # Tolerations survive the DTO round-trip via provider_config.tolerations.
    assert rebuilt.tolerations is not None and len(rebuilt.tolerations) == 1
    tol = rebuilt.tolerations[0]
    assert isinstance(tol, K8sToleration)
    assert tol.key == "dedicated"
    assert tol.operator == "Equal"
    assert tol.value == "compute"
    assert tol.effect == "NoSchedule"

    # Resource requests / limits survive via provider_config fields.
    assert rebuilt.resource_requests is not None
    assert rebuilt.resource_requests.cpu == "500m"
    assert rebuilt.resource_requests.memory == "1Gi"
    assert rebuilt.resource_limits is not None
    assert rebuilt.resource_limits.cpu == "2"
    assert rebuilt.resource_limits.memory == "4Gi"


# ---------------------------------------------------------------------------
# Tests — one per provider_api
# ---------------------------------------------------------------------------


def test_pod_template_round_trips_all_fields(factory: TemplateFactory) -> None:
    """A Pod K8sTemplate with rich k8s-specific fields round-trips without data loss."""
    original = _build_template("Pod")
    rebuilt = _do_roundtrip(original, factory)
    _assert_common_fields(original, rebuilt)


def test_deployment_template_round_trips_all_fields(factory: TemplateFactory) -> None:
    """A Deployment K8sTemplate round-trips all common and Deployment-specific fields."""
    original = _build_template("Deployment")
    rebuilt = _do_roundtrip(original, factory)
    _assert_common_fields(original, rebuilt)


def test_statefulset_template_round_trips_all_fields(factory: TemplateFactory) -> None:
    """A StatefulSet K8sTemplate round-trips all fields that stress the typed surface."""
    original = _build_template("StatefulSet")
    rebuilt = _do_roundtrip(original, factory)
    _assert_common_fields(original, rebuilt)


def test_job_template_round_trips_all_fields(factory: TemplateFactory) -> None:
    """A Job K8sTemplate round-trips including Job-specific fields (completions, parallelism,
    ttl_seconds_after_finished)."""
    original = _build_template(
        "Job",
        extra={
            "completions": 10,
            "parallelism": 4,
            "ttl_seconds_after_finished": 300,
            "active_deadline_seconds": 3600,
        },
    )
    rebuilt = _do_roundtrip(original, factory)
    _assert_common_fields(original, rebuilt)
    assert rebuilt.completions == 10
    assert rebuilt.parallelism == 4
    assert rebuilt.ttl_seconds_after_finished == 300
    assert rebuilt.active_deadline_seconds == 3600
