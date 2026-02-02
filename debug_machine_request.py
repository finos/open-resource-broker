#!/usr/bin/env python3

import sys
import traceback
import asyncio
sys.path.insert(0, 'src')

async def test_machine_request():
    try:
        from infrastructure.di.container import get_container
        from infrastructure.di.buses import CommandBus
        from application.dto.commands import CreateRequestCommand
        
        container = get_container()
        command_bus = container.get(CommandBus)
        
        command = CreateRequestCommand(
            template_id="RunInstances-OnDemand",
            requested_count=1,
            request_type="provision"
        )
        
        print("Executing command...")
        result = await command_bus.execute(command)
        print("Result:", result)
        
    except Exception as e:
        print("ERROR:", str(e))
        print("FULL TRACEBACK:")
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_machine_request())
