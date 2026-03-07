"""
SDK usage example for Open Resource Broker.

Covers the full lifecycle: initialize, list templates, request machines,
check status, return machines, batch operations, middleware, and method
discovery.

Run with:
    python docs/root/examples/sdk_usage.py
    python docs/root/examples/sdk_usage.py --help
    python docs/root/examples/sdk_usage.py --dry-run
    python docs/root/examples/sdk_usage.py --show-methods
    python docs/root/examples/sdk_usage.py --template my-template --count 3
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure src/ is on the path when running directly from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from orb import ORBClient as orb
from orb.sdk import SDKMiddleware
from orb.sdk.exceptions import (
    ConfigurationError,
    HandlerDiscoveryError,
    MethodExecutionError,
    ProviderError,
    SDKError,
)

# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open Resource Broker SDK usage example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python docs/root/examples/sdk_usage.py
  python docs/root/examples/sdk_usage.py --config /path/to/config.json
  python docs/root/examples/sdk_usage.py --dry-run
  python docs/root/examples/sdk_usage.py --show-methods
  python docs/root/examples/sdk_usage.py --template my-template --count 3
        """,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to ORB config file (default: uses ORB_CONFIG_FILE env var or built-in defaults)",
    )
    parser.add_argument(
        "--template",
        metavar="ID",
        default=None,
        help="Template ID to use for machine request (skips request step if not provided)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of machines to request (default: 1)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would happen without making real requests",
    )
    parser.add_argument(
        "--show-methods",
        action="store_true",
        help="Print all discovered SDK methods and exit",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_json(label: str, data: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Config mode examples (shown as comments — not all run at once)
# ---------------------------------------------------------------------------
#
# Mode 1 — default (env vars / built-in defaults):
#   async with orb() as sdk: ...
#
# Mode 2 — SDK config dict (timeout, log_level, region, etc.):
#   async with orb(config={"provider": "aws", "region": "us-west-2", "timeout": 600}) as sdk: ...
#
# Mode 3 — config file on disk:
#   async with orb(config_path="/etc/orb/config.json") as sdk: ...
#
# Mode 4 — full app config in memory (Lambda, notebooks, CI — no filesystem):
#   app_cfg = {"provider": {"type": "aws", "providers": [{"name": "default", "type": "aws",
#               "region": "us-east-1"}]}, "storage": {"type": "json"}}
#   async with orb(app_config=app_cfg) as sdk: ...
#
# Mode 5 — env vars only (ORB_PROVIDER, ORB_REGION, ORB_CONFIG_FILE, etc.):
#   export ORB_PROVIDER=aws
#   export ORB_REGION=us-east-1
#   async with orb() as sdk: ...
#
# Modes can be combined: app_config= sets the application config structure while
# config= tunes SDK behaviour (timeout, log_level, etc.).


# ---------------------------------------------------------------------------
# Middleware example
# ---------------------------------------------------------------------------


class LoggingMiddleware(SDKMiddleware):
    """Logs every SDK method call and its result."""

    async def process(self, method_name, args, kwargs, next_handler):
        print(f"    [middleware] -> {method_name}({kwargs})")
        result = await next_handler(args, kwargs)
        print(f"    [middleware] <- {method_name} returned {type(result).__name__}")
        return result


# ---------------------------------------------------------------------------
# Demo steps
# ---------------------------------------------------------------------------


async def demo_method_discovery(sdk) -> None:
    """Print all discovered methods grouped by type."""
    print("\n[discovery] Available SDK methods:")
    stats = sdk.get_stats()
    print(
        f"  {stats['methods_discovered']} total  "
        f"({stats.get('command_methods', 0)} commands, "
        f"{stats.get('query_methods', 0)} queries)"
    )

    query_methods = sdk.get_methods_by_type("query")
    command_methods = sdk.get_methods_by_type("command")

    print(f"\n  Queries ({len(query_methods)}):")
    for name in sorted(query_methods):
        print(f"    {name}")

    print(f"\n  Commands ({len(command_methods)}):")
    for name in sorted(command_methods):
        print(f"    {name}")

    # Detailed info for one method
    info = sdk.get_method_info("list_templates")
    if info:
        print(f"\n  get_method_info('list_templates'): handler_type={info.handler_type}")


async def demo_list_templates(sdk) -> list:
    """List available templates and return them."""
    print("\n[1] Listing available templates...")
    try:
        # Convenience method: show_template(template_id) -> get_template(template_id=...)
        # CQRS method used here for listing:
        result = await sdk.list_templates(active_only=True)
        templates = result if isinstance(result, list) else result.get("templates", [])
        print(f"    Found {len(templates)} template(s)")
        for t in templates[:3]:
            tid = t.get("template_id") or t.get("id") or "<unknown>"
            name = t.get("name", "")
            print(f"    - {tid}  {name}")
        if len(templates) > 3:
            print(f"    ... and {len(templates) - 3} more")
        return templates
    except MethodExecutionError as e:
        print(f"    Could not list templates: {e.message}")
        return []


async def demo_request_machines(sdk, template_id: str, count: int, dry_run: bool) -> str | None:
    """Request machines and return the request_id, or None on failure."""
    print(f"\n[2] Requesting {count} machine(s) from template '{template_id}'...")
    if dry_run:
        print("    [dry-run] Skipping actual request.")
        return None

    try:
        # CLI-style alias: count maps to requested_count internally.
        # Both of these are equivalent:
        #   await sdk.create_request(template_id=template_id, count=count)
        #   await sdk.request_machines(template_id, count)   # convenience wrapper
        result = await sdk.create_request(template_id=template_id, count=count)
        if result is None:
            print("    Request submitted (no request_id returned — check status separately)")
            return None

        request_id = (
            result.get("created_request_id") or result.get("request_id") or result.get("id")
            if isinstance(result, dict)
            else None
        )
        print(f"    Request created: {request_id}")
        return request_id
    except MethodExecutionError as e:
        print(f"    Request failed: {e.message}")
        return None


async def demo_check_status(sdk, request_id: str) -> None:
    """Check the status of a request."""
    print(f"\n[3] Checking status of request '{request_id}'...")
    try:
        result = await sdk.get_request(request_id=request_id)
        if result:
            status = result.get("status", "<unknown>") if isinstance(result, dict) else result
            print(f"    Status: {status}")
            _print_json("Request details", result)
    except MethodExecutionError as e:
        print(f"    Status check failed: {e.message}")


async def demo_return_machines(sdk, machine_ids: list[str], dry_run: bool) -> None:
    """Return machines by ID."""
    print(f"\n[4] Returning {len(machine_ids)} machine(s)...")
    if dry_run:
        print("    [dry-run] Skipping actual return.")
        return
    if not machine_ids:
        print("    No machine IDs provided — skipping return step.")
        return

    try:
        result = await sdk.create_return_request(machine_ids=machine_ids)
        print(f"    Return request result: {result}")
    except MethodExecutionError as e:
        print(f"    Return failed: {e.message}")


async def demo_batch(sdk, template_id: str, dry_run: bool) -> None:
    """Demonstrate batch operations — concurrent requests in one call."""
    print("\n[5] Batch operations demo...")
    if dry_run or not template_id:
        print("    [dry-run / no template] Skipping batch demo.")
        return

    # sdk.batch() runs all coroutines concurrently via asyncio.gather.
    # Failures are returned as exception instances, not raised.
    results = await sdk.batch(
        [
            sdk.create_request(template_id=template_id, count=1),
            sdk.create_request(template_id="nonexistent-template-for-demo", count=1),
            sdk.list_templates(active_only=True),
        ]
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"    operation[{i}] failed: {result}")
        else:
            print(f"    operation[{i}] succeeded: {type(result).__name__}")


async def demo_serialization(sdk) -> None:
    """Demonstrate raw_response and format serialization options."""
    print("\n[6] Serialization options...")
    try:
        # JSON string output
        json_str = await sdk.list_templates(format="json")
        if isinstance(json_str, str):
            print(f"    format='json' -> str of length {len(json_str)}")

        # YAML string output
        yaml_str = await sdk.list_templates(format="yaml")
        if isinstance(yaml_str, str):
            print(f"    format='yaml' -> str of length {len(yaml_str)}")

        # Raw handler result — no dict conversion, format= is ignored
        raw = await sdk.list_templates(raw_response=True)
        print(f"    raw_response=True -> {type(raw).__name__}")
    except MethodExecutionError as e:
        print(f"    Serialization demo skipped: {e.message}")


async def demo_health(sdk) -> None:
    """Check provider health using the convenience method."""
    print("\n[7] Provider health check...")
    try:
        # Convenience: health_check() -> get_provider_health()
        health = await sdk.health_check()
        print(f"    Health: {health}")
    except MethodExecutionError as e:
        print(f"    Health check failed: {e.message}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> int:
    args = parse_args()

    print("Open Resource Broker SDK — usage example")
    print("=" * 45)

    # Build SDK kwargs from CLI args
    sdk_kwargs: dict = {}
    if args.config:
        sdk_kwargs["config_path"] = args.config

    # Context manager is the recommended pattern — handles initialize() and
    # cleanup() automatically even if an exception is raised.
    try:
        async with orb(**sdk_kwargs) as sdk:
            print(f"\nSDK ready. Provider: {sdk.provider}")
            stats = sdk.get_stats()
            print(
                f"Discovered {stats['methods_discovered']} methods "
                f"({stats.get('command_methods', 0)} commands, "
                f"{stats.get('query_methods', 0)} queries)"
            )

            # --show-methods: print all discovered methods and exit
            if args.show_methods:
                await demo_method_discovery(sdk)
                return 0

            # Attach logging middleware (optional — remove for production use)
            # sdk.add_middleware(LoggingMiddleware())

            # Step 1: list templates
            templates = await demo_list_templates(sdk)

            # Resolve template to use
            template_id = args.template
            if not template_id and templates:
                first = templates[0]
                template_id = first.get("template_id") or first.get("id")
                if template_id:
                    print(f"\n    (Using first available template: {template_id})")

            # Step 2: request machines
            request_id = None
            if template_id:
                request_id = await demo_request_machines(sdk, template_id, args.count, args.dry_run)
            else:
                print("\n[2] No template available — skipping machine request.")

            # Step 3: check status
            if request_id:
                await demo_check_status(sdk, request_id)

            # Step 4: return machines (no real IDs without a live request)
            await demo_return_machines(sdk, [], args.dry_run)

            # Step 5: batch operations
            await demo_batch(sdk, template_id or "", args.dry_run)

            # Step 6: serialization options
            await demo_serialization(sdk)

            # Step 7: health check
            await demo_health(sdk)

            print("\nDone.")
            return 0

    except ConfigurationError as e:
        print(f"\nConfiguration error: {e}")
        print("Hint: pass --config <path> or set ORB_CONFIG_FILE.")
        return 1
    except ProviderError as e:
        print(f"\nProvider error: {e}")
        print("Hint: run 'orb init' to configure a provider, or pass --config <path>.")
        return 1
    except HandlerDiscoveryError as e:
        print(f"\nHandler discovery error: {e}")
        return 1
    except SDKError as e:
        print(f"\nSDK error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
