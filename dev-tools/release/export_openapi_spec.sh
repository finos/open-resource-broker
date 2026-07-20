#!/bin/bash
# Export the OpenAPI spec from a running ORB server into sdk/spec/openapi.json.
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

orb "${CONFIG_FLAG[@]}" server start --foreground --api-only --socket-path "$SOCK" &
SERVER_PID=$!

for _ in $(seq 1 30); do
    # Poll the OpenAPI endpoint directly: it becomes available as soon as uvicorn
    # is serving, regardless of provider health (AWS creds, etc.).  The old loop
    # polled /health whose status depends on backend provider checks that always
    # fail in CI (no credentials), so the loop never broke early.
    if curl -sf --unix-socket "$SOCK" http://localhost/openapi.json >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Explicit readiness gate: if the server never became ready the loop above
# exited without breaking, and the curl below would hang or error with a
# confusing message.  Fail loudly here instead so CI shows the real cause.
curl -sf --unix-socket "$SOCK" http://localhost/openapi.json >/dev/null 2>&1 \
    || { echo "ERROR: ORB server never became ready after 30s; aborting spec export" >&2; exit 1; }

mkdir -p sdk/spec
# Fetch to a temp file, then re-serialise with a canonical, stable formatting
# (indent=2, trailing newline).  FastAPI/uvicorn serve the spec as a single
# compact line; committing that makes every re-export a giant one-line diff and
# defeats the spec-drift guard (which relies on `git diff --exit-code`).  A
# deterministic pretty-print keeps the committed spec reviewable AND lets the
# drift guard show only real route/schema changes, not formatting churn.
RAW_SPEC=$(mktemp)
curl --fail --unix-socket "$SOCK" http://localhost/openapi.json > "$RAW_SPEC"
python3 - "$RAW_SPEC" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as fh:
    doc = json.load(fh)
assert doc.get("openapi"), "Invalid OpenAPI spec"
with open("sdk/spec/openapi.json", "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, ensure_ascii=True)
    fh.write("\n")
PY
rm -f "$RAW_SPEC"
