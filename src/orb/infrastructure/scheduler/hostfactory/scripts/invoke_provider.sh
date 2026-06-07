#!/bin/bash

# HF_LOGDIR: set by HostFactory to its own log directory (used for scripts.log).
# ORB_LOG_DIR: set by ORB to control where the orb process writes its own logs.
# These are intentionally separate — do not conflate them.
export HF_LOGDIR=${HF_LOGDIR:-./}

SCRIPTS_LOG_FILE="${HF_LOGDIR}/scripts.log"

USE_LOCAL_DEV=${USE_LOCAL_DEV:-false}
LOG_SCRIPTS=${LOG_SCRIPTS:-false}
PACKAGE_NAME=${ORB_PACKAGE_NAME:-"open-resource-broker"}
PACKAGE_COMMAND=${ORB_COMMAND:-"orb"}

if [ "$LOG_SCRIPTS" = "true" ] || [ "$LOG_SCRIPTS" = "1" ]; then
{

	    echo "=== Input Arguments Start ==="
	    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
	    echo "Arguments:" "$@"

    prev_arg=""
    for i in "$@"; do
        if [ "$prev_arg" = "-f" ] && [ -f "$i" ]; then
            echo "File content:"
            # This line prints the content of the file with indentation
            cat "$i" | sed 's/^/      /'
        fi
        prev_arg="$i"
    done
    echo "=== Input Arguments End ==="
    echo "OUTPUT:"
} >> "$SCRIPTS_LOG_FILE"
fi

# Source venv if ORB_VENV_PATH is set
if [ -n "${ORB_VENV_PATH}" ]; then
    if [ -f "${ORB_VENV_PATH}/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "${ORB_VENV_PATH}/bin/activate"
    else
        echo "Error: ORB_VENV_PATH set but ${ORB_VENV_PATH}/bin/activate not found" >&2
        exit 1
    fi
fi

# Get script directory and project root
# Walk up from SCRIPT_DIR until we find ${PACKAGE_ROOT}/run.py (works whether scripts are at
# scripts/ after orb init or at src/infrastructure/scheduler/hostfactory/scripts/ in dev)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PACKAGE_ROOT=$(python3 -c "import yaml; print(yaml.safe_load(open('.project.yml'))['build']['package_root'])" 2>/dev/null || echo "src/orb")
PROJECT_ROOT="$SCRIPT_DIR"
while [ "$PROJECT_ROOT" != "/" ] && [ ! -f "$PROJECT_ROOT/${PACKAGE_ROOT}/run.py" ]; do
    PROJECT_ROOT="$(dirname "$PROJECT_ROOT")"
done

# Prefer project virtualenv if present, otherwise fall back to system python
if [ -x "${PROJECT_ROOT}/.venv/bin/python" ]; then
    PYTHON_CMD="${PROJECT_ROOT}/.venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON_CMD=python3
elif command -v python &> /dev/null; then
    PYTHON_CMD=python
else
    echo "Error: Python not found" >&2
    exit 1
fi

# Disable Console Logging for HF Scripts
HF_LOGGING_CONSOLE_ENABLED=${HF_LOGGING_CONSOLE_ENABLED:-false}
export HF_LOGGING_CONSOLE_ENABLED

# Determine execution mode
if [ "$USE_LOCAL_DEV" = "true" ] || [ "$USE_LOCAL_DEV" = "1" ]; then
    # Local development mode - use python -m orb from project root
    # echo "Using local development mode (python -m orb)" >&2

    # Add project root and src/ to PYTHONPATH so orb package is importable
    export PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}:${PYTHONPATH}"

    # Check if ${PACKAGE_ROOT}/run.py exists
    if [ ! -f "${PROJECT_ROOT}/${PACKAGE_ROOT}/run.py" ]; then
        echo "Error: ${PACKAGE_ROOT}/run.py not found at ${PROJECT_ROOT}/${PACKAGE_ROOT}/run.py" >&2
        echo "Make sure you're running from the correct directory or install the package." >&2
        exit 1
    fi

    # Extract -f <file> from args — the only global flag run.py needs before the subcommand.
    # Everything else passes through verbatim so subcommand flags (--force, --all, etc.)
    # always reach the right handler without this script needing to know about them.
    file_args=()
    pass_args=()

    i=0
    all_args=("$@")
    while [ $i -lt ${#all_args[@]} ]; do
        arg="${all_args[$i]}"
        if [ "$arg" = "-f" ] || [ "$arg" = "--file" ]; then
            file_args+=("$arg")
            i=$((i + 1))
            if [ $i -lt ${#all_args[@]} ]; then
                file_args+=("${all_args[$i]}")
            fi
        else
            pass_args+=("$arg")
        fi
        i=$((i + 1))
    done

    # Execute: ${PACKAGE_ROOT}/run.py [-f <file>] <everything else verbatim>
	    "$PYTHON_CMD" "${PROJECT_ROOT}/${PACKAGE_ROOT}/run.py" "${file_args[@]}" "${pass_args[@]}" 2>&1 | tee -a "$SCRIPTS_LOG_FILE"
	    exit "${PIPESTATUS[0]}"

else
    # Package mode - use installed command
    echo "Using installed package mode ($PACKAGE_COMMAND)" >&2

    # Check if package command is available
    if ! command -v "$PACKAGE_COMMAND" &> /dev/null; then
        echo "Error: $PACKAGE_COMMAND command not found" >&2
        echo "" >&2
        echo "Options:" >&2
        echo "  1. Install package: pip install $PACKAGE_NAME" >&2
        echo "  2. Use local development: USE_LOCAL_DEV=true $0 $*" >&2
        echo "  3. Install in dev mode: ./dev-tools/package/install_dev.sh" >&2
        exit 1
    fi

	    # Execute the installed command with all arguments
	    "$PACKAGE_COMMAND" "$@" 2>&1 | tee -a "$SCRIPTS_LOG_FILE"
	    exit "${PIPESTATUS[0]}"
	fi
