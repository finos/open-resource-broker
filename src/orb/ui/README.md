# ORB UI (Reflex)

Python-based web UI for the Open Resource Broker. Built on
[Reflex](https://reflex.dev) (which compiles to React + Tailwind v4 +
Radix Themes under the hood). Lives inside the main `orb` package so
the UI imports ORB DTOs and orchestrators directly — no OpenAPI codegen,
no contract drift.

## Pages

| Route | Module | Purpose |
|-------|--------|---------|
| `/` | `pages/dashboard.py` | Stat cards (machines, requests, templates) + recent activity feed |
| `/machines` | `pages/machines.py` | Table, status filter, bulk select, return, detail drawer |
| `/requests` | `pages/requests.py` | Filter tabs, progress bars, cancel, detail drawer |
| `/templates` | `pages/templates.py` | Card grid, create/edit form, validate, delete, detail drawer |
| `/config` | `pages/config.py` | Read-only server info + health check status |

## Modes

Two deployment modes selected by `ui.mode` config field (or `ORB_MODE`
env var, env wins):

| Mode | What happens | When to use |
|------|--------------|-------------|
| `embedded` (default) | Reflex backend hosts ORB FastAPI via `api_transformer`. UI calls ORB orchestrators **directly in-process** — no HTTP, no JSON. Single process, single backend port. | Dev, demos, single-tenant deploys |
| `remote` | Reflex runs standalone, talks to a remote ORB over HTTP via `httpx`. Set `base_url` to the remote ORB. | Prod with split UI/API tiers, multi-region UI fronting central ORB |

## Install

```bash
# Adds reflex + httpx to ORB
pip install -e ".[ui]"
```

The `[ui]` extra implies `[api]` (FastAPI is required for the
`api_transformer` mount in embedded mode).

## Running

### Via `orb server start` (recommended)

Set `ui.enabled=true` in your ORB config and run:

```bash
# Daemonised (writes PID to <work_dir>/server/orb-server.pid)
orb --config /path/to/config.json server start

# Or foreground (blocks the shell, useful in containers / dev)
orb --config /path/to/config.json server start --foreground
```

When `ui.enabled` and `ui.mode=embedded`, ORB hands off to
`reflex run` automatically; the Reflex backend mounts ORB's FastAPI
app and serves both the UI websocket and the REST API on the same
port (`ui.backend_port`, default `8001` — set to `8000` to share
with `server.port`).

Example config snippet:

```json
{
  "ui": {
    "enabled": true,
    "mode": "embedded",
    "backend_port": 8000,
    "frontend_port": 3000
  }
}
```

When `ui.enabled=false` (default), `orb server start` runs uvicorn
with the FastAPI app as before — UI is not started. Pass
`--api-only` to skip the UI even when `ui.enabled=true`.

### Via `reflex run` (dev convenience)

```bash
cd <repo-root>
ORB_MODE=embedded ORB_UI_BACKEND_PORT=8000 reflex run
```

Same outcome as above without the ORB CLI bootstrap.

### Remote mode

```bash
ORB_MODE=remote ORB_BASE_URL=https://orb.internal:8000 reflex run
```

Reflex backend on `:ui.backend_port` only handles the UI websocket;
page handlers httpx → remote ORB.

## Production build

```bash
reflex export --frontend-only --no-zip --no-ssr
# → static SPA in .web/_static/
```

The `--no-ssr` flag is **required**: the `vaul` library (used by
`rx.drawer` in machine/request/template detail panels) reads
`document` at module scope, which crashes node-side prerender. SPA
hydration on the client works fine.

Serve the static bundle from any HTTP server, or mount it on the
ORB FastAPI app at `/` for a true single-port deployment.

## Architecture

```
src/orb/ui/
  app.py              # rx.App() + routes + api_transformer wiring
  api.py              # facade — picks api_inproc vs api_http by ORB_MODE
  api_inproc.py       # embedded mode: direct orchestrator calls via DI
  api_http.py         # remote mode: httpx wrappers per ORB endpoint
  state.py            # AppState — health/info polling, server status badge
  components/
    layout.py         # sidebar + topbar + page() shell helper
    machine_drawer.py
    request_drawer.py
    template_drawer.py
    template_form.py
    status_badge.py
    confirm_modal.py
    empty_state.py
    error_callout.py
    json_view.py
  pages/
    dashboard.py
    machines.py
    requests.py
    templates.py
    config.py
```

