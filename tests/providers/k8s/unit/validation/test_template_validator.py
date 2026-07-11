"""Unit tests for :class:`K8sTemplateValidator`.

Covers each of the eight validation rules independently as well as a
handful of integration scenarios using fully-typed :class:`K8sTemplate`
instances.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from orb.providers.k8s.domain.template.k8s_template_aggregate import (
    K8sResourceQuantities,
    K8sTemplate,
    K8sToleration,
)
from orb.providers.k8s.validation.template_validator import (
    K8sTemplateValidationResult,
    K8sTemplateValidator,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_validator() -> K8sTemplateValidator:
    return K8sTemplateValidator()


def _minimal_template(**overrides: Any) -> K8sTemplate:
    """Return the minimal valid K8sTemplate, with optional field overrides."""
    defaults: dict[str, Any] = {
        "template_id": "my-template",
        "image_id": "busybox:latest",
        "max_instances": 1,
    }
    defaults.update(overrides)
    return K8sTemplate(**defaults)


def _stub(**attrs: Any) -> Any:
    """Return a SimpleNamespace with the given attributes (for duck-typing tests)."""
    return SimpleNamespace(**attrs)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class TestK8sTemplateValidationResult:
    def test_valid_result_is_truthy(self) -> None:
        r = K8sTemplateValidationResult(valid=True)
        assert r

    def test_invalid_result_is_falsy(self) -> None:
        r = K8sTemplateValidationResult(valid=False, errors=["oops"])
        assert not r

    def test_errors_default_to_empty_list(self) -> None:
        r = K8sTemplateValidationResult(valid=True)
        assert r.errors == []

    def test_warnings_default_to_empty_list(self) -> None:
        r = K8sTemplateValidationResult(valid=True)
        assert r.warnings == []


# ---------------------------------------------------------------------------
# Rule 1 — template_id
# ---------------------------------------------------------------------------


class TestTemplateIdRule:
    def test_valid_template_id(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert "template_id" not in " ".join(result.errors)

    def test_missing_template_id(self) -> None:
        v = _make_validator()
        # Use a stub without template_id attribute to simulate missing field.
        obj = _stub(
            image_id="busybox:latest",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("template_id" in e for e in result.errors)

    def test_empty_string_template_id(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="",
            image_id="busybox:latest",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("template_id" in e for e in result.errors)

    def test_whitespace_only_template_id(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="   ",
            image_id="busybox:latest",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("template_id" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Rule 2 — max_instances
# ---------------------------------------------------------------------------


class TestMaxInstancesRule:
    def test_valid_max_instances(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template(max_instances=5))
        assert result.valid, result.errors

    def test_max_instances_of_one(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template(max_instances=1))
        assert result.valid, result.errors

    def test_max_instances_zero(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=0,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("max_instances" in e for e in result.errors)

    def test_max_instances_negative(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=-10,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("max_instances" in e for e in result.errors)

    def test_max_instances_none_is_allowed(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=None,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_max_instances_non_integer_rejected(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances="banana",
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("max_instances" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Rule 3 — provider_api
# ---------------------------------------------------------------------------


class TestProviderApiRule:
    @pytest.mark.parametrize("api", ["Pod", "Deployment", "StatefulSet", "Job"])
    def test_supported_provider_apis(self, api: str) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=api,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_unsupported_provider_api(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api="CronJob",
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("provider_api" in e for e in result.errors)

    def test_none_provider_api_is_allowed(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_lowercase_pod_rejected(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api="pod",
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid


# ---------------------------------------------------------------------------
# Rule 4 — image_id
# ---------------------------------------------------------------------------


class TestImageIdRule:
    def test_valid_image_id(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert result.valid, result.errors

    def test_missing_image_id(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id=None,
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("image_id" in e for e in result.errors)

    def test_empty_image_id(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("image_id" in e for e in result.errors)

    def test_whitespace_image_id(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="  ",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid


# ---------------------------------------------------------------------------
# Rule 5 — namespace
# ---------------------------------------------------------------------------


class TestNamespaceRule:
    @pytest.mark.parametrize(
        "ns",
        [
            "default",
            "my-namespace",
            "kube-system",
            "orb123",
            "a",
            "a" * 63,  # max 63 chars
        ],
    )
    def test_valid_namespaces(self, ns: str) -> None:
        v = _make_validator()
        # Use a stub with namespace set.
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=ns,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, f"Expected valid for namespace={ns!r}, got {result.errors}"

    @pytest.mark.parametrize(
        "ns",
        [
            "-starts-with-hyphen",
            "ends-with-hyphen-",
            "Has-Uppercase",
            "has spaces",
            "a" * 64,  # 64 chars — too long
            "with.dot",
            "",
        ],
    )
    def test_invalid_namespaces(self, ns: str) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=ns,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid, f"Expected invalid for namespace={ns!r}"
        assert any("namespace" in e for e in result.errors)

    def test_none_namespace_is_allowed(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert result.valid, result.errors


# ---------------------------------------------------------------------------
# Rule 6 — service_account
# ---------------------------------------------------------------------------


class TestServiceAccountRule:
    @pytest.mark.parametrize(
        "sa",
        ["default", "my-sa", "workload-identity", "sa123", "s"],
    )
    def test_valid_service_accounts(self, sa: str) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=sa,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, f"Expected valid for service_account={sa!r}, got {result.errors}"

    @pytest.mark.parametrize(
        "sa",
        [
            "-bad-start",
            "bad-end-",
            "Has_Underscore",
            "has spaces",
            "ALLCAPS",
        ],
    )
    def test_invalid_service_accounts(self, sa: str) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=sa,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid, f"Expected invalid for service_account={sa!r}"
        assert any("service_account" in e for e in result.errors)

    def test_none_service_account_is_allowed(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert result.valid, result.errors


# ---------------------------------------------------------------------------
# Rule 7 — resource_requests / resource_limits
# ---------------------------------------------------------------------------


class TestResourceQuantitiesRule:
    @pytest.mark.parametrize(
        "qty",
        ["500m", "1", "2.5", "1Gi", "256Mi", "500M", "100k", "200Ki", "1G"],
    )
    def test_valid_quantities(self, qty: str) -> None:
        v = _make_validator()
        rq = K8sResourceQuantities(cpu=qty)
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=rq,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, f"Expected valid for qty={qty!r}, got {result.errors}"

    @pytest.mark.parametrize(
        "qty",
        ["notaquantity", "12abc", "Gi", "!!!", "1 GiB"],
    )
    def test_invalid_quantities(self, qty: str) -> None:
        v = _make_validator()
        rq = K8sResourceQuantities(cpu=qty)
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=rq,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid, f"Expected invalid for qty={qty!r}"
        assert any("resource_requests" in e for e in result.errors)

    def test_resource_limits_also_validated(self) -> None:
        v = _make_validator()
        rl = K8sResourceQuantities(memory="BAD_QUANTITY")
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=rl,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("resource_limits" in e for e in result.errors)

    def test_none_resources_allowed(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert result.valid, result.errors

    def test_raw_dict_resources_valid(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests={"cpu": "500m", "memory": "256Mi"},
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_raw_dict_resources_invalid(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests={"cpu": "lots"},
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid


# ---------------------------------------------------------------------------
# Rule 8 — tolerations
# ---------------------------------------------------------------------------


class TestTolerationsRule:
    def test_valid_typed_toleration(self) -> None:
        v = _make_validator()
        tol = K8sToleration(key="gpu", operator="Exists", effect="NoSchedule")
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=[tol],
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_valid_dict_toleration(self) -> None:
        v = _make_validator()
        tol_dict = {"key": "spot", "operator": "Equal", "value": "true", "effect": "NoSchedule"}
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=[tol_dict],
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_empty_tolerations_allowed(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=[],
        )
        result = v.validate(obj)
        assert result.valid, result.errors

    def test_none_tolerations_allowed(self) -> None:
        v = _make_validator()
        result = v.validate(_minimal_template())
        assert result.valid, result.errors

    def test_invalid_toleration_type(self) -> None:
        v = _make_validator()
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=["not-a-toleration"],
        )
        result = v.validate(obj)
        assert not result.valid
        assert any("toleration" in e.lower() for e in result.errors)

    def test_multiple_tolerations_valid(self) -> None:
        v = _make_validator()
        tols = [
            K8sToleration(key="gpu", operator="Exists"),
            {"key": "spot", "operator": "Equal", "value": "true"},
        ]
        obj = _stub(
            template_id="t",
            image_id="img",
            max_instances=1,
            provider_api=None,
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=tols,
        )
        result = v.validate(obj)
        assert result.valid, result.errors


# ---------------------------------------------------------------------------
# Integration — full K8sTemplate round-trip
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_minimal_valid_k8s_template(self) -> None:
        v = _make_validator()
        t = K8sTemplate(template_id="my-pod-template", image_id="nginx:1.25", max_instances=10)
        result = v.validate(t)
        assert result.valid, result.errors

    def test_k8s_template_with_all_typed_fields(self) -> None:
        v = _make_validator()
        t = K8sTemplate(
            template_id="full-template",
            image_id="ghcr.io/example/worker:v2",
            max_instances=5,
            provider_api="Deployment",
            namespace="my-namespace",
            service_account="workload-sa",
            resource_requests=K8sResourceQuantities(cpu="500m", memory="256Mi"),
            resource_limits=K8sResourceQuantities(cpu="2", memory="2Gi"),
            tolerations=[K8sToleration(key="gpu", operator="Exists", effect="NoSchedule")],
        )
        result = v.validate(t)
        assert result.valid, result.errors

    def test_k8s_template_missing_image_id(self) -> None:
        v = _make_validator()
        t = K8sTemplate(template_id="no-image")
        result = v.validate(t)
        assert not result.valid
        assert any("image_id" in e for e in result.errors)

    def test_k8s_template_invalid_namespace(self) -> None:
        v = _make_validator()
        t = K8sTemplate(
            template_id="t",
            image_id="busybox:latest",
            namespace="UPPERCASE-BAD",  # type: ignore[arg-type]
        )
        result = v.validate(t)
        assert not result.valid
        assert any("namespace" in e for e in result.errors)

    def test_create_k8s_validator_factory_returns_instance(self) -> None:
        """create_k8s_validator() must return a K8sTemplateValidator, not None."""
        from orb.providers.k8s.registration import create_k8s_validator

        validator = create_k8s_validator()
        assert validator is not None
        assert isinstance(validator, K8sTemplateValidator)

    def test_create_k8s_validator_with_config_arg(self) -> None:
        """Passing a provider_config must still return a validator instance."""
        from orb.providers.k8s.registration import create_k8s_validator

        validator = create_k8s_validator(provider_config={"namespace": "default"})
        assert isinstance(validator, K8sTemplateValidator)

    def test_multiple_errors_accumulate(self) -> None:
        """All failing rules should be reported together, not short-circuited."""
        v = _make_validator()
        obj = _stub(
            template_id="",  # fails rule 1
            image_id=None,  # fails rule 4
            max_instances=0,  # fails rule 2
            provider_api="DaemonSet",  # fails rule 3
            namespace=None,
            service_account=None,
            resource_requests=None,
            resource_limits=None,
            tolerations=None,
        )
        result = v.validate(obj)
        assert not result.valid
        # Expect at least four errors.
        assert len(result.errors) >= 4
