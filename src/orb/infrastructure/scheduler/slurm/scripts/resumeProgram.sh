#!/bin/bash
# SLURM ResumeProgram hook for ORB — powers up cloud nodes via ORB provisioning.
# Configure in slurm.conf: ResumeProgram=/path/to/resumeProgram.sh
# SLURM invokes this with node list as $1 (e.g. "compute-[001-003]" or "node1 node2")
set -euo pipefail

LOG_DIR="${SLURM_ORB_LOG_DIR:-/var/log/orb}"
LOG_FILE="${LOG_DIR}/resume_program.log"
ORB_MODE="${SLURM_ORB_MODE:-cli}"  # "cli" or "api"
ORB_API_URL="${SLURM_ORB_API_URL:-http://localhost:8000}"

mkdir -p "${LOG_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [resumeProgram] $*" >> "${LOG_FILE}"
}

# --- Input validation ---
if [ -z "${1:-}" ]; then
    log "ERROR: No node names provided"
    exit 1
fi

NODE_LIST="$1"

# Validate node names: alphanumeric, hyphens, brackets, commas, spaces only
if ! echo "${NODE_LIST}" | grep -qE '^[a-zA-Z0-9\-\[\],\s ]+$'; then
    log "ERROR: Invalid node name characters in: ${NODE_LIST}"
    exit 1
fi

log "INFO: ResumeProgram called with nodes: ${NODE_LIST}"

# --- Invoke ORB ---
if [ "${ORB_MODE}" = "api" ]; then
    # REST API mode
    PAYLOAD="{\"node_names\": [\"${NODE_LIST// /\", \"}\"], \"request_type\": \"provision\"}"
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" \
        "${ORB_API_URL}/api/v1/machines/request" 2>&1) || true

    HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
    BODY=$(echo "${RESPONSE}" | sed '$d')

    if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
        log "INFO: API request succeeded (HTTP ${HTTP_CODE}): ${BODY}"
        exit 0
    else
        log "ERROR: API request failed (HTTP ${HTTP_CODE}): ${BODY}"
        exit 1
    fi
else
    # CLI mode (default)
    if orb machines request --nodes "${NODE_LIST}" --scheduler slurm >> "${LOG_FILE}" 2>&1; then
        log "INFO: CLI request succeeded for nodes: ${NODE_LIST}"
        exit 0
    else
        RC=$?
        log "ERROR: CLI request failed (exit ${RC}) for nodes: ${NODE_LIST}"
        exit "${RC}"
    fi
fi
