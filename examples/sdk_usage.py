"""
SDK usage example for Open Resource Broker.

Shows the full lifecycle: initialize, list templates, request machines,
check status, and return machines.

Run with:
    python examples/sdk_usage.py
    python examples/sdk_usage.py --help
    python examples/sdk_usage.py --dry-run
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Ensure src/ is on the path when running directly from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orb.sdk import ORBClient
from orb.sdk.exceptions import ConfigurationError, MethodExecutionError, ProviderError, SDKError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open Resource Broker SDK usage example",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python examples/sdk_usage.py
  python examples/sdk_usage.py --config /path/to/config.json
  python examples/sdk_usage.py --dry-run
  python examples/sdk_usage.py --template my-template --count 3
        """,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to ORB config file (default: uses ORB_CONFIG_PATH env var or ~/.orb/config.json)",
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


def _print_json(label: str, data: object) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(data, indent=2, default=str))


async def demo_list_templates(sdk: ORBClient) -> list:
    """List available templates and return them."""
    print("\n[1] Listing available templates...")
    try:
        result = await sdk.list_templates(active_only=True)  # type: ignore[attr-defined]
        templates = result if isinstance(result, list) else result.get("templates", [])
        print(f"    Found {len(templates)} template(s)")
        for t in templates[:3]:  # show first 3
            tid = t.get("template_id") or t.get("id") or "<unknown>"
            name = t.get("name", "")
            print(f"    - {tid}  {name}")
        if len(templates) > 3:
            print(f"    ... and {len(templates) - 3} more")
        return templates
    except MethodExecutionError as e:
        print(f"    Could not list templates: {e.message}")
        return []


async def demo_request_machines(
    sdk: ORBClient, template_id: str, count: int, dry_run: bool
) -> str | None:
    """Request machines and return the request_id, or None on failure."""
    print(f"\n[2] Requesting {count} machine(s) from template '{template_id}'...")
    if dry_run:
        print("    [dry-run] Skipping actual request.")
        return None

    try:
        # Both parameter styles work — using the CLI-style alias here
        result = await sdk.create_request(  # type: ignore[attr-defined]
            template_id=template_id,
            count=count,  # maps to requested_count internally
        )
        if result is None:
            print("    Request submitted (no request_id returned — check status separately)")
            return None

        request_id = (
            result.get("request_id") or result.get("id") if isinstance(result, dict) else None
        )
        print(f"    Request created: {request_id}")
        return request_id
    except MethodExecutionError as e:
        print(f"    Request failed: {e.message}")
        return None


async def demo_check_status(sdk: ORBClient, request_id: str) -> None:
    """Check the status of a request."""
    print(f"\n[3] Checking status of request '{request_id}'...")
    try:
        result = await sdk.get_request(request_id=request_id)  # type: ignore[attr-defined]
        if result:
            status = result.get("status", "<unknown>") if isinstance(result, dict) else result
            print(f"    Status: {status}")
            _print_json("Request details", result)
    except MethodExecutionError as e:
        print(f"    Status check failed: {e.message}")


async def demo_return_machines(
    sdk: ORBClient, machine_ids: list[str], dry_run: bool
) -> None:
    """Return machines by ID."""
    print(f"\n[4] Returning {len(machine_ids)} machine(s)...")
    if dry_run:
        print("    [dry-run] Skipping actual return.")
        return
    if not machine_ids:
        print("    No machine IDs provided — skipping return step.")
        return

    try:
        result = await sdk.create_return_request(machine_ids=machine_ids)  # type: ignore[attr-defined]
        print(f"    Return request result: {result}")
    except MethodExecutionError as e:
        print(f"    Return failed: {e.message}")


async def main() -> int:
    args = parse_args()

    # Build SDK config
    sdk_kwargs: dict = {}
    if args.config:
        sdk_kwargs["config_path"] = args.config

    print("Open Resource Broker SDK — usage example")
    print("=" * 45)

    try:
        sdk = ORBClient(**sdk_kwargs)
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        return 1

    try:
        print("\nInitializing SDK...")
        await sdk.initialize()
        print(f"SDK ready. Provider: {sdk.provider}")

        stats = sdk.get_stats()
        print(
            f"Discovered {stats['methods_discovered']} methods "
            f"({stats.get('command_methods', 0)} commands, "
            f"{stats.get('query_methods', 0)} queries)"
        )

        # --show-methods: print all discovered methods and exit
        if args.show_methods:
            print("\nAvailable SDK methods:")
            for name in sorted(sdk.list_available_methods()):
                info = sdk.get_method_info(name)
                kind = f"[{info.handler_type}]" if info else ""
                print(f"  {name:40s} {kind}")
            return 0

        # Step 1: list templates
        templates = await demo_list_templates(sdk)

        # Determine template to use
        template_id = args.template
        if not template_id and templates:
            first = templates[0]
            template_id = first.get("template_id") or first.get("id")
            if template_id:
                print(f"\n    (Using first available template: {template_id})")

        # Step 2: request machines (only if we have a template)
        request_id = None
        if template_id:
            request_id = await demo_request_machines(sdk, template_id, args.count, args.dry_run)
        else:
            print("\n[2] No template available — skipping machine request.")

        # Step 3: check status
        if request_id:
            await demo_check_status(sdk, request_id)

        # Step 4: return machines (demo only — no real machine IDs without a real request)
        await demo_return_machines(sdk, [], args.dry_run)

        print("\nDone.")
        return 0

    except ProviderError as e:
        print(f"\nProvider error: {e}")
        print("Hint: run 'orb init' to configure a provider, or pass --config <path>.")
        return 1
    except SDKError as e:
        print(f"\nSDK error: {e}")
        return 1
    finally:
        await sdk.cleanup()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
