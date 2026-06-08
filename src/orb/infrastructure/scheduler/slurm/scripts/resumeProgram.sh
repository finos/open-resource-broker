#!/bin/bash
# SLURM ResumeProgram hook for ORB — powers up cloud nodes via ORB provisioning.
# Configure in slurm.conf: ResumeProgram=/path/to/resumeProgram.sh
# SLURM invokes this with node list as $1 (e.g. "compute-[001-003]" or "node1 node2")
#
# SLURM Resume Lifecycle:
# 1. SLURM calls this script with node names
# 2. ORB provisions cloud instances (EC2)
# 3. This script reports back node addresses via scontrol update
# 4. slurmd starts on the provisioned node and registers with slurmctld
# 5. SLURM clears POWERING_UP flag → node becomes IDLE → jobs can run
#
# IMPORTANT: The provisioned instance's AMI/image MUST have slurmd
# pre-installed and configured to connect to this cluster's slurmctld.
# Without slurmd, the node will never register and SLURM will timeout.
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

# --- Invoke ORB to provision ---
if [ "${ORB_MODE}" = "api" ]; then
    # REST API mode — response includes machine details with IPs
    PAYLOAD="{\"node_names\": [\"${NODE_LIST// /\", \"}\"], \"request_type\": \"provision\"}"
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" \
        "${ORB_API_URL}/api/v1/machines/request" 2>&1) || true

    HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
    BODY=$(echo "${RESPONSE}" | sed '$d')

    if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
        log "INFO: API request succeeded (HTTP ${HTTP_CODE}): ${BODY}"
    else
        log "ERROR: API request failed (HTTP ${HTTP_CODE}): ${BODY}"
        exit 1
    fi
else
    # CLI mode (default)
    if ! BODY=$(orb machines request --nodes "${NODE_LIST}" --scheduler slurm --format json 2>>"${LOG_FILE}"); then
        RC=$?
        log "ERROR: CLI request failed (exit ${RC}) for nodes: ${NODE_LIST}"
        exit "${RC}"
    fi
    log "INFO: CLI request succeeded for nodes: ${NODE_LIST}"
fi

# --- Register node addresses with SLURM ---
# Parse ORB response for node_name→IP mappings and update slurmctld.
# This allows SLURM to route communications before slurmd fully registers.
# Format expected: JSON with .machines[].node_name and .machines[].private_ip_address
if command -v jq >/dev/null 2>&1 && [ -n "${BODY:-}" ]; then
    echo "${BODY}" | jq -r '.machines[]? | "\(.node_name) \(.private_ip_address)"' 2>/dev/null | \
    while read -r NODE_NAME NODE_IP; do
        if [ -n "${NODE_NAME}" ] && [ -n "${NODE_IP}" ] && [ "${NODE_IP}" != "null" ]; then
            if scontrol update "NodeName=${NODE_NAME}" "NodeAddr=${NODE_IP}" 2>>"${LOG_FILE}"; then
                log "INFO: Registered NodeAddr=${NODE_IP} for ${NODE_NAME}"
            else
                log "WARN: scontrol update failed for ${NODE_NAME} (slurmd fallback will handle)"
            fi
        fi
    done
else
    log "INFO: jq not available or no response body — skipping scontrol update (slurmd will self-register)"
fi

exit 0
