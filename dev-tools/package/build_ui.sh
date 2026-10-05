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
#   2. ``reflex export --frontend-only`` emits a React Router 7 project into
#      ``.web/`` and, as part of that one command, runs its own `bun install`
#      and `bun run export` (the vite production build) to compile the SPA
#      into ``.web/build/client/`` -- including the gzip sidecars and the
#      SPA-fallback 404.html it generates on top of the raw vite output.
#   3. Copy ``.web/build/client/`` to ``src/orb/ui/_static/`` so
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

# Phase timing: written to stderr so these lines show up in CI logs even when
# --quiet suppresses the rest of this script's output. Phases run
# sequentially, so one pair of variables is enough to track whichever phase
# is currently open.
_phase_t0=0
_phase_name=""
_phase_start() {
    _phase_name="$1"
    _phase_t0=$(date +%s)
    printf '[timing] %s: start\n' "$_phase_name" >&2
}
_phase_end() {
    printf '[timing] %s: done in %ss\n' "$_phase_name" "$(( $(date +%s) - _phase_t0 ))" >&2
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

# Pin reflex to this resolved bun instead of letting it silently provision
# its own (newer) copy and write a lockfile this bun can't read back.
export REFLEX_USE_SYSTEM_BUN=1
_bun_dir="$(dirname "$BUN")"
export PATH="$_bun_dir:$PATH"

log "INFO: Building UI static bundle..."

# ---------------------------------------------------------------------------
# Venv / reflex setup
#
# Strategy: prefer an already-active venv or the project's .venv so that
# incremental dev builds are fast.  If neither exists, create an ephemeral
# venv, install .[ui] into it, run the build, and delete it afterward.
#
# The [ui] extra (reflex→click>=8.2) conflicts with the dev/ci groups
# (semgrep→click<8.2), so we NEVER sync from the full lockfile — we always
# install .[ui] directly into whichever venv we end up using.
# ---------------------------------------------------------------------------

_phase_start "SPA: venv/ui install"

EPHEMERAL_VENV=""

# Ephemeral venvs live under one deterministic parent so orphans left by an
# uncatchable kill (SIGKILL / OOM / CI timeout, which no trap can intercept)
# can be reaped on the next run rather than accumulating on a long-lived host.
_EPHEMERAL_BASE="${TMPDIR:-/tmp}/orb-ui-build-venvs"

# Ensure this run's ephemeral venv is removed even if the build fails mid-way.
_cleanup_ephemeral() {
    if [ -n "$EPHEMERAL_VENV" ]; then
        rm -rf "$(dirname "$EPHEMERAL_VENV")" 2>/dev/null || true
    fi
}
# EXIT covers normal/`set -e`/SIGINT/SIGTERM exits; INT/TERM added explicitly so
# cleanup runs before the shell re-raises them.  SIGKILL is uncatchable, so a
# hard kill can still orphan this run's venv — grouping all ephemeral venvs
# under one predictable base ($_EPHEMERAL_BASE) means such orphans cluster in a
# single dir for the OS/CI tmp reaper (or a manual `rm -rf`) to clean, instead
# of scattering across random mktemp paths.  No auto-sweep here: wiping the base
# at startup would race a concurrent build sharing the same host.
trap _cleanup_ephemeral EXIT INT TERM

# Detect whether we are already inside a usable venv (VIRTUAL_ENV is set by
# `source activate`; UV_PROJECT_ENVIRONMENT is set by uv itself).
_active_venv="${VIRTUAL_ENV:-${UV_PROJECT_ENVIRONMENT:-}}"

if [ -n "$_active_venv" ] && [ -x "$_active_venv/bin/python" ]; then
    log "INFO: Using active virtual environment: $_active_venv"
    _VENV_BIN="$_active_venv/bin"
elif [ -x "$PROJECT_ROOT/.venv/bin/python" ]; then
    log "INFO: Using project .venv: $PROJECT_ROOT/.venv"
    _VENV_BIN="$PROJECT_ROOT/.venv/bin"
else
    # No usable venv — create an ephemeral one under the deterministic base.
    log "INFO: No virtual environment found; creating an ephemeral one..."
    mkdir -p "$_EPHEMERAL_BASE"
    EPHEMERAL_VENV="$(mktemp -d "$_EPHEMERAL_BASE/XXXXXX")/orb-ui-build-venv"
    if command -v uv >/dev/null 2>&1; then
        uv venv --quiet "$EPHEMERAL_VENV"
    else
        python3 -m venv "$EPHEMERAL_VENV"
    fi
    _VENV_BIN="$EPHEMERAL_VENV/bin"
    log "INFO: Ephemeral venv created at $EPHEMERAL_VENV"
fi

# Install .[ui] into the chosen venv.  Use uv pip when available (faster);
# fall back to the venv's own pip.
log "INFO: Ensuring [ui] extras are installed..."
# Installing '.[ui]' builds the orb-py wheel, which runs setup.py's build_py
# hook — which calls THIS script.  Set ORB_SKIP_UI_BUILD=1 for the nested
# build so it does not recurse (we are already inside the UI build).
if command -v uv >/dev/null 2>&1; then
    ORB_SKIP_UI_BUILD=1 VIRTUAL_ENV="$(dirname "$_VENV_BIN")" uv pip install --quiet '.[ui]'
else
    ORB_SKIP_UI_BUILD=1 "$_VENV_BIN/pip" install --quiet "$(pwd)[ui]"
fi
_phase_end

# Invoke reflex from the venv we installed .[ui] into.  Call the venv binary
# directly rather than ``uv run``: ``uv run`` resolves reflex against uv's
# *project* environment, which is NOT $_VENV_BIN when we created an ephemeral
# venv (or when an unrelated venv is active) — so it would miss the reflex we
# just installed.  The .[ui] install above guarantees $_VENV_BIN/reflex exists.
if [ -x "$_VENV_BIN/reflex" ]; then
    REFLEX=("$_VENV_BIN/reflex")
elif command -v reflex >/dev/null 2>&1; then
    REFLEX=(reflex)
else
    echo "ERROR: reflex not found in $_VENV_BIN. Check the '.[ui]' install above." >&2
    exit 1
fi

# If .web/ was generated by a different Reflex version than the one now
# installed, a stale node_modules/build graph in there can silently produce
# a bundle for the wrong Reflex release (reflex's own version-check guards
# `init`/`run`, not `export --frontend-only`). Compare the version recorded
# in .web/reflex.json against the installed one and wipe the whole
# directory, not just build/, when they differ.
REFLEX_JSON="$WEB_DIR/reflex.json"
if [ -f "$REFLEX_JSON" ]; then
    _web_reflex_version=$("$_VENV_BIN/python" -c '
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("version", ""))
except Exception:
    pass
' "$REFLEX_JSON" 2>/dev/null || true)
    _installed_reflex_version=$("$_VENV_BIN/python" -c '
import importlib.metadata as m
try:
    print(m.version("reflex"))
except Exception:
    pass
' 2>/dev/null || true)
    if [ -n "$_web_reflex_version" ] && [ -n "$_installed_reflex_version" ] \
        && [ "$_web_reflex_version" != "$_installed_reflex_version" ]; then
        log "INFO: .web/ was built with reflex $_web_reflex_version but $_installed_reflex_version is installed; removing .web/ for a clean rebuild."
        rm -rf "$WEB_DIR"
    fi
fi

log "INFO: Cleaning stale bundle outputs..."
rm -rf "$STATIC_DIR" "$WEB_DIR/build"

# `reflex export --frontend-only` already runs its own `bun install` and
# `bun run export` (= the vite production build) internally, then adds the
# gzip sidecars and the SPA-fallback 404.html on top. Re-running bun here
# ourselves was redundant work that also clobbered that gzip/404.html
# post-processing (vite's emptyOutDir wipes them on a second build), so the
# wheel never shipped them. Use reflex's own export output as-is instead.
log "INFO: Running reflex export --frontend-only..."
_phase_start "reflex export (bun install + build)"
(
    cd "$UI_DIR"
    if [ "$QUIET" = true ]; then
        "${REFLEX[@]}" export --frontend-only --no-zip --no-ssr --loglevel warning >/dev/null
    else
        "${REFLEX[@]}" export --frontend-only --no-zip --no-ssr --loglevel info
    fi
)
_phase_end

if [ ! -d "$CLIENT_DIR" ]; then
    echo "ERROR: expected $CLIENT_DIR after reflex export" >&2
    exit 1
fi

log "INFO: Copying bundle to $STATIC_DIR..."
_phase_start "copy to _static"
cp -r "$CLIENT_DIR" "$STATIC_DIR"
_phase_end

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
# The EXIT trap (_cleanup_ephemeral) removes the ephemeral venv if one was created.
