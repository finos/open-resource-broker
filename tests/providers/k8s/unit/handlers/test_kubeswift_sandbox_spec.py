"""Template -> SwiftSandbox spec rules for the KubeSwiftSandbox handler."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orb.providers.k8s.infrastructure.handlers.kubeswift.sandbox_spec import (
    KubeSwiftTemplateError,
    build_spec,
    kubeswift_config,
    template_static_errors,
)

POOL = {
    "metadata": {"name": "workers"},
    "spec": {
        "image": "alpine:3.20",
        "cpu": 1,
        "memory": "512Mi",
        "network": {"mode": "restricted"},
        "rootfsMode": "block",
        "minWarm": 2,
    },
}


def _template(image: str | None = "alpine:3.20", ks: Any = None, **fields: Any) -> Any:
    base: dict[str, Any] = {
        "machine_image": image,
        "metadata": {"kubeswift": ks if ks is not None else {}},
        "machine_bootstrap": None,
        "command": None,
        "args": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


def test_kubeswift_config_reads_metadata_block() -> None:
    assert kubeswift_config(_template(ks={"poolRef": "w"})) == {"poolRef": "w"}
    assert kubeswift_config(SimpleNamespace(metadata=None)) == {}
    assert kubeswift_config(SimpleNamespace(metadata={"kubeswift": "x"})) == {}


def test_pooled_spec_inherits_the_pool_shape() -> None:
    spec = build_spec(_template(ks={"poolRef": "workers", "command": ["sleep", "1"]}), POOL)
    assert spec == {
        "image": "alpine:3.20",
        "cpu": 1,
        "memory": "512Mi",
        "network": {"mode": "restricted"},
        "rootfsMode": "block",
        "poolRef": {"name": "workers"},
        "command": ["sleep", "1"],
    }


def test_pooled_image_mismatch_is_refused() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="pool image"):
        build_spec(_template(image="busybox", ks={"poolRef": "workers"}), POOL)


def test_pooled_shape_override_is_refused() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="memory"):
        build_spec(_template(ks={"poolRef": "workers", "memory": "1Gi"}), POOL)


def test_pooled_gpu_is_refused() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="GPU"):
        build_spec(_template(ks={"poolRef": "workers", "gpuProfileRef": "g"}), POOL)


def test_cold_spec() -> None:
    spec = build_spec(
        _template(ks={"cpu": 2, "memory": "1Gi", "gpuProfileRef": "l40s", "env": {"A": 1}}),
        None,
    )
    assert spec == {
        "image": "alpine:3.20",
        "memory": "1Gi",
        "cpu": 2,
        "network": {"mode": "restricted"},
        "gpuProfileRef": {"name": "l40s"},
        "env": [{"name": "A", "value": "1"}],
    }


def test_cold_spec_defaults() -> None:
    assert build_spec(_template(), None) == {
        "image": "alpine:3.20",
        "memory": "512Mi",
        "network": {"mode": "restricted"},
    }


def test_template_command_and_args_are_used_when_the_block_has_none() -> None:
    spec = build_spec(_template(command=["/bin/app"], args=["--x"]), None)
    assert spec["command"] == ["/bin/app"]
    assert spec["args"] == ["--x"]


def test_gpu_backends_are_mutually_exclusive() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="mutually exclusive"):
        build_spec(_template(ks={"gpuProfileRef": "a", "gpuResourceClaim": {"name": "c"}}), None)


def test_machine_bootstrap_is_refused() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="cloud-init"):
        build_spec(_template(machine_bootstrap="#cloud-config"), None)


def test_pod_fields_are_refused() -> None:
    with pytest.raises(KubeSwiftTemplateError, match="resource_requests"):
        build_spec(_template(resource_requests={"cpu": "1"}), None)


def test_machine_image_is_required() -> None:
    assert template_static_errors(_template(image=None)) == [
        "machine_image is required (for a pooled sandbox, the pool's image)"
    ]


def test_non_object_block_is_refused() -> None:
    assert template_static_errors(_template(ks=["poolRef"])) == [
        "metadata.kubeswift must be an object"
    ]


def test_env_must_be_an_object() -> None:
    assert template_static_errors(_template(ks={"env": ["A=1"]})) == [
        "metadata.kubeswift.env must be an object of name: value"
    ]


@pytest.mark.parametrize("pool", [POOL, None])
def test_spec_never_sets_pod_metadata(pool: Any) -> None:
    # KubeSwift copies podMetadata labels onto the launcher pod; ORB's pod
    # orphan GC must never see a live sandbox's launcher as its own pod.
    ks = {"poolRef": "workers"} if pool else {}
    assert "podMetadata" not in build_spec(_template(ks=ks), pool)
