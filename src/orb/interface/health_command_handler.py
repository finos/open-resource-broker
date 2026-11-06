"""Health check command handler."""

import asyncio
from pathlib import Path
from typing import Any, cast

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)

# Health output is returned to API, CLI and MCP callers, so it carries generic
# status text only. Exception text and provider messages (which can embed
# account identifiers or credentials hints) are written to the log instead.
_CHECK_FAILED = "Check failed; see server logs for details"
_PROVIDER_UNHEALTHY = "unhealthy; see server logs for details"


def _sanitize_details(details: Any) -> Any:
    """Replace any raw error text in monitor details with a generic message."""
    if isinstance(details, dict) and "error" in details:
        return {**details, "error": _CHECK_FAILED}
    return details


async def _probe_all(registry_service: Any, provider_names: list[str]) -> list[Any]:
    return list(
        await asyncio.gather(*(registry_service.probe_strategy_health(n) for n in provider_names))
    )


def handle_health_check(args) -> dict[str, Any]:
    """Handle health check command.

    Returns:
        Response dict with health check results
    """
    container = args._container
    try:
        scheduler_strategy = container.get(SchedulerPort)
        config_port = container.get(ConfigurationPort)

        checks = []

        # 0. System/disk/database/application checks via HealthCheckPort
        try:
            health_port = container.get(HealthCheckPort)
            hc_results = health_port.run_all_checks()
            for name, result in hc_results.items():
                hc_status = result.get("status", "unknown")
                # Map HealthCheck statuses to CLI check statuses
                if hc_status == "healthy":
                    cli_status = "pass"
                elif hc_status == "unknown":
                    cli_status = "warn"
                else:
                    cli_status = "fail"
                if cli_status == "fail" and isinstance(result.get("details"), dict):
                    if result["details"].get("error"):
                        logger.warning(
                            "Health check %s failed: %s", name, result["details"]["error"]
                        )
                checks.append(
                    {
                        "name": name,
                        "status": cli_status,
                        "details": _sanitize_details(result.get("details", {})),
                    }
                )
        except Exception as e:
            logger.warning("Health monitor checks failed: %s", e)
            checks.append({"name": "health_monitor", "status": "warn", "error": _CHECK_FAILED})

        # 1. Config file check
        # The file the configuration was actually loaded from; the configured
        # path and the relative default only when no file was loaded.
        config_path = (
            config_port.get_loaded_config_file()
            or config_port.get_config_file_path()
            or "./config/config.json"
        )
        config_exists = Path(config_path).exists()
        if not config_exists:
            logger.warning("Configuration file not found: %s", config_path)
        checks.append(
            {
                "name": "config_file",
                "status": "pass" if config_exists else "fail",
                "details": "Configuration file found"
                if config_exists
                else "Configuration file not found",
            }
        )

        # 2. Templates file check
        try:
            scheduler = container.get(SchedulerPort)
            template_paths = cast(Any, scheduler).get_template_paths()

            # Check if any template file exists
            existing_paths = [path for path in template_paths if Path(path).exists()]

            if existing_paths:
                checks.append(
                    {
                        "name": "templates_file",
                        "status": "pass",
                        "details": f"{len(existing_paths)} template files found",
                    }
                )
            else:
                logger.warning("No template files found. Checked: %s", ", ".join(template_paths))
                checks.append(
                    {
                        "name": "templates_file",
                        "status": "fail",
                        "details": "No template files found",
                    }
                )
        except Exception as e:
            logger.warning("Templates file check failed: %s", e)
            checks.append({"name": "templates_file", "status": "fail", "error": _CHECK_FAILED})

        # 3. Templates loaded check
        try:
            from orb.domain.template.repository import TemplateRepository

            repo = container.get(TemplateRepository)
            templates = repo.find_active_templates()  # Use interface method
            checks.append(
                {
                    "name": "templates_loaded",
                    "status": "pass" if templates else "warn",
                    "details": f"{len(templates)} templates",
                }
            )
        except Exception as e:
            logger.warning("Templates loaded check failed: %s", e)
            checks.append({"name": "templates_loaded", "status": "fail", "error": _CHECK_FAILED})

        # 4. Provider health check (provider-agnostic)
        try:
            from orb.application.services.provider_registry_service import ProviderRegistryService

            registry_service = container.get(ProviderRegistryService)
            # The configured provider instances, as REST /providers/health
            # probes; registered provider types without an instance are not
            # something this deployment can be unhealthy on.
            all_providers = [p.name for p in config_port.get_active_providers()]

            if not all_providers:
                checks.append(
                    {
                        "name": "provider_health",
                        "status": "warn",
                        "details": "No provider strategies configured",
                    }
                )
            else:
                # Check all configured providers
                healthy_count = 0
                total_count = len(all_providers)
                unhealthy: list[str] = []

                # One bounded, cached probe per provider, all at once, the same
                # probe the REST endpoint uses: a hung provider API costs one
                # time limit in total, not one per provider or per check.
                snapshots = asyncio.run(_probe_all(registry_service, list(all_providers)))
                for provider_name, snapshot in zip(all_providers, snapshots):
                    if snapshot.status == "healthy":
                        healthy_count += 1
                    else:
                        logger.warning(
                            "Provider %s is %s: %s",
                            provider_name,
                            snapshot.status,
                            snapshot.message,
                        )
                        unhealthy.append(provider_name)

                if healthy_count == total_count:
                    status = "pass"
                    details = f"All {total_count} provider strategies healthy"
                elif healthy_count > 0:
                    status = "warn"
                    details = f"{healthy_count}/{total_count} providers healthy"
                else:
                    status = "warn"
                    details = (
                        f"No providers healthy ({', '.join(unhealthy)}): {_PROVIDER_UNHEALTHY}"
                    )

                checks.append({"name": "provider_health", "status": status, "details": details})
        except Exception as e:
            logger.warning("Provider health check failed: %s", e)
            checks.append({"name": "provider_health", "status": "warn", "error": _CHECK_FAILED})

        # 5. Work directory check
        try:
            scheduler = container.get(SchedulerPort)
            work_dir = scheduler.get_working_directory()
            work_dir_exists = Path(work_dir).exists()
            if not work_dir_exists:
                logger.warning("Work directory not found: %s", work_dir)
            checks.append(
                {
                    "name": "work_directory",
                    "status": "pass" if work_dir_exists else "fail",
                    "details": "Work directory found"
                    if work_dir_exists
                    else "Work directory not found",
                }
            )
        except Exception as e:
            logger.warning("Work directory check failed: %s", e)
            checks.append({"name": "work_directory", "status": "fail", "error": _CHECK_FAILED})

        # 6. Logs directory check
        try:
            scheduler = container.get(SchedulerPort)
            logs_dir = scheduler.get_logs_directory()
            logs_dir_exists = Path(logs_dir).exists()
            if not logs_dir_exists:
                logger.warning("Logs directory not found: %s", logs_dir)
            checks.append(
                {
                    "name": "logs_directory",
                    "status": "pass" if logs_dir_exists else "fail",
                    "details": "Logs directory found"
                    if logs_dir_exists
                    else "Logs directory not found",
                }
            )
        except Exception as e:
            logger.warning("Logs directory check failed: %s", e)
            checks.append({"name": "logs_directory", "status": "fail", "error": _CHECK_FAILED})

        # Format response using scheduler strategy
        response = cast(Any, scheduler_strategy).format_health_response(checks)

        # Console output for interactive use (not controlled by logging setting)
        if not cast(Any, scheduler_strategy).should_log_to_console():
            # HostFactory mode: only output JSON, no extra messages
            pass
        else:
            # Default mode: show human-readable output
            console = container.get(ConsolePort)
            console.info("ORB Health Check:")
            for check in checks:
                status_text = check["status"].upper()
                name = check["name"].replace("_", " ").title()
                if check["status"] == "pass":
                    console.success(f"  {status_text}: {name}")
                elif check["status"] == "warn":
                    console.warning(f"  {status_text}: {name} - {check.get('error', 'Warning')}")
                else:
                    console.error(f"  {status_text}: {name} - {check.get('error', 'Failed')}")

            summary = response["summary"]
            console.info(f"\nSummary: {summary['passed']}/{summary['total']} checks passed")

        return response

    except Exception as e:
        logger.error("Health check failed: %s", e, exc_info=True)
        container.get(ConsolePort).error("Health check failed; see server logs for details")
        return {
            "success": False,
            "message": "Health check failed",
            "error": "Health check failed",
        }
