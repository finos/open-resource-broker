# Embedded UI Deployment

## Overview

ORB ships an optional Reflex-based web dashboard, opt-in via:

```bash
pip install 'orb-py[ui]'
```

Three runtime modes are available, controlled by the `ui.mode` configuration key:

| Mode | Config value | When to use |
|---|---|---|
| Embedded | `embedded` (default) | Single-process production deployments |
| Split | `split` | Reverse-proxy / CDN setups, multi-replica API |
| Dev | `dev` | Local UI development only (requires Node/Bun) |

---

## Mode 1 — Embedded (default, production-ready)

A single Reflex backend process serves everything on one port:

- The SPA pages (`/`, `/machines`, `/requests`, etc.)
- WebSocket state sync (`/_event`) — required for Reflex interactivity
- File upload (`/_upload`)
- ORB REST API (`/orb/api/v1/*`) — mounted via Reflex's `api_transformer`
- Health endpoint (`/orb/health`)
- Metrics (`/orb/metrics`)

**Requirements:** The `[ui]` extra only. No Node or Bun needed at runtime.

**How it works:** `orb server start` spawns `reflex run --env prod --backend-only`
in the `orb/ui` package directory with `ORB_MODE=embedded`. The Reflex app
(`orb.ui.app`) has `api_transformer` configured to mount ORB's FastAPI at `/orb`,
so all API routes are served on the same port alongside the SPA and WebSocket layer.
The pre-built frontend bundle (compiled by `make ui-build` and shipped in the wheel)
is served directly by the Reflex backend — no Node/Bun process at runtime.

**Command:**

```bash
orb server start
# or, to keep the process in the foreground:
orb server start --foreground
```

**Verify:**

```bash
# Should return HTML (SPA shell)
curl http://localhost:8000/

# Should return {"status":"ok"}
curl http://localhost:8000/orb/health

# WebSocket — browser connects automatically via the SPA; ws://localhost:8000/_event
```

No more "Cannot connect to server: ws://localhost:8001/_event" errors — everything
is on a single port.

> **Security defaults (changed from earlier releases)**
>
> The server now binds to `127.0.0.1` (loopback) by default instead of `0.0.0.0`.
> CORS `origins` defaults to `["http://localhost:8000"]` and `trusted_hosts` defaults
> to `["localhost", "127.0.0.1"]`.
>
> To expose the server on a network interface you must set all three explicitly in
> your config file:
>
> ```json
> {
>   "server": {
>     "host": "0.0.0.0",
>     "cors": { "origins": ["https://your-domain.example.com"] },
>     "trusted_hosts": ["your-domain.example.com"]
>   }
> }
> ```
>
> Leaving `host` as `0.0.0.0` in the CLI help text is intentional — it remains a
> valid example of how to broaden network exposure.

---

## Mode 2 — Split (production, reverse-proxy)

Two processes are started and managed together by `orb server start`:

- **Process A** — uvicorn with ORB's FastAPI on `server_config.port` (default 8000).
  API-only; no Reflex, no SPA.
- **Process B** — Reflex production backend on `ui_config.backend_port` (default 8001).
  Serves the SPA and WebSocket state. `ORB_MODE=remote` — the API lives in Process A,
  not mounted here.

Both processes are managed together: SIGINT/SIGTERM are forwarded to both groups and
`orb server stop` tears down both cleanly.

Use this mode when you need:
- A CDN edge in front of static assets
- Multiple API replicas behind a load balancer
- Independent scaling of the API and UI tiers

**Config:** Set `ui.mode = "split"` in your configuration file.

**Command:**

```bash
orb server start
```

`orb server stop` stops both processes.

**nginx reverse proxy sample:**

```nginx
# Reflex WebSocket and SPA — proxy to Process B
location /_event {
    proxy_pass http://127.0.0.1:8001;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}

location / {
    proxy_pass http://127.0.0.1:8001;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

# ORB API — proxy to Process A
location /orb/ {
    proxy_pass http://127.0.0.1:8000/orb/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Running Process B externally (advanced):**

If you want to manage the Reflex backend yourself (outside `orb server start`):

```bash
# Use --api-only on the ORB side to start only the API (Process A):
orb server start --api-only

# Start Reflex separately:
cd $(python -c "import orb.ui, pathlib; print(pathlib.Path(orb.ui.__file__).parent)")
ORB_MODE=remote ORB_UI_BACKEND_PORT=8001 reflex run --env prod --backend-only --backend-port 8001
```

---

## Mode 3 — Dev (local iteration, hot reload)

`orb server start` spawns `reflex run` (dev mode) as a child process. The Reflex dev
server provides hot reload so UI changes are reflected immediately without restarting
the backend.

**Requirements:** Node and Bun must be installed on the machine. This mode is not
intended for production — the Reflex dev server carries a heavier runtime footprint
and is not hardened for public traffic.

**Config:** Set `ui.mode = "dev"` in your configuration file.

**Ports (from UIConfig):**

| Setting | Default | Purpose |
|---|---|---|
| `frontend_port` | `3000` | Bun/Vite frontend dev server (browser) |
| `backend_port` | `8001` | Reflex backend + ORB API (`api_transformer`) |

**Command:**

```bash
# With mode=dev in config:
orb server start
```

Directly with Reflex (useful for rapid iteration outside of `orb server`):

```bash
cd src/orb/ui   # directory containing rxconfig.py
ORB_MODE=dev reflex run --loglevel debug
```

---

## Environment Variables

```
ORB_MODE                # Deployment mode seen by orb.ui.app:
                        #   "embedded" — api_transformer active (default)
                        #   "remote"   — no api_transformer (split Process B)
                        #   "dev"      — same as embedded but dev server
