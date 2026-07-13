#!/bin/bash
set -euo pipefail

# smoke_check_pages.sh — post-deploy smoke check for GitHub Pages docs site.
#
# Usage: smoke_check_pages.sh <base_url>
#   base_url: the root URL of the deployed Pages site, e.g.
#             https://finos.github.io/open-resource-broker/
#
# Checks performed:
#   1. Root URL (/) returns HTTP 200 and contains real HTML content.
#   2. /versions.json returns HTTP 200 and is valid JSON with at least one version.
#   3. A versioned index page (/dev/ or /latest/) returns HTTP 200.
#   4. A known nav page under /latest/ returns HTTP 200.
#
# Exit code 0 = all checks passed.
# Exit code 1 = one or more checks failed (details printed to stderr).

BASE_URL="${1:-}"
if [ -z "$BASE_URL" ]; then
    echo "ERROR: base_url argument is required." >&2
    echo "Usage: smoke_check_pages.sh <base_url>" >&2
    exit 1
fi

# Normalise: strip trailing slash for consistent path joining.
BASE_URL="${BASE_URL%/}"

PASS=0
FAIL=0

check_url() {
    local label="$1"
    local url="$2"
    local grep_pattern="${3:-}"

    echo "==> Checking: ${label} (${url})"

    http_code=$(curl --silent --show-error --write-out "%{http_code}" \
        --max-time 30 --retry 3 --retry-delay 5 \
        --location --output /tmp/smoke_check_body.html \
        "${url}" 2>/tmp/smoke_check_curl_err.txt || true)

    if [ "${http_code}" != "200" ]; then
        echo "  FAIL: expected HTTP 200, got ${http_code}" >&2
        cat /tmp/smoke_check_curl_err.txt >&2 || true
        FAIL=$((FAIL + 1))
        return
    fi

    if [ -n "${grep_pattern}" ]; then
        if ! grep -q "${grep_pattern}" /tmp/smoke_check_body.html 2>/dev/null; then
            echo "  FAIL: pattern '${grep_pattern}' not found in response body." >&2
            FAIL=$((FAIL + 1))
            return
        fi
    fi

    # Soft-404 guard: real pages should not contain common 404 page markers.
    if grep -qi "page not found\|404 - not found\|isn't available\|File not found" \
            /tmp/smoke_check_body.html 2>/dev/null; then
        echo "  FAIL: response body looks like a soft-404 page." >&2
        FAIL=$((FAIL + 1))
        return
    fi

    echo "  PASS: HTTP 200 OK"
    PASS=$((PASS + 1))
}

check_versions_json() {
    local url="${BASE_URL}/versions.json"
    echo "==> Checking: versions.json (${url})"

    http_code=$(curl --silent --show-error --write-out "%{http_code}" \
        --max-time 30 --retry 3 --retry-delay 5 \
        --location --output /tmp/smoke_check_versions.json \
        "${url}" 2>/tmp/smoke_check_curl_err.txt || true)

    if [ "${http_code}" != "200" ]; then
        echo "  FAIL: versions.json returned HTTP ${http_code} (expected 200)." >&2
        FAIL=$((FAIL + 1))
        return
    fi

    # Validate JSON and require at least one version entry.
    if ! python3 -c "
import json, sys
data = json.load(open('/tmp/smoke_check_versions.json'))
if not isinstance(data, list) or len(data) == 0:
    print('  FAIL: versions.json is empty or not an array', file=sys.stderr)
    sys.exit(1)
print(f'  versions.json contains {len(data)} version(s): {[v.get(\"version\",\"?\") for v in data]}')
" 2>/tmp/smoke_check_versions_err.txt; then
        echo "  FAIL: versions.json is not valid JSON or contains no versions." >&2
        cat /tmp/smoke_check_versions_err.txt >&2 || true
        FAIL=$((FAIL + 1))
        return
    fi

    echo "  PASS: versions.json is valid JSON with at least one version."
    PASS=$((PASS + 1))
}

echo "==> Starting smoke check for: ${BASE_URL}"
echo ""

# Check 1: Root returns 200 and looks like a real HTML page (not a 404).
check_url "Root redirect" "${BASE_URL}/" "<html"

# Check 2: versions.json exists and is valid.
check_versions_json

# Check 3: versioned index (try 'latest' first, fall back to 'dev').
# We attempt both aliases; a passing result on either satisfies the check.
echo "==> Checking: versioned alias index (/latest/ or /dev/)"
latest_code=$(curl --silent --write-out "%{http_code}" --max-time 15 \
    --location --output /dev/null "${BASE_URL}/latest/" 2>/dev/null || echo "000")
dev_code=$(curl --silent --write-out "%{http_code}" --max-time 15 \
    --location --output /dev/null "${BASE_URL}/dev/" 2>/dev/null || echo "000")

if [ "${latest_code}" = "200" ] || [ "${dev_code}" = "200" ]; then
    echo "  PASS: versioned alias reachable (latest=${latest_code}, dev=${dev_code})"
    PASS=$((PASS + 1))
else
    echo "  FAIL: neither /latest/ (${latest_code}) nor /dev/ (${dev_code}) returned 200." >&2
    FAIL=$((FAIL + 1))
fi

# Check 4: A known nav page under /latest/; fall back to /dev/ if latest not deployed.
if [ "${latest_code}" = "200" ]; then
    alias_prefix="latest"
else
    alias_prefix="dev"
fi
check_url "Nav page (${alias_prefix}/getting_started/quick_start/)" \
    "${BASE_URL}/${alias_prefix}/getting_started/quick_start/" "<html"

echo ""
echo "==> Smoke check results: ${PASS} passed, ${FAIL} failed."

if [ "${FAIL}" -gt 0 ]; then
    echo "FAIL: smoke check failed — ${FAIL} check(s) did not pass." >&2
    exit 1
fi

echo "PASS: all smoke checks passed."
