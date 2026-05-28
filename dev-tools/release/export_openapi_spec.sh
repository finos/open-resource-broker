#!/bin/bash
# Export the OpenAPI spec from a running ORB server into sdk/go/openapi.json.
# Uses an existing config/config.json if present; otherwise bootstraps a
# throwaway config via `orb init --non-interactive` to a temp directory.
set -euo pipefail

# Re-exec under `uv run` so `orb` and `python3` resolve from the project venv,
# regardless of whether the caller already activated it.
if [[ -z "${ORB_EXPORT_SPEC_REEXEC:-}" ]] && command -v uv >/dev/null 2>&1; then
    export ORB_EXPORT_SPEC_REEXEC=1
    exec uv run -- bash "$0" "$@"
fi

SOCK=/tmp/orb-spec.sock
SPEC_TMP=$(mktemp -d)

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    rm -rf "$SPEC_TMP"
    rm -f "$SOCK"
}
trap cleanup EXIT

if [[ -f config/config.json ]]; then
    CONFIG_FLAG=(--config config/config.json)
else
    orb init --non-interactive --config-dir "$SPEC_TMP/config" >/dev/null
    CONFIG_FLAG=(--config "$SPEC_TMP/config/config.json")
fi

orb "${CONFIG_FLAG[@]}" system serve --socket-path "$SOCK" &
SERVER_PID=$!

for _ in $(seq 1 30); do
    if curl -sf --unix-socket "$SOCK" http://localhost/health \
        | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status') in ('healthy','degraded') else 1)" 2>/dev/null; then
        break
    fi
    sleep 1
done

curl --fail --unix-socket "$SOCK" http://localhost/openapi.json > sdk/go/openapi.json
python3 -c "import json; d=json.load(open('sdk/go/openapi.json')); assert d.get('openapi'), 'Invalid OpenAPI spec'"
