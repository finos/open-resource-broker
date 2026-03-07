"""Templates generate command handler."""

from typing import Any, Dict

from orb.application.dto.template_generation_dto import TemplateGenerationRequest
from orb.application.services.template_generation_service import TemplateGenerationService
from orb.cli.console import print_info, print_success
from orb.infrastructure.di.container import get_container


async def handle_templates_generate(args) -> Dict[str, Any]:
    """Handle orb templates generate command with multi-provider support."""
    try:
        # Get template generation service from DI container
        container = get_container()
        template_service = container.get(TemplateGenerationService)

        # Create request DTO from arguments
        request = TemplateGenerationRequest(
            specific_provider=getattr(args, "provider", None),
            all_providers=getattr(args, "all_providers", False),
            provider_api=getattr(args, "provider_api", None),
            provider_specific=getattr(args, "provider_specific", False),
            provider_type_filter=getattr(args, "provider_type", None),
            force_overwrite=getattr(args, "force", False),
        )

        # Use application service to generate templates
        result = await template_service.generate_templates(request)

        # Handle UI output
        _print_generation_results(result)

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
        import traceback

        # Print traceback to stderr but don't include in JSON response
        traceback.print_exc()

        return {
            "status": "error",
            "message": f"Failed to generate templates: {e}",
        }


def _print_generation_results(result) -> None:
    """Print generation results to console."""
    if result.status == "error":
        return

    skipped_providers = [p for p in result.providers if p.status == "skipped"]
    created_providers = [p for p in result.providers if p.status == "created"]

    # Print skipped files
    if skipped_providers:
        print_info(f"Skipped {len(skipped_providers)} existing files (use --force to overwrite):")
        for provider_result in skipped_providers:
            print_info(f"  - {provider_result.filename}")
        print_info("")

    # Print created results
    if created_providers:
        print_success(result.message)
        print_info(f"Total templates: {result.total_templates}")
        print_info("")

        for provider_result in created_providers:
            print_info(f"Provider: {provider_result.provider}")
            print_info(f"  File: {provider_result.filename}")
            print_info(f"  Templates: {provider_result.templates_count}")
    elif skipped_providers:
        print_info("No new templates generated (all files already exist)")
    else:
        print_info("No templates generated")
