"""Health check command handler."""

from pathlib import Path
from typing import Any, cast

from orb.application.ports.scheduler_port import SchedulerPort
from orb.domain.base.ports.configuration_port import ConfigurationPort
from orb.domain.base.ports.console_port import ConsolePort
from orb.domain.base.ports.health_check_port import HealthCheckPort
from orb.infrastructure.di.container import get_container


def handle_health_check(args) -> int:
    """Handle health check command.

    Returns:
        0 if all checks pass, 1 if any fail
    """
    try:
        container = get_container()
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
                checks.append(
                    {
                        "name": name,
                        "status": cli_status,
                        "details": result.get("details", {}),
                    }
                )
        except Exception as e:
            checks.append({"name": "health_monitor", "status": "warn", "error": str(e)})

        # 1. Config file check
        config_path = config_port.get_config_file_path() or "./config/config.json"
        checks.append(
            {
                "name": "config_file",
                "status": "pass" if Path(config_path).exists() else "fail",
                "details": config_path,
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
                        "details": f"{len(existing_paths)} template files found: {', '.join(existing_paths)}",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "templates_file",
                        "status": "fail",
                        "details": f"No template files found. Checked: {', '.join(template_paths)}",
                    }
                )
        except Exception as e:
            checks.append({"name": "templates_file", "status": "fail", "error": str(e)})

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
            checks.append({"name": "templates_loaded", "status": "fail", "error": str(e)})

        # 4. Provider health check (provider-agnostic)
        try:
            from orb.application.services.provider_registry_service import ProviderRegistryService

            registry_service = container.get(ProviderRegistryService)
            all_providers = registry_service.get_available_strategies()

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
                errors = []

                for provider_name in all_providers:
                    try:
                        health_status = registry_service.check_strategy_health(provider_name)
                        if health_status and health_status.is_healthy:
                            healthy_count += 1
                        elif health_status:
                            errors.append(f"{provider_name}: {health_status.message}")
                        else:
                            errors.append(f"{provider_name}: No health data available")
                    except Exception as e:
                        errors.append(f"{provider_name}: {e!s}")

                if healthy_count == total_count:
                    status = "pass"
                    details = f"All {total_count} provider strategies healthy"
                elif healthy_count > 0:
                    status = "warn"
                    details = f"{healthy_count}/{total_count} providers healthy"
                else:
                    status = "warn"
                    details = f"No providers healthy. Errors: {'; '.join(errors)}"

                checks.append({"name": "provider_health", "status": status, "details": details})
        except Exception as e:
            checks.append({"name": "provider_health", "status": "warn", "error": str(e)})

        # 5. Work directory check
        try:
            scheduler = container.get(SchedulerPort)
            work_dir = scheduler.get_working_directory()
            checks.append(
                {
                    "name": "work_directory",
                    "status": "pass" if Path(work_dir).exists() else "fail",
                    "details": work_dir,
                }
            )
        except Exception as e:
            checks.append({"name": "work_directory", "status": "fail", "error": str(e)})

        # 6. Logs directory check
        try:
            scheduler = container.get(SchedulerPort)
            logs_dir = scheduler.get_logs_directory()
            checks.append(
                {
                    "name": "logs_directory",
                    "status": "pass" if Path(logs_dir).exists() else "fail",
                    "details": logs_dir,
                }
            )
        except Exception as e:
            checks.append({"name": "logs_directory", "status": "fail", "error": str(e)})

        # Format response using scheduler strategy
        response = cast(Any, scheduler_strategy).format_health_response(checks)

        # Console output for interactive use (not controlled by logging setting)
        if not cast(Any, scheduler_strategy).should_log_to_console():
            # HostFactory mode: only output JSON, no extra messages
            pass
        else:
            # Default mode: show human-readable output
            console = get_container().get(ConsolePort)
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

        # Always output JSON
        import json

        print(json.dumps(response, indent=2))

        return 0 if response["success"] else 1

    except Exception as e:
        get_container().get(ConsolePort).error(f"Health check failed: {e}")
        return 1
