# API Layer

FastAPI-based REST interface for the Open Resource Broker.

## Structure

- `routers/machines.py` — `/api/v1/machines` endpoints (request, return, list, get)
- `routers/requests.py` — `/api/v1/requests` endpoints (list, status, stream, cancel)
- `routers/templates.py` — `/api/v1/templates` endpoints (CRUD, validate, refresh)
- `dependencies.py` — FastAPI dependency functions that resolve orchestrators and `SchedulerPort` from the DI container
- `models/base.py` — `APIRequest` base Pydantic model (camelCase alias support)

## Dispatch pattern

Each router endpoint receives an orchestrator and a `SchedulerPort` via `Depends(...)`:

```python
from orb.api.dependencies import get_list_templates_orchestrator, get_scheduler_strategy

result = await orchestrator.execute(ListTemplatesInput(...))
return JSONResponse(content=scheduler.format_templates_response(result.templates))
```

The orchestrator handles CQRS dispatch; `SchedulerPort` handles response formatting.

## Adding a new endpoint

1. Add an orchestrator dependency function to `dependencies.py`.
2. Add the route to the appropriate router (or create a new one under `routers/`).
3. Register the router in the FastAPI app in `bootstrap.py`.
