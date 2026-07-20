"""Unit tests for K8sTemplateValidationService (VALIDATE_TEMPLATE operation)."""

from __future__ import annotations

from unittest.mock import MagicMock

from orb.providers.base.strategy import ProviderOperation, ProviderOperationType
from orb.providers.k8s.services.template_validation_service import (
    K8sTemplateValidationService,
)


def _svc() -> K8sTemplateValidationService:
    return K8sTemplateValidationService(logger=MagicMock())


def _op(template_config: dict) -> ProviderOperation:
    return ProviderOperation(
        operation_type=ProviderOperationType.VALIDATE_TEMPLATE,
        parameters={"template_config": template_config},
    )


def test_missing_config_is_error() -> None:
    result = _svc().validate_template(_op({}))
    assert not result.success
    assert result.error_code == "MISSING_TEMPLATE_CONFIG"


def test_valid_pod_template() -> None:
    result = _svc().validate_template(
        _op({"template_id": "t", "image_id": "nginx", "provider_api": "Pod"})
    )
    assert result.success
    assert result.data["valid"] is True
    assert result.data["errors"] == []


def test_missing_image_is_invalid() -> None:
    result = _svc().validate_template(_op({"template_id": "t", "provider_api": "Pod"}))
    assert result.success  # operation succeeded; validation verdict is in data
    assert result.data["valid"] is False
    assert any("machine_image" in e for e in result.data["errors"])


def test_unknown_provider_api_is_invalid() -> None:
    result = _svc().validate_template(_op({"image_id": "nginx", "provider_api": "CronJob"}))
    assert result.data["valid"] is False
    assert any("provider_api" in e for e in result.data["errors"])


def test_job_rejects_always_restart_policy() -> None:
    result = _svc().validate_template(
        _op({"image_id": "busybox", "provider_api": "Job", "restart_policy": "Always"})
    )
    assert result.data["valid"] is False
    assert any("Always" in e for e in result.data["errors"])


def test_deployment_nonalways_restart_policy_warns_not_errors() -> None:
    result = _svc().validate_template(
        _op({"image_id": "nginx", "provider_api": "Deployment", "restart_policy": "Never"})
    )
    assert result.data["valid"] is True
    assert any("ignored" in w for w in result.data["warnings"])


def test_bogus_restart_policy_is_invalid() -> None:
    result = _svc().validate_template(
        _op({"image_id": "nginx", "provider_api": "Pod", "restart_policy": "Sometimes"})
    )
    assert result.data["valid"] is False


def test_non_positive_max_instances_is_invalid() -> None:
    result = _svc().validate_template(
        _op({"image_id": "nginx", "provider_api": "Pod", "max_instances": 0})
    )
    assert result.data["valid"] is False
