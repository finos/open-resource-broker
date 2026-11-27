"""Templates generate command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Dict

from orb.application.dto.template_generation_dto import TemplateGenerationRequest

if TYPE_CHECKING:
    from orb.application.services.template_generation_service import TemplateGenerationService
    from orb.domain.base.ports.console_port import ConsolePort


class TemplatesGenerateHandler:
    """Generates provider template files for ``orb templates generate``.

    The service and the console are providers, not instances: resolving either
    can fail, and that failure is reported in the command's error result rather
    than aborting the command before it starts.
    """

    def __init__(
        self,
        template_service_provider: Callable[[], TemplateGenerationService],
        console_provider: Callable[[], ConsolePort],
    ) -> None:
        self._template_service_provider = template_service_provider
        self._console_provider = console_provider

    async def handle(self, args) -> Dict[str, Any]:
        """Handle orb templates generate command with multi-provider support."""
        try:
            template_service = self._template_service_provider()

            # Create request DTO from arguments
            request = TemplateGenerationRequest(
                specific_provider=getattr(args, "provider_name", None),
                all_providers=getattr(args, "all_providers", False),
                provider_api=getattr(args, "provider_api", None),
                provider_specific=getattr(args, "provider_specific", False),
                provider_type_filter=getattr(args, "provider_type", None),
                force_overwrite=getattr(args, "force", False),
                slurm_conf=getattr(args, "slurm_conf", None),
            )

            # Use application service to generate templates
            result = await template_service.generate_templates(request)

            # Handle UI output
            self._print_generation_results(result)

            # Convert result to dict for CLI compatibility
            return {
                "status": result.status,
                "message": result.message,
                "providers": [
                    {
                        "provider": p.provider,
                        "filename": p.filename,
                        "templates_count": p.templates_count,
                        "path": p.path,
                        "status": p.status,
                        "reason": p.reason,
                    }
                    for p in result.providers
                ],
                "total_templates": result.total_templates,
                "created_count": result.created_count,
                "skipped_files": [p.filename for p in result.providers if p.status == "skipped"],
            }

        except Exception as e:
            return {
                "error": str(e),
                "status": "error",
                "success": False,
            }

    def _print_generation_results(self, result) -> None:
        """Print generation results to console."""
        if result.status == "error":
            return

        console = self._console_provider()
        skipped_providers = [p for p in result.providers if p.status == "skipped"]
        created_providers = [p for p in result.providers if p.status == "created"]

        if skipped_providers:
            console.info(
                f"Skipped {len(skipped_providers)} existing files (use --force to overwrite):"
            )
            for provider_result in skipped_providers:
                console.info(f"  - {provider_result.filename}")
            console.info("")

        if created_providers:
            console.success(result.message)
            console.info(f"Total templates: {result.total_templates}")
            console.info("")

            for provider_result in created_providers:
                console.info(f"Provider: {provider_result.provider}")
                console.info(f"  File: {provider_result.filename}")
                console.info(f"  Templates: {provider_result.templates_count}")
        elif skipped_providers:
            console.info("No new templates generated (all files already exist)")
        else:
            console.info("No templates generated")
