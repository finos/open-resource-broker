"""GCP template examples for the shared generation service."""

from __future__ import annotations

from orb.providers.gcp.domain.template.gcp_template_aggregate import GCPTemplate


class GCPTemplateExampleGeneratorAdapter:
    """Generate valid examples for each supported GCP handler."""

    def generate_example_templates(
        self, provider_name: str, provider_api: str | None = None
    ) -> list[dict[str, object]]:
        """Return GCP templates, optionally restricted to one provider API."""
        common: dict[str, object] = {
            "provider_name": provider_name,
            "project_id": "example-project-12345",
            "region": "us-central1",
            "machine_type": "e2-standard-4",
            "source_image_family": "debian-12",
            "source_image_project": "debian-cloud",
        }
        examples = [
            GCPTemplate.model_validate(
                {
                    **common,
                    "template_id": "gcp-mig-example",
                    "provider_api": "MIG",
                    "max_machines": 2,
                    "zones": ["us-central1-a", "us-central1-b"],
                }
            ),
            GCPTemplate.model_validate(
                {
                    **common,
                    "template_id": "gcp-single-vm-example",
                    "provider_api": "SingleVM",
                    "max_machines": 1,
                    "zones": ["us-central1-a"],
                }
            ),
        ]
        return [
            template.model_dump(
                mode="json", exclude_none=True, exclude={"created_at", "updated_at"}
            )
            for template in examples
            if provider_api is None or template.provider_api.value == provider_api
        ]
