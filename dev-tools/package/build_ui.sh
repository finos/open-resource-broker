#!/bin/bash
# Build the Reflex UI static bundle into src/orb/ui/_static/.
#
# Produces the compiled SPA (index.html + hashed JS/CSS chunks) that ships
# inside the orb wheel. The wheel-installed runtime serves this bundle
# from ``<site-packages>/orb/ui/_static/`` — no Node/Bun needed at runtime,
# only at build time.
#
# Steps:
#   1. Wipe any prior _static/ and .web/build/ so stale hashed chunks do
#      not leak into the wheel.
#   2. ``reflex export --frontend-only`` emits a React Router 7 project
#      into ``.web/``.
#   3. ``bun install && bun run export`` inside ``.web/`` compiles the
#      SPA into ``.web/build/client/``.
#   4. Copy ``.web/build/client/`` to ``src/orb/ui/_static/`` so
#      ``[tool.setuptools.package-data]`` picks it up.
#
# Usage: dev-tools/package/build_ui.sh [--quiet]
set -e

QUIET=false
for arg in "$@"; do
    case $arg in
        --quiet|-q)
            QUIET=true
            ;;
    esac
done

log() {
    if [ "$QUIET" = false ]; then
        echo "$@"
    fi
}

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

UI_DIR="src/orb/ui"
STATIC_DIR="$UI_DIR/_static"
WEB_DIR="$UI_DIR/.web"
CLIENT_DIR="$WEB_DIR/build/client"

# Resolve bun: PATH first, then the standard installer location.
resolve_bun() {
    BUN="${BUN:-$(command -v bun 2>/dev/null || true)}"
    if [ -z "$BUN" ] && [ -x "$HOME/.bun/bin/bun" ]; then
        BUN="$HOME/.bun/bin/bun"
    fi
}

resolve_bun
if [ -z "$BUN" ] || [ ! -x "$BUN" ]; then
    log "INFO: bun not found; installing to \$HOME/.bun/bin/..."
    curl -fsSL https://bun.sh/install | bash >&2
    resolve_bun
fi
if [ -z "$BUN" ] || [ ! -x "$BUN" ]; then
    echo "ERROR: bun install failed. Install manually: curl -fsSL https://bun.sh/install | bash" >&2
    exit 1
fi

log "INFO: Building UI static bundle..."
log "INFO: Ensuring [ui] extras are installed..."
uv pip install --quiet '.[ui]'

# Resolve how to invoke ``reflex`` without hard-coding a venv layout.  The rest
# of the repo (see dev-tools/docs/ci_docs_build.sh, dev-tools/package/build.sh,
# makefiles/*.mk) prefers ``uv run`` when uv is on PATH and falls back to the
# repo-local ``.venv/bin/`` entry points otherwise.  That covers all three
# supported environments: developer editable install, CI's uv-cached .venv,
# and a fresh clone driven by ``make dev-install``.
if command -v uv >/dev/null 2>&1; then
    REFLEX=(uv run reflex)
elif [ -x "$PROJECT_ROOT/.venv/bin/reflex" ]; then
    REFLEX=("$PROJECT_ROOT/.venv/bin/reflex")
elif command -v reflex >/dev/null 2>&1; then
    REFLEX=(reflex)
else
    echo "ERROR: reflex not found. Run 'make dev-install' or install '.[ui]' extras." >&2
    exit 1
fi

log "INFO: Cleaning stale bundle outputs..."
rm -rf "$STATIC_DIR" "$WEB_DIR/build"

log "INFO: Running reflex export --frontend-only..."
(
    cd "$UI_DIR"
    if [ "$QUIET" = true ]; then
        "${REFLEX[@]}" export --frontend-only --no-zip --no-ssr --loglevel warning >/dev/null
    else
        "${REFLEX[@]}" export --frontend-only --no-zip --no-ssr --loglevel info
    fi
)

log "INFO: Running bun install + bun run export..."
(
    cd "$WEB_DIR"
    if [ "$QUIET" = true ]; then
        "$BUN" install --frozen-lockfile >/dev/null 2>&1
        "$BUN" run export >/dev/null 2>&1
    else
        "$BUN" install --frozen-lockfile
        "$BUN" run export
    fi
)

if [ ! -d "$CLIENT_DIR" ]; then
    echo "ERROR: expected $CLIENT_DIR after bun run export" >&2
    exit 1
fi

log "INFO: Copying bundle to $STATIC_DIR..."
cp -r "$CLIENT_DIR" "$STATIC_DIR"

if [ ! -f "$STATIC_DIR/index.html" ]; then
    echo "ERROR: $STATIC_DIR/index.html missing after copy" >&2
    exit 1
fi

# Verify the SPA bundle baked the expected backend port. If someone runs
# ORB_UI_BACKEND_PORT=<other> make ui-build the resulting bundle only
# works on that port; catching this at build-time is easier than
# debugging a broken deployment.
EXPECTED_PORT="${ORB_UI_BACKEND_PORT:-8000}"
BUNDLE_ENV=$(find src/orb/ui/_static/assets -name "reflex-env-*.js" 2>/dev/null | head -1)
if [ -n "$BUNDLE_ENV" ] && ! grep -q "localhost:${EXPECTED_PORT}" "$BUNDLE_ENV"; then
    echo "ERROR: SPA bundle does not reference localhost:${EXPECTED_PORT}" >&2
    echo "       (Rxconfig may have baked a wrong api_url; check ORB_UI_BACKEND_PORT.)" >&2
    exit 1
fi

log "SUCCESS: Static bundle written to $STATIC_DIR/"
if [ "$QUIET" = false ]; then
    du -sh "$STATIC_DIR" 2>/dev/null || true
fi
