#!/bin/bash

# Configuration - can be overridden by environment variables
USE_LOCAL_DEV=${USE_LOCAL_DEV:-false}
PACKAGE_NAME=${OHFP_PACKAGE_NAME:-"open-hostfactory-plugin"}
PACKAGE_COMMAND=${OHFP_COMMAND:-"ohfp"}

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Check if Python is available
if command -v python3 &> /dev/null; then
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
    # Local development mode - use src/run.py from project root
    echo "Using local development mode (src/run.py)" >&2

    # Add project root to PYTHONPATH
    export PYTHONPATH="${PROJECT_ROOT}:${PYTHONPATH}"

    # Check if src/run.py exists
    if [ ! -f "${PROJECT_ROOT}/src/run.py" ]; then
        echo "Error: src/run.py not found at ${PROJECT_ROOT}/src/run.py" >&2
        echo "Make sure you're running from the correct directory or install the package." >&2
        exit 1
    fi

    # Parse arguments to separate global flags from command
    global_args=()
    command_args=()

    # First, collect all arguments
    all_args=("$@")

    # Separate global flags from command arguments
    i=0
    while [ $i -lt ${#all_args[@]} ]; do
        arg="${all_args[$i]}"
        case "$arg" in
            -f|--file|-d|--data|--config|--log-level|--format|--output|--scheduler)
                # These flags need a value
                global_args+=("$arg")
                i=$((i + 1))
                if [ $i -lt ${#all_args[@]} ]; then
                    global_args+=("${all_args[$i]}")
                fi
                ;;
            --quiet|--verbose|--dry-run)
                # These are boolean flags
                global_args+=("$arg")
                ;;
            -*)
                # Other flags
                global_args+=("$arg")
                ;;
            *)
                # Non-flag arguments go to command
                command_args+=("$arg")
                ;;
        esac
        i=$((i + 1))
    done

    # Execute the Python script with global args first, then command args
    exec $PYTHON_CMD "${PROJECT_ROOT}/src/run.py" "${global_args[@]}" "${command_args[@]}"

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
    exec "$PACKAGE_COMMAND" "--legacy" "$@"
fi
