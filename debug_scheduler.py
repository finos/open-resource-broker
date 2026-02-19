#!/usr/bin/env python3
"""Debug script to test scheduler selection."""

import argparse
import asyncio


def test_scheduler_selection():
    """Test scheduler selection with CLI override."""

    # Simulate CLI args
    args = argparse.Namespace()
    args.scheduler = "default"
    args.config = None
    args.dry_run = False

    print("=== Testing Scheduler Selection ===")

    # Step 1: Get initial configuration
    from infrastructure.di.container import get_container
    from domain.base.ports.configuration_port import ConfigurationPort

    container = get_container()
    config = container.get(ConfigurationPort)

    print(f"1. Initial scheduler: {config.get_scheduler_strategy()}")

    # Step 2: Apply CLI override (like main.py does)
    config.override_scheduler_strategy(args.scheduler)
    print(f"2. After CLI override: {config.get_scheduler_strategy()}")

    # Step 3: Initialize application (like main.py does)
    from bootstrap import Application

    app = Application(args.config, skip_validation=False)

    async def init_app():
        return await app.initialize(dry_run=args.dry_run)

    result = asyncio.run(init_app())
    print(f"3. App initialized: {result}")

    # Step 4: Check configuration after app init
    config_after = container.get(ConfigurationPort)
    print(f"4. After app init: {config_after.get_scheduler_strategy()}")

    # Step 5: Get scheduler strategy (like handler does)
    from domain.base.ports.scheduler_port import SchedulerPort

    scheduler = container.get(SchedulerPort)
    print(f"5. Scheduler strategy: {type(scheduler).__name__}")

    # Step 6: Test format method
    mock_data = []  # Empty list for testing
    try:
        result = scheduler.format_request_status_response(mock_data)
        print(f"6. Format method works: {type(result)}")
        print(f"   Result keys: {list(result.keys()) if isinstance(result, dict) else 'Not dict'}")
    except Exception as e:
        print(f"6. Format method error: {e}")

    return scheduler


if __name__ == "__main__":
    scheduler = test_scheduler_selection()
    print(f"\nFinal scheduler type: {type(scheduler).__name__}")
