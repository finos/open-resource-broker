# Orchestration layer

Orchestrators are thin coordinators that sit between the interface/API layers and the CQRS buses.

## Contract

Every orchestrator extends `OrchestratorBase[Input, Output]` from `base.py`:

```python
class OrchestratorBase(ABC, Generic[InputT, OutputT]):
    async def execute(self, input: InputT) -> OutputT: ...
```

The concrete class receives `CommandBusPort` and/or `QueryBusPort` via constructor injection,
dispatches one command or query, and returns a typed output DTO.

## DTOs

All input and output types live in `dtos.py` in this directory. Each orchestrator has a
matching `*Input` / `*Output` pair (e.g. `ListTemplatesInput` / `ListTemplatesOutput`).

## Rules

- Orchestrators never call `get_container()` — all dependencies are constructor-injected.
- Orchestrators never call `SchedulerPort` — response formatting is the interface layer's job.
- One orchestrator per operation; no shared state between calls.
