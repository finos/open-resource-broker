"""How a Kubernetes template row gets its type through the defaults layer and the factory.

``TemplateDefaultsService.resolve_template_defaults`` uses the row's
``provider_type``, or the configured type of the selected provider instance, to
choose which provider-type defaults to merge, and writes that type onto a row
that has none.  ``TemplateFactory.create_template(row)`` builds the provider's
template class only when the row carries ``provider_type``, so a row resolved
against an instance is typed by its instance, and a row built without an
instance is typed only by its own ``provider_type`` or by ``provider_type`` in
the type's ``template_defaults`` (which the merge copies onto the row).

These cases cover the Kubernetes provider, whose bundled ``template_defaults``
carry no ``provider_type``.
"""

from __future__ import annotations

import json
from importlib.resources import files
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from orb.application.services.template_defaults_service import TemplateDefaultsService
from orb.domain.template.factory import TemplateFactory
from orb.domain.template.template_aggregate import Template
from orb.providers.k8s.domain.template.k8s_template_aggregate import K8sTemplate
from orb.providers.k8s.registration import register_k8s_template_factory

ROW = {"template_id": "t1", "machine_image": "busybox:1", "namespace": "team-a"}


def _bundled_template_defaults() -> dict[str, Any]:
    text = files("orb.providers.k8s.config").joinpath("k8s_defaults.json").read_text("utf-8")
    return json.loads(text)["provider"]["provider_defaults"]["k8s"]["template_defaults"]


def _build(
    row: dict[str, Any], instance: Optional[str], template_defaults: dict[str, Any]
) -> tuple[dict[str, Any], Template]:
    config_manager = MagicMock()
    config_manager.get_template_config.return_value = {}
    config_manager.get_provider_config.return_value = SimpleNamespace(
        providers=[SimpleNamespace(name="k8s_ctx", type="k8s", template_defaults={})],
        provider_defaults={"k8s": SimpleNamespace(template_defaults=template_defaults)},
    )
    service = TemplateDefaultsService(config_manager=config_manager, logger=MagicMock())
    factory = TemplateFactory()
    register_k8s_template_factory(factory)
    resolved = service.resolve_template_defaults(row, instance)
    return resolved, factory.create_template(resolved)


@pytest.mark.unit
class TestKubernetesRowTyping:
    def test_scheduler_path_with_an_instance_types_an_untyped_row(self) -> None:
        resolved, template = _build(ROW, "k8s_ctx", _bundled_template_defaults())
        assert resolved["provider_type"] == "k8s"
        assert isinstance(template, K8sTemplate)
        assert template.namespace == "team-a"

    def test_explicit_provider_type_row_becomes_a_k8s_template(self) -> None:
        resolved, template = _build(
            {**ROW, "provider_type": "k8s"}, "k8s_ctx", _bundled_template_defaults()
        )
        assert resolved["provider_type"] == "k8s"
        assert isinstance(template, K8sTemplate)
        assert template.namespace == "team-a"

    def test_no_instance_untyped_row_is_a_core_template(self) -> None:
        # The three callers that build templates without a selected instance
        # (the repository, the template command and query handlers) get a core
        # Template for an untyped row: typed fields such as ``namespace`` are
        # dropped.  Rows reaching them must carry ``provider_type``.
        resolved, template = _build(ROW, None, _bundled_template_defaults())
        assert "provider_type" not in resolved
        assert type(template) is Template

    def test_no_instance_typed_row_becomes_a_k8s_template(self) -> None:
        _, template = _build({**ROW, "provider_type": "k8s"}, None, _bundled_template_defaults())
        assert isinstance(template, K8sTemplate)

    def test_provider_type_in_the_type_defaults_types_every_row_of_the_type(self) -> None:
        defaults = {**_bundled_template_defaults(), "provider_type": "k8s"}
        resolved, template = _build(ROW, "k8s_ctx", defaults)
        assert resolved["provider_type"] == "k8s"
        assert isinstance(template, K8sTemplate)
