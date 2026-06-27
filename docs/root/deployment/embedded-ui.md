# Embedded UI Deployment

The Open Resource Broker ships an optional Reflex-based web UI that can run
alongside the API server.  This page explains the two operational modes and the
recommended production path.

## Modes at a glance

| Mode | Command | Use case |
|---|---|---|
| Development | `reflex run` | Local development; hot-reload; **not for production** |
| Production (static export + nginx/CDN) | `reflex export --frontend-only` + separate ORB backend | Recommended production path |

---

## Development mode (`reflex run`)

```bash
# Install with the ui extra
pip install "orb-py[ui]"

# Start both the Reflex frontend dev server and the ORB backend in one process
reflex run
```

`reflex run` starts a webpack-based hot-reload server on the frontend port
(default `3000`) and the Reflex/ORB backend on the backend port (default
`8001`).  It rebuilds on every file save.

> **Do not use `reflex run` in production.**  It runs an unoptimised dev
> server with no output caching, and exposes the webpack port directly to
> clients.

---

## Production mode

### Step 1 — Export static frontend assets

```bash
# Produces a static/ directory with compiled JS/CSS/HTML
reflex export --frontend-only
```

The output is a standard set of static web files that can be served by any
HTTP server or CDN.

### Step 2 — Start the ORB backend

Run the ORB API server independently:

```bash
export ORB_MODE=embedded          # tells ORB it is the backend for the UI
export ORB_UI_BACKEND_PORT=8001   # Reflex backend port (matches rxconfig.py)
orb server start --foreground
```

The backend listens on `ORB_UI_BACKEND_PORT` (default `8001`).  The frontend
files are entirely static and do not require a running Reflex process in
production.

### Step 3 — Serve static assets and reverse-proxy the API

#### nginx example

```nginx
upstream orb_backend {
    server 127.0.0.1:8001;
    keepalive 16;
}

server {
    listen 443 ssl;
    server_name dashboard.your-domain.com;

    # --- Static UI assets (compiled by `reflex export --frontend-only`) ------
    root /opt/orb/static;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
        expires 1h;
        add_header Cache-Control "public, max-age=3600";
    }

    # Immutable JS/CSS bundles produced by the build step
    location /_next/static/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # --- ORB REST API ---------------------------------------------------------
    location /api/ {
        proxy_pass http://orb_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # --- Server-Sent Events ---------------------------------------------------
    location /api/v1/events/ {
        proxy_pass http://orb_backend;
        proxy_buffering off;
        proxy_cache off;
        proxy_set_header Connection '';
        proxy_http_version 1.1;
        chunked_transfer_encoding off;
        proxy_read_timeout 3600s;
    }
}
```

#### CDN / object-storage alternative

If you prefer a CDN (CloudFront, Fastly, etc.):

1. Upload the `static/` directory to an S3 bucket or equivalent.
2. Configure the CDN distribution to serve the bucket for `/*` and to
   forward `/api/*` requests to the ORB backend origin.
3. Enable CORS on the CDN distribution if the API origin differs from the
   static origin.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `ORB_MODE` | *(unset)* | Set to `embedded` when running ORB as the backend for the UI |
| `ORB_UI_BACKEND_PORT` | `8001` | Port the Reflex/ORB backend listens on |
| `ORB_UI_FRONTEND_PORT` | `3000` | Port the Reflex frontend dev server uses (development only) |

See [Environment variables](../configuration/environment-variables.md) for all
`ORB_*` variables, and [Configuration](../user_guide/configuration.md) for the
corresponding config-file keys.

---

## Summary

| Step | Tool | Output |
|---|---|---|
| 1. Build frontend | `reflex export --frontend-only` | `static/` directory |
| 2. Run backend | `orb server start --foreground` | ORB API on `ORB_UI_BACKEND_PORT` |
| 3. Serve traffic | nginx / CDN | HTTPS + proxied API |

The Reflex process is **not required at runtime** in production — only the
compiled static assets and the ORB backend process are needed.
