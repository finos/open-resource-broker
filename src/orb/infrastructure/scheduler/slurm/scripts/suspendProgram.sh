#!/bin/bash
# SLURM SuspendProgram hook for ORB — powers down cloud nodes via ORB deprovisioning.
# Configure in slurm.conf: SuspendProgram=/path/to/suspendProgram.sh
# SLURM invokes this with node list as $1 (e.g. "compute-[001-003]" or "node1 node2")
#
# Dynamic Slot Model:
# - Always TERMINATES instances (never stops) — they are ephemeral
# - Node name ↔ machine ID mappings are cleared after termination
# - Next resume will provision completely fresh instances
# - No data residency between cycles — use shared storage
set -euo pipefail

export ORB_ROOT_DIR=${ORB_ROOT_DIR:-/usr/orb}

LOG_DIR="${SLURM_ORB_LOG_DIR:-/var/log/orb}"
LOG_FILE="${LOG_DIR}/suspend_program.log"
ORB_MODE="${SLURM_ORB_MODE:-cli}"  # "cli" or "api"
ORB_API_URL="${SLURM_ORB_API_URL:-http://localhost:8000}"

mkdir -p "${LOG_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [suspendProgram] $*" >> "${LOG_FILE}"
}

# --- Input validation ---
if [ -z "${1:-}" ]; then
    log "ERROR: No node names provided"
    exit 1
fi

NODE_LIST="$1"

# Validate node names: alphanumeric, hyphens, brackets, commas, spaces only
if ! echo "${NODE_LIST}" | grep -qE '^[a-zA-Z0-9 \-\[\],]+$'; then
    log "ERROR: Invalid node name characters in: ${NODE_LIST}"
    exit 1
fi

log "INFO: SuspendProgram called with nodes: ${NODE_LIST}"

# --- Single batch request to ORB (terminate all) ---
if [ "${ORB_MODE}" = "api" ]; then
    PAYLOAD="{\"node_names\": [\"${NODE_LIST// /\", \"}\"], \"request_type\": \"deprovision\"}"
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" \
        "${ORB_API_URL}/api/v1/machines/return" 2>&1) || true

    HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
    BODY=$(echo "${RESPONSE}" | sed '$d')

    if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
        log "INFO: Batch API terminate succeeded (HTTP ${HTTP_CODE})"
        exit 0
    else
        log "ERROR: Batch API terminate failed (HTTP ${HTTP_CODE}): ${BODY}"
        exit 1
    fi
else
    if orb machines terminate --nodes "${NODE_LIST}" --force >> "${LOG_FILE}" 2>&1; then
        log "INFO: Batch CLI terminate succeeded for nodes: ${NODE_LIST}"
        exit 0
    else
        RC=$?
        log "ERROR: Batch CLI terminate failed (exit ${RC}) for nodes: ${NODE_LIST}"
        exit "${RC}"
    fi
fi
