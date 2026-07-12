"""Regression tests for StatefulSet ``spec.serviceName`` resolution (Fix 2).

The adversarial review found that ``_resolve_service_name`` was using
``service_account`` (a ``v1/ServiceAccount``) as the headless-Service name
(``v1/Service``), producing invalid pod DNS.  The fix adds a dedicated
``service_name`` field to K8sTemplate (and its DTO mirror) and uses that
instead, falling back to the StatefulSet's own name when unset.
"""

from __future__ import annotations

import uuid

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.utilities.statefulset_spec import _resolve_service_name

# ---------------------------------------------------------------------------
# Unit tests for _resolve_service_name directly
# ---------------------------------------------------------------------------


def _k8s_template(**kwargs) -> K8sTemplate:  # type: ignore[no-untyped-def]
    return K8sTemplate(
        template_id="tpl-1",
        provider_api="StatefulSet",
        image_id="busybox:latest",
        max_instances=5,
        **kwargs,
    )


def test_service_name_field_takes_precedence_over_fallback() -> None:
    """When service_name is set it is returned as spec.serviceName."""
    tmpl = _k8s_template(service_name="my-headless-svc")
    result = _resolve_service_name(tmpl, fallback="orb-deadbeef")
    assert result == "my-headless-svc"


def test_service_name_falls_back_to_statefulset_name_when_unset() -> None:
    """When service_name is not set, fallback (StatefulSet name) is used."""
    tmpl = _k8s_template()  # service_name=None
    result = _resolve_service_name(tmpl, fallback="orb-deadbeef")
    assert result == "orb-deadbeef"


def test_service_account_is_not_used_as_service_name() -> None:
    """service_account must NOT be used as spec.serviceName (wrong resource type).

    This is the regression test for the original bug: _resolve_service_name was
    returning service_account, which is a v1/ServiceAccount, not a v1/Service.
    Pods that used the ServiceAccount name as the headless-Service name got
    NXDOMAIN on stable-DNS lookups.
    """
    tmpl = _k8s_template(service_account="my-sa")
    # service_name not set → must fall back to statefulset name, NOT service_account
    result = _resolve_service_name(tmpl, fallback="orb-deadbeef")
    assert result == "orb-deadbeef", (
        "service_account ('my-sa') must NOT be used as spec.serviceName. "
        "Only the 'service_name' field or the StatefulSet name itself are valid."
    )
    assert result != "my-sa"


def test_service_name_and_service_account_both_set_uses_service_name() -> None:
    """When both service_name and service_account are set, service_name wins."""
    tmpl = _k8s_template(service_name="headless-svc", service_account="my-sa")
    result = _resolve_service_name(tmpl, fallback="orb-deadbeef")
    assert result == "headless-svc"
    assert result != "my-sa"


# ---------------------------------------------------------------------------
# Integration: build_statefulset_spec uses service_name field
# ---------------------------------------------------------------------------


def _build_request() -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="StatefulSet",
        template_id="tpl-1",
        requested_count=3,
    )


def test_build_statefulset_spec_uses_service_name_field() -> None:
    """build_statefulset_spec sets spec.serviceName from template.service_name."""
    from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

    request = _build_request()
    template = _k8s_template(service_name="my-headless-svc")
    spec = build_statefulset_spec(
        template,
        request,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
        replicas=3,
    )
    assert spec.spec.service_name == "my-headless-svc"


def test_build_statefulset_spec_falls_back_to_statefulset_name() -> None:
    """Without service_name, spec.serviceName equals the StatefulSet name."""
    from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

    request = _build_request()
    template = _k8s_template()  # no service_name, no service_account
    spec = build_statefulset_spec(
        template,
        request,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
        replicas=3,
    )
    assert spec.spec.service_name == "orb-deadbeef"


def test_build_statefulset_spec_does_not_use_service_account_as_service_name() -> None:
    """Regression: service_account must not be used as spec.serviceName."""
    from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

    request = _build_request()
    template = _k8s_template(service_account="my-sa")  # service_name not set
    spec = build_statefulset_spec(
        template,
        request,
        statefulset_name="orb-deadbeef",
        namespace="orb-test",
        replicas=3,
    )
    assert spec.spec.service_name == "orb-deadbeef", (
        "spec.serviceName must be the StatefulSet name, not service_account 'my-sa'"
    )
    assert spec.spec.service_name != "my-sa"


# ---------------------------------------------------------------------------
# DTO round-trip: service_name survives DTO serialisation
# ---------------------------------------------------------------------------


def test_service_name_survives_dto_config_promotion() -> None:
    """service_name set via provider_config dict is promoted onto K8sTemplate."""
    tmpl = K8sTemplate(
        template_id="tpl-1",
        provider_api="StatefulSet",
        image_id="busybox:latest",
        max_instances=5,
        provider_config={"service_name": "svc-from-dto"},
    )
    assert tmpl.service_name == "svc-from-dto"


__all__: list[str] = []
