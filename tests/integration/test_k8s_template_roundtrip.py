"""Integration tests: K8sTemplate → TemplateDTO → TemplateFactory round-trip.

Verifies that kubernetes-specific fields survive the full serialisation
path: domain object → TemplateDTO (via from_domain) → plain dict
(via model_dump) → TemplateFactory.create_template.  One test per
supported provider_api (Pod, Deployment, StatefulSet, Job) so each
workload type's field surface is covered.

The ``env`` field (typed ``K8sTemplate.env``, a list[K8sEnvVar] on the
domain) is stored as a ``dict[str, str]`` wire form in
``K8sTemplateDTOConfig.env``.  Dict-style input is coerced by the
``K8sTemplate`` field validator so dict env vars survive the round-trip
and are reassembled into ``K8sEnvVar`` entries on the rebuilt template.
"""

from __future__ import annotations

import pytest

from orb.application.dto.template import TemplateDTO
from orb.domain.template.factory import TemplateFactory
from orb.infrastructure.registry.template_extension_registry import TemplateExtensionRegistry
from orb.infrastructure.template.factories import TemplateDTOFactory

_template_dto_factory = TemplateDTOFactory()
from orb.providers.k8s.domain.template.k8s_template import (
    K8sEnvVar,
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
    tolerations=[
        {"key": "dedicated", "operator": "Equal", "value": "compute", "effect": "NoSchedule"}
    ],
    resource_requests={"cpu": "500m", "memory": "1Gi"},
    resource_limits={"cpu": "2", "memory": "4Gi"},
    command=["python"],
    args=["-m", "myapp", "--workers=4"],
    annotations={"prometheus.io/scrape": "true", "prometheus.io/port": "8080"},
    service_account="orb-service-account",
    # dict[str, str] form — coerced to list[K8sEnvVar] by the domain
    # validator and stored back as env on the rebuilt template.
    env={"WORKER_MODE": "batch", "LOG_LEVEL": "info"},
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
    dto: TemplateDTO = _template_dto_factory.from_domain(template)
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

    # env survives the DTO round-trip: dict[str,str] input -> list[K8sEnvVar]
    # on both the original and the rebuilt template.
    assert rebuilt.env is not None and len(rebuilt.env) == 2
    env_names = {e.name for e in rebuilt.env}
    assert env_names == {"WORKER_MODE", "LOG_LEVEL"}
    env_map = {e.name: e.value for e in rebuilt.env}
    assert env_map["WORKER_MODE"] == "batch"
    assert env_map["LOG_LEVEL"] == "info"
    # Confirm domain type is preserved.
    assert all(isinstance(e, K8sEnvVar) for e in rebuilt.env)


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
