"""Infrastructure adapter for OCI example templates."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any, Optional

from orb.domain.base.ports.template_example_generator_port import TemplateExampleGeneratorPort


class OCITemplateExampleGeneratorAdapter(TemplateExampleGeneratorPort):
    """Generate OCI example templates from the provider-owned template catalog."""

    def generate_example_templates(
        self,
        provider_name: str,
        provider_api: Optional[str] = None,
    ) -> list[Any]:
        """Generate OCI example templates for the given provider instance."""
        text = (
            files("orb.providers.oci.config")
            .joinpath("oci_templates.json")
            .read_text(encoding="utf-8")
        )
        data = json.loads(text)
        templates = data.get("templates", [])
        if not isinstance(templates, list):
            return []

        generated = []
        for template in templates:
            if not isinstance(template, dict):
                continue
            if provider_api and template.get("provider_api") != provider_api:
                continue
            item = dict(template)
            item["provider_name"] = provider_name
            generated.append(item)
        return generated
