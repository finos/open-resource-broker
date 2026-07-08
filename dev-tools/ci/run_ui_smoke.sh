#!/bin/bash
# Boot the embedded UI, curl each SPA route + the ORB REST + Reflex health
# endpoints, then shut down. Mirrors what customers get from
# ``orb server start`` on a fresh install.
#
# Steps:
#   1. Install ``.[ui]`` (with bun on PATH -- the runner installs it in the
#      workflow before this script is called).
#   2. Build the SPA bundle so ``_static/`` is populated.
#   3. Launch ``orb server start`` (embedded mode, port 8000).
#   4. Poll ``/_health`` until Reflex is up (max ~60s -- the first-run
#      compile step can be slow).
#   5. GET each SPA route, an ``/orb`` REST endpoint, and a hashed asset;
#      require 200 on all of them.
#   6. ``orb server stop`` no matter what.
set -eo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

PORT="${ORB_SMOKE_PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
MAX_WAIT="${ORB_SMOKE_MAX_WAIT:-60}"

echo "INFO: Installing [ui] extras..."
uv pip install '.[ui]'

# Resolve how to invoke ``orb`` / ``python`` without hard-coding a venv layout.
# Mirrors the pattern used in dev-tools/docs/ci_docs_build.sh and
# dev-tools/package/build.sh: prefer ``uv run`` when uv is on PATH, fall back
# to the repo-local .venv, and finally to whatever is on the caller's PATH.
if command -v uv >/dev/null 2>&1; then
    ORB=(uv run orb)
    PY=(uv run python)
elif [ -x "$PROJECT_ROOT/.venv/bin/orb" ]; then
    ORB=("$PROJECT_ROOT/.venv/bin/orb")
    PY=("$PROJECT_ROOT/.venv/bin/python")
elif command -v orb >/dev/null 2>&1; then
    ORB=(orb)
    PY=(python3)
else
    echo "ERROR: orb not found. Run 'make dev-install' or install '.[ui]' extras." >&2
    exit 1
fi

echo "INFO: Building SPA bundle..."
./dev-tools/package/build_ui.sh --quiet

# Point orb at the repo's tracked default config so the startup validator
# finds a valid config.json without requiring `orb init` to have been run.
# In a fresh CI checkout config/config.json is gitignored (user-specific),
# but config/default_config.json is committed and contains a valid AppConfig.
# ORB_CONFIG_FILE is the highest-priority candidate in StartupValidator._find_config_file(),
# so this overrides all other discovery paths without mutating the checkout.
export ORB_CONFIG_FILE="${PROJECT_ROOT}/config/default_config.json"

cleanup() {
    "${ORB[@]}" server stop 2>/dev/null || true
}
trap cleanup EXIT

echo "INFO: Starting orb server (embedded mode, port ${PORT})..."
"${ORB[@]}" server stop 2>/dev/null || true
"${ORB[@]}" server start

echo "INFO: Waiting for backend on :${PORT} (max ${MAX_WAIT}s)..."
for i in $(seq 1 "$MAX_WAIT"); do
    if curl -sf "${BASE}/_health" > /dev/null 2>&1; then
        echo "INFO: Backend up after ${i}s."
        break
    fi
    if [ "$i" -eq "$MAX_WAIT" ]; then
        echo "ERROR: backend did not start within ${MAX_WAIT}s" >&2
        exit 1
    fi
    sleep 1
done

FAILED=0
probe() {
    local path="$1"
    local expected="${2:-200}"
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}${path}" 2>/dev/null || echo "000")
    if [ "$status" = "$expected" ]; then
        printf "  OK   %-40s %s\n" "$path" "$status"
    else
        printf "  FAIL %-40s got %s (expected %s)\n" "$path" "$status" "$expected"
        FAILED=1
    fi
}

echo "INFO: Curling SPA routes..."
probe "/"
probe "/machines"
probe "/requests"
probe "/templates"
probe "/config"

echo "INFO: Curling Reflex + ORB endpoints..."
probe "/_health"
probe "/orb/health"

echo "INFO: Curling a hashed asset..."
ASSET=$("${PY[@]}" -c "import glob; paths = glob.glob('src/orb/ui/_static/assets/*.js'); print(paths[0].split('/_static/')[1] if paths else '')")
if [ -n "$ASSET" ]; then
    probe "/${ASSET}"
else
    echo "  WARN no assets found under src/orb/ui/_static/assets/"
    FAILED=1
fi

echo "INFO: Checking machines API returns 200 with pagination fields..."
MACHINES_BODY=$(curl -sf "${BASE}/orb/api/v1/machines/" 2>/dev/null || echo "")
MACHINES_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/orb/api/v1/machines/" 2>/dev/null || echo "000")
if [ "$MACHINES_STATUS" = "200" ]; then
    printf "  OK   %-40s %s\n" "/orb/api/v1/machines/" "$MACHINES_STATUS"
    # Verify response body contains pagination fields
    if echo "$MACHINES_BODY" | "${PY[@]}" -c "import sys, json; d=json.load(sys.stdin); assert 'total_count' in d and 'next_cursor' in d" 2>/dev/null; then
        echo "  OK   machines response contains total_count and next_cursor"
    else
        echo "  FAIL machines response missing total_count or next_cursor"
        FAILED=1
    fi
else
    printf "  FAIL %-40s got %s (expected 200)\n" "/orb/api/v1/machines/" "$MACHINES_STATUS"
    FAILED=1
fi

echo "INFO: Checking config endpoint returns 403 without auth token..."
CONFIG_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${BASE}/orb/api/v1/config/" 2>/dev/null || echo "000")
if [ "$CONFIG_STATUS" = "403" ]; then
    printf "  OK   %-40s %s (auth gate active)\n" "/orb/api/v1/config/" "$CONFIG_STATUS"
else
    printf "  WARN %-40s got %s (expected 403 — auth gate may not be active)\n" "/orb/api/v1/config/" "$CONFIG_STATUS"
    # Not a hard failure — auth may be disabled in test environments
fi

echo "INFO: Checking compiled JS bundle for stale port reference (:8001)..."
JS_BUNDLE=$("${PY[@]}" -c "
import glob, os
pattern = 'src/orb/ui/_static/assets/reflex-env-*.js'
paths = glob.glob(pattern)
print(paths[0] if paths else '')
" 2>/dev/null || echo "")
if [ -n "$JS_BUNDLE" ] && [ -f "$JS_BUNDLE" ]; then
    if grep -q ':8001' "$JS_BUNDLE" 2>/dev/null; then
        echo "  FAIL $JS_BUNDLE contains stale port :8001"
        FAILED=1
    else
        echo "  OK   $JS_BUNDLE does not contain stale port :8001"
    fi
else
    echo "  WARN no reflex-env-*.js bundle found under src/orb/ui/_static/assets/ — skipping port check"
fi

if [ "$FAILED" -ne 0 ]; then
    echo "ERROR: one or more probes failed" >&2
    exit 1
fi

echo "SUCCESS: UI smoke complete — all routes returned 200."