ORB_UI_BACKEND_PORT     # Reflex backend port.
                        #   embedded: set to server_config.port (e.g. 8000)
                        #   split/dev: set to ui_config.backend_port (e.g. 8001)
                        #   Default: 8001
ORB_UI_FRONTEND_PORT    # Dev mode only — Bun frontend dev server port (default 3000)
ORB_API_URL             # Full URL of the ORB REST API called by UI state actions
                        #   e.g. http://localhost:8000/orb/api/v1 (embedded)
                        #   e.g. http://localhost:8000/orb/api/v1 (split, points to Process A)
```

---

## Building the Static Bundle from Source

```bash
make ui-build
# Outputs to src/orb/ui/_static/
# Requires Node/Bun (Reflex uses Bun to compile the frontend)
```

This step runs automatically in CI before the wheel is packaged, so PyPI releases
always include the pre-compiled bundle. If you are working from a bare source
checkout you must run `make ui-build` before embedded or split modes will serve
the UI frontend.

---

## Systemd Unit Example (Embedded Mode)

Save the following as `/etc/systemd/system/orb.service`:

```ini
[Unit]
Description=ORB server (embedded UI mode)
After=network.target

[Service]
Type=simple
User=orb
ExecStart=/opt/orb/venv/bin/orb server start --foreground
Restart=on-failure
Environment="ORB_CONFIG_DIR=/etc/orb"

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable orb
sudo systemctl start orb
sudo systemctl status orb
```

Adjust `ExecStart` to match the actual path of the `orb` binary in your installation.

---

## Troubleshooting

**"Cannot connect to server: ws://localhost:8001/_event"**

This error means the browser is connecting to the wrong port. Causes:

- **Embedded mode with stale config:** The SPA was built with `ORB_UI_BACKEND_PORT=8001`
  baked in. Rebuild the bundle: `make ui-build` then restart the server.
- **Split mode:** The Reflex backend (Process B) is not running on port 8001, or
  the reverse proxy is not forwarding `/_event` WebSocket upgrades.
- **Accessing the port directly instead of via the proxy:** In split mode, point your
  browser at the nginx/proxy port, not at Process A or B directly.

**"UI bundle not present at ..."**

The installed wheel does not contain `_static/`. Either install a wheel that was
built with `make ui-build` (all PyPI releases include this), or run
`make ui-build` from your source checkout and then restart the server.

**Frontend loads but API calls 404**

In split mode, check that:
- nginx is forwarding `/orb/*` requests to Process A (uvicorn on port 8000).
- `ORB_API_URL` in the UI build points to the correct API base URL. The value
  is baked in at bundle-build time, so if the URL changes you must rebuild with
  `make ui-build`.

**Reflex `RuntimeError: There should not be an __init__.py file in your app root`**

Reflex treats the app root directory as a namespace and forbids the presence of
`__init__.py` in that directory. Ensure `src/orb/ui/__init__.py` does not exist.

---

## Docker Compose

Two production-ready Compose files are provided under `deployment/docker/`.

### Embedded mode

Runs a single container that serves the SPA, WebSocket layer, and REST API on
port 8000:

```bash
docker compose -f deployment/docker/docker-compose.embedded.yml up -d
```

The container bind-mounts `config/config.json` (read-only) and uses a named
volume `orb-work` for runtime state (PID file, loopback token, logs).

**When to use:** Standard single-host production deployments, self-contained
staging environments, or anywhere you want the simplest possible operational
footprint. Zero extra services required.

**Non-default port:** If you need to run on a port other than 8000, you must
rebuild the SPA bundle first so the WebSocket URL baked into the JS matches:

```bash
ORB_UI_BACKEND_PORT=9000 make ui-build
docker build -t orb-api:custom .
ORB_PORT=9000 docker compose -f deployment/docker/docker-compose.embedded.yml up -d
```

### Split mode

Runs two containers — `orb-api` (REST API, port 8000) and `orb-ui` (Reflex
SPA + WebSocket, port 8001) — on a shared Docker network:

```bash
docker compose -f deployment/docker/docker-compose.split.yml up -d
```

A reverse proxy (nginx or traefik) must sit in front to unify the two services
on a single public port. An nginx template is included as a commented `proxy`
service block inside the Compose file — uncomment and adjust the TLS paths to
enable it.

**When to use:** Deployments that need independent scaling of the API and UI
tiers, CDN edge caching of static assets, or multiple API replicas behind a
load balancer. Prefer embedded mode for simpler setups.

**Key environment variables for split mode:**

| Variable | Default | Purpose |
|---|---|---|
| `ORB_API_PORT` | `8000` | Port for the `orb-api` container |
| `ORB_UI_PORT` | `8001` | Port for the `orb-ui` container |
| `ORB_BASE_URL` | `http://orb-api:8000` | URL the UI's httpx client uses to reach the API |
