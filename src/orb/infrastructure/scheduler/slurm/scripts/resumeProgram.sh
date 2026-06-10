#!/bin/bash
# SLURM ResumeProgram hook for ORB — powers up cloud nodes via ORB provisioning.
# Configure in slurm.conf: ResumeProgram=/path/to/resumeProgram.sh
# SLURM invokes this with node list as $1 (e.g. "compute-[001-003]" or "node1 node2")
#
# Dynamic Slot Model:
# - Node names are fungible capacity slots (not persistent identities)
# - Each resume provisions FRESH instances (no data residency)
# - Assignment of instances to slot names is arbitrary
# - All persistent data should live on shared storage (FSx, EFS, NFS)
#
# SLURM Resume Lifecycle:
# 1. SLURM calls this script with node names (single batch)
# 2. ORB provisions cloud instances in ONE batch request
# 3. This script reports back node addresses via scontrol update
# 4. slurmd starts on the provisioned nodes and registers with slurmctld
# 5. SLURM clears POWERING_UP flag → nodes become IDLE → jobs can run
#
# IMPORTANT: The provisioned instance's AMI/image MUST have slurmd
# pre-installed and configured to connect to this cluster's slurmctld.
set -euo pipefail

# Ensure Python reads source files as UTF-8 regardless of system locale
export PYTHONUTF8=1

export ORB_ROOT_DIR=${ORB_ROOT_DIR:-/usr/orb}

# Source hook configuration if available
if [ -f "${ORB_ROOT_DIR}/slurm_hooks.env" ]; then
    . "${ORB_ROOT_DIR}/slurm_hooks.env"
fi

LOG_DIR="${SLURM_ORB_LOG_DIR:-/var/log/orb}"
LOG_FILE="${LOG_DIR}/resume_program.log"
ORB_MODE="${SLURM_ORB_MODE:-cli}"  # "cli" or "api"
ORB_API_URL="${SLURM_ORB_API_URL:-http://localhost:8000}"
TEMPLATE_ID="${SLURM_ORB_TEMPLATE_ID:-default}"

mkdir -p "${LOG_DIR}"

log() {
    echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [resumeProgram] $*" >> "${LOG_FILE}"
}

if [ "${TEMPLATE_ID}" = "default" ]; then
    log "WARN: SLURM_ORB_TEMPLATE_ID not set, using 'default' — set this in ${ORB_ROOT_DIR}/slurm_hooks.env"
fi

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

log "INFO: ResumeProgram called with nodes: ${NODE_LIST}"

# --- Compute node count from SLURM hostlist ---
NUM_NODES=$(scontrol show hostnames "${NODE_LIST}" | wc -l | tr -d ' ')
log "INFO: Resolved ${NUM_NODES} nodes from hostlist"

# --- Single batch request to ORB ---
if [ "${ORB_MODE}" = "api" ]; then
    PAYLOAD="{\"node_names\": [\"${NODE_LIST// /\", \"}\"], \"request_type\": \"provision\"}"
    RESPONSE=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d "${PAYLOAD}" \
        "${ORB_API_URL}/api/v1/machines/request" 2>&1) || true

    HTTP_CODE=$(echo "${RESPONSE}" | tail -1)
    BODY=$(echo "${RESPONSE}" | sed '$d')

    if [ "${HTTP_CODE}" -ge 200 ] && [ "${HTTP_CODE}" -lt 300 ]; then
        log "INFO: Batch API request succeeded (HTTP ${HTTP_CODE})"
    else
        log "ERROR: Batch API request failed (HTTP ${HTTP_CODE}): ${BODY}"
        exit 1
    fi
else
    if ! BODY=$(orb machines request "${TEMPLATE_ID}" ${NUM_NODES} --nodes "${NODE_LIST}" 2>>"${LOG_FILE}"); then
        RC=$?
        log "ERROR: Batch CLI request failed (exit ${RC}) for nodes: ${NODE_LIST}"
        exit "${RC}"
    fi
    log "INFO: Batch CLI request succeeded for nodes: ${NODE_LIST}"
fi

# --- Register node addresses with SLURM ---
if command -v jq >/dev/null 2>&1 && [ -n "${BODY:-}" ]; then
    echo "${BODY}" | jq -r '.machines[]? | "\(.node_name) \(.private_ip_address)"' 2>/dev/null | \
    while read -r NODE_NAME NODE_IP; do
        if [ -n "${NODE_NAME}" ] && [ -n "${NODE_IP}" ] && [ "${NODE_IP}" != "null" ]; then
            if scontrol update "NodeName=${NODE_NAME}" "NodeAddr=${NODE_IP}" 2>>"${LOG_FILE}"; then
                log "INFO: Registered NodeAddr=${NODE_IP} for ${NODE_NAME}"
            else
                log "WARN: scontrol update failed for ${NODE_NAME} (slurmd fallback)"
            fi
        fi
    done
else
    log "INFO: jq not available or no body — skipping scontrol update (slurmd will self-register)"
fi

exit 0
