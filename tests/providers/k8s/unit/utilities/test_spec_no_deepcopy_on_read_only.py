"""T05 — verify no copy.deepcopy on read-only spec-builder paths.

``build_deployment_spec`` and ``build_statefulset_spec`` only READ the
template/request; they never mutate the input spec.  Patching
``copy.deepcopy`` and asserting it is never called proves that the
read-only paths stay allocation-free (no hidden deep-copy overhead).

The deepcopy in
:func:`orb.providers.k8s.infrastructure.handlers.shared.label_stamper.stamp_native_workload_body`
is correctly scoped to the mutating path (native-spec stamping) and is
NOT exercised by the typed spec-builder functions tested here.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

from orb.domain.request.aggregate import Request
from orb.domain.request.value_objects import RequestId, RequestType
from orb.domain.template.template_aggregate import Template

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(requested_count: int = 3) -> Request:
    return Request(
        request_id=RequestId(value=f"req-{uuid.uuid4()}"),
        request_type=RequestType.ACQUIRE,
        provider_type="k8s",
        provider_api="Deployment",
        template_id="tpl-1",
        requested_count=requested_count,
        provider_data={},
    )


def _make_template(provider_api: str = "Deployment") -> Template:
    return Template(
        template_id="tpl-1",
        provider_type="k8s",
        provider_api=provider_api,
        image_id="busybox:latest",
        max_instances=5,
        provider_data={
            "k8s": {
                "namespace": "orb-test",
                "container_image": "busybox:latest",
                "resource_requests": {"cpu": "100m", "memory": "64Mi"},
            }
        },
    )


# ---------------------------------------------------------------------------
# T05 — no deepcopy on typed deployment spec build
# ---------------------------------------------------------------------------


def test_build_deployment_spec_does_not_call_deepcopy() -> None:
    """``build_deployment_spec`` must not call ``copy.deepcopy`` — it is a
    read-only builder: all output is freshly constructed SDK objects."""
    from orb.providers.k8s.utilities.deployment_spec import build_deployment_spec

    request = _make_request()
    template = _make_template()

    # Patch deepcopy in the deployment_spec module's own namespace so that any
    # accidental import-and-use inside the builder would be caught.
    with patch("orb.providers.k8s.utilities.deployment_spec.copy", create=True):
        # Also patch via the copy module itself to be thorough.
        with patch("copy.deepcopy") as mock_deepcopy:
            build_deployment_spec(
                template,
                request,
                deployment_name="orb-deadbeef",
                namespace="orb-test",
                replicas=3,
            )
            mock_deepcopy.assert_not_called()


def test_build_statefulset_spec_does_not_call_deepcopy() -> None:
    """``build_statefulset_spec`` must not call ``copy.deepcopy`` — it is a
    read-only builder: all output is freshly constructed SDK objects."""
    from orb.providers.k8s.utilities.statefulset_spec import build_statefulset_spec

    request = _make_request()
    template = _make_template(provider_api="StatefulSet")

    with patch("copy.deepcopy") as mock_deepcopy:
        build_statefulset_spec(
            template,
            request,
            statefulset_name="orb-deadbeef",
            namespace="orb-test",
            replicas=3,
        )
        mock_deepcopy.assert_not_called()


def test_stamp_native_workload_body_calls_deepcopy_exactly_once() -> None:
    """``stamp_native_workload_body`` MUST deepcopy — it mutates the body
    in-place on the copy.  Verify the deepcopy is scoped to the mutating
    branch and called exactly once per invocation."""
    import copy

    from orb.providers.k8s.infrastructure.handlers.shared.label_stamper import (
        stamp_native_workload_body,
    )

    request = _make_request()
    native_body: dict[str, Any] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"labels": {"app": "my-app"}},
        "spec": {"replicas": 1},
    }
    original_labels = dict(native_body["metadata"]["labels"])

    original_deepcopy = copy.deepcopy
    call_count = 0

    def _counting_deepcopy(obj: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return original_deepcopy(obj)

    with patch(
        "orb.providers.k8s.infrastructure.handlers.shared.label_stamper.copy.deepcopy",
        side_effect=_counting_deepcopy,
    ):
        result = stamp_native_workload_body(
            native_body,
            workload_name="orb-deadbeef",
            namespace="orb-test",
            replicas=3,
            request=request,
            label_prefix="orb.io",
        )

    assert call_count == 1, "deepcopy must be called exactly once (mutation guard)"
    # Original must be unmodified.
    assert native_body["metadata"]["labels"] == original_labels
    # Result carries the stamped labels.
    assert result["metadata"]["labels"]["orb.io/managed"] == "true"
    assert result["metadata"]["labels"]["orb.io/request-id"] == str(request.request_id)
