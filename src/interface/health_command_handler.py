"""Health check command handler."""

import os
from pathlib import Path

from cli.console import print_success, print_error, print_info, print_warning
from domain.base.ports.scheduler_port import SchedulerPort
from infrastructure.di.container import get_container


def handle_health_check(args) -> int:
    """Handle health check command.

    Returns:
        0 if all checks pass, 1 if any fail
    """
    try:
        container = get_container()
        scheduler_strategy = container.get(SchedulerPort)

        checks = []

        # 1. Config file check
        config_path = os.environ.get("ORB_CONFIG_FILE") or "./config/config.json"
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
            templates_path = scheduler.get_templates_file_path()
            checks.append(
                {
                    "name": "templates_file",
                    "status": "pass" if Path(templates_path).exists() else "fail",
                    "details": templates_path,
                }
            )
        except Exception as e:
            checks.append({"name": "templates_file", "status": "fail", "error": str(e)})

        # 3. Templates loaded check
        try:
            from domain.template.repository import TemplateRepository
            
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
            from application.queries.system import GetSystemStatusQuery
            from infrastructure.di.buses import QueryBus

            query_bus = container.get(QueryBus)
            query = GetSystemStatusQuery()
            status = query_bus.execute_sync(query)

            checks.append(
                {
                    "name": "provider_health",
                    "status": "pass" if status.get("healthy") else "warn",
                    "details": status.get("message", "Provider check completed"),
                }
            )
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
        response = scheduler_strategy.format_health_response(checks)

        # Console output for interactive use (not controlled by logging setting)
        if not scheduler_strategy.should_log_to_console():
            # HostFactory mode: only output JSON, no extra messages
            pass
        else:
            # Default mode: show human-readable output
            print_info("ORB Health Check:")
            for check in checks:
                status_text = check["status"].upper()
                name = check["name"].replace("_", " ").title()
                if check["status"] == "pass":
                    print_success(f"  {status_text}: {name}")
                elif check["status"] == "warn":
                    print_warning(f"  {status_text}: {name} - {check.get('error', 'Warning')}")
                else:
                    print_error(f"  {status_text}: {name} - {check.get('error', 'Failed')}")

            summary = response["summary"]
            print_info(f"\nSummary: {summary['passed']}/{summary['total']} checks passed")

        # Always output JSON
        import json

        print(json.dumps(response, indent=2))

        return 0 if response["success"] else 1

    except Exception as e:
        print_error(f"Health check failed: {e}")
        return 1
