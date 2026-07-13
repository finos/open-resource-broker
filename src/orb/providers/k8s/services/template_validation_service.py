"""Kubernetes template validation service.

Mirrors :class:`orb.providers.aws.services.template_validation_service.AWSTemplateValidationService`
so the ``VALIDATE_TEMPLATE`` provider operation returns a structured result for
the kubernetes provider instead of ``UNSUPPORTED_OPERATION``.

Like the AWS service, validation runs against the raw ``template_config`` dict
(snake_case keys, as the application layer supplies) rather than constructing a
:class:`K8sTemplate` aggregate — this keeps validation side-effect-free and
robust to partially-specified configs.
"""

from __future__ import annotations

from typing import Any

from orb.domain.base.ports import LoggingPort
from orb.providers.base.strategy import ProviderOperation, ProviderResult

# Workload kinds the k8s provider supports (provider_api values).
_SUPPORTED_APIS: frozenset[str] = frozenset({"Pod", "Deployment", "StatefulSet", "Job"})

# restartPolicy values the Kubernetes API accepts.
_RESTART_POLICIES: frozenset[str] = frozenset({"Always", "OnFailure", "Never"})


class K8sTemplateValidationService:
    """Service for kubernetes template validation operations."""

    def __init__(self, logger: LoggingPort) -> None:
        self._logger = logger

    def validate_template(self, operation: ProviderOperation) -> ProviderResult:
        """Handle the ``VALIDATE_TEMPLATE`` operation for the kubernetes provider."""
        try:
            template_config = operation.parameters.get("template_config", {})
            if not template_config:
                return ProviderResult.error_result(
                    "Template configuration is required for validation",
                    "MISSING_TEMPLATE_CONFIG",
                )
            return ProviderResult.success_result(
                self._validate_k8s_template(template_config),
                {"operation": "validate_template"},
            )
        except Exception as e:
            return ProviderResult.error_result(
                f"Failed to validate template: {e}", "VALIDATE_TEMPLATE_ERROR"
            )

    def _validate_k8s_template(self, template_config: dict[str, Any]) -> dict[str, Any]:
        """Validate kubernetes-specific template configuration (dict form)."""
        errors: list[str] = []
        warnings: list[str] = []

        # A container image is mandatory — the kubelet needs something to pull.
        if not template_config.get("image_id") and not template_config.get("container_image"):
            errors.append("Missing required field: image_id")

        # provider_api, when set, must be a supported workload kind.
        provider_api = template_config.get("provider_api")
        if provider_api and provider_api not in _SUPPORTED_APIS:
            errors.append(
                f"Invalid provider_api {provider_api!r}; supported: {sorted(_SUPPORTED_APIS)!r}."
            )

        # restart_policy, when set, must be a legal Kubernetes value.  Per-kind
        # validity (Deployment/StatefulSet require Always, Job rejects Always) is
        # enforced at spec-build time and only warned about here.
        restart_policy = template_config.get("restart_policy")
        if restart_policy and restart_policy not in _RESTART_POLICIES:
            errors.append(
                f"Invalid restart_policy {restart_policy!r}; "
                f"allowed: {sorted(_RESTART_POLICIES)!r}."
            )
        elif (
            restart_policy
            and provider_api in ("Deployment", "StatefulSet")
            and (restart_policy != "Always")
        ):
            warnings.append(
                f"restart_policy={restart_policy!r} is ignored for {provider_api}; "
                "the Kubernetes API requires 'Always' for that kind."
            )
        elif restart_policy == "Always" and provider_api == "Job":
            errors.append(
                "restart_policy='Always' is not valid for a Job (use Never or OnFailure)."
            )

        # max_instances, when set, must be a positive integer.
        max_instances = template_config.get("max_instances")
        if max_instances is not None:
            try:
                if int(max_instances) <= 0:
                    errors.append("max_instances must be a positive integer when set.")
            except (TypeError, ValueError):
                errors.append(f"max_instances must be an integer; got {max_instances!r}.")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "validated_fields": list(template_config.keys()),
        }


__all__ = ["K8sTemplateValidationService"]