### Embedded mode call chain

```
Browser
  ↓ (websocket)
Reflex backend (port 8000)
  ↓ (Python function call)
api_inproc.list_machines()
  ↓
ListMachinesOrchestrator.execute(ListMachinesInput(...))
  ↓
ResponseFormattingService.format_machine_list(...)
  ↓
Reflex State.machines = [...]
  ↓ (websocket)
Browser re-renders
```

No HTTP loop. No JSON serialisation. Same Python process the whole
way down.

The same Reflex backend ALSO exposes ORB's full FastAPI surface at
`/api/v1/*`, `/health`, `/info`, `/openapi.json`, `/docs` for
external REST clients — but the UI itself does not use it.

### Remote mode call chain

```
Browser → Reflex backend (port 8001)
  → api_http.list_machines()
  → httpx → ORB API at base_url:8000/api/v1/machines/
  → JSON → dict → State
```

## State pattern

Every page has a `*State(rx.State)` class:

- Raw data fields (`machines: list[dict]`)
- Loading + error fields
- `@rx.event` handlers for user actions (load, refresh, filter, cancel...)
- `@rx.var` computed properties for derived UI data

**Important — Reflex Vars cannot run arbitrary Python at template
time.** Anything involving `or`, `if/else`, string slicing, `len()`,
`bool()`, `str()` over reactive Vars must be pre-computed in a
`@rx.var` and consumed as a typed dict/list in the template.

Example pattern (see `pages/requests.py`):

```python
@rx.var
def request_rows(self) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in self.requests:
        rows.append({
            "request_id": r.get("request_id") or "",
            "is_terminal": (r.get("status") or "").lower() in _TERMINAL,
            "progress_pct": min(int(...), 100),
            ...
        })
    return rows

# Template:
rx.foreach(RequestsState.request_rows, _request_row)
```

This adds boilerplate but is the price of having Python state
auto-synced to a React frontend without writing JSX.

## Adding a new page

1. Create `pages/foo.py` with a `*State(rx.State)` class and a
   `foo_page() -> rx.Component` function wrapped with the
   `components.layout.page()` helper.
2. Wire ORB API calls via `from .. import api`. Never import
   `api_inproc` or `api_http` directly.
3. If you need a new ORB endpoint, add wrappers to both `api_inproc.py`
   (direct orchestrator call) and `api_http.py` (httpx call), then
   re-export in `api.py`.
4. Register the page in `app.py` via `app.add_page(foo_page, route="/foo", title="ORB · Foo")`.
5. Add a nav link in `components/layout.py` `NAV_ITEMS`.

## Known limitations / TODOs

- **Var pre-formatting** — every page has 30-50 LOC of computed Vars
  that wouldn't exist in plain Python or JSX. This is a Reflex tax.
- **No SSE wiring** for live request progress — drawers rely on the
  10s page poll. Phase 3 work.
- **No template/machine create form pre-fill from URL params** — the
  React PoC's `?expand=<id>` deep links aren't replicated.
- **No clipboard "copy ID" buttons** — Reflex 0.9 has no
  first-class `navigator.clipboard` binding.
- **Auth** — Reflex websocket bypasses ORB's `AuthMiddleware`. When
  ORB auth is enabled the REST API is protected as expected, but
  the UI websocket is not. Secure separately at the network layer
  (private VPC, mTLS, reverse-proxy auth) until first-class auth
  integration lands.
- **Production prerender** — `rx.drawer` (vaul) requires
  `--no-ssr` flag at export time. SSR-friendly drawer alternative
  not yet investigated.

## Troubleshooting

**`document is not defined` during `reflex export`**: pass
`--no-ssr`. See "Production build" above.

**Frontend on `:3000` but cannot reach API**: confirm
`ui.mode=embedded` (so the same backend hosts both) or that CORS
on remote ORB allows `:3000`.

**`User configuration file not found`** warning at startup: harmless;
ORB looks at a default path first before falling back to the
`--config` flag.

**Reflex won't start because port 8000 is busy**: ORB's uvicorn or
another process is holding it. Kill it: `lsof -ti:8000 | xargs kill -9`.

**Pyright complains about `reflex` imports**: pyright runs against
the system Python by default. Point it at the venv via
`pyrightconfig.json` (`venvPath` + `venv` keys) or run pyright from
inside the activated venv.
