#!/bin/bash
set -e

# Centralized tool execution script with environment setup
# Usage: run_tool.sh <tool_name> [args...]
# Handles environment setup and tries different execution methods

TOOL_NAME="$1"
shift  # Remove tool name from arguments

setup_environment() {
    # UV-first approach (fastest and most reliable)
    if command -v uv >/dev/null 2>&1; then
        # Check if we're in a UV project
        if [ -f "pyproject.toml" ] || [ -f "uv.lock" ]; then
            echo "Using UV-managed environment..."
            return 0
        fi
    fi

    # Fallback to .venv if it exists
    if [ -f ".venv/bin/activate" ]; then
        echo "Using existing .venv environment..."
        # shellcheck disable=SC1091
        source .venv/bin/activate
        return 0
    fi

    # Create venv if none exists and we're in a project directory
    if [ -f "pyproject.toml" ] && [ ! -d ".venv" ]; then
        echo "Creating virtual environment..."
        if command -v uv >/dev/null 2>&1; then
            uv venv
        else
            python3 -m venv .venv
            # shellcheck disable=SC1091
            source .venv/bin/activate
        fi
        return 0
    fi

    # Use system environment as last resort
    echo "Using system environment..."
}

run_tool() {
    setup_environment

    echo "Running ${TOOL_NAME}..."

    # Try different execution methods in order of preference
    if command -v uv >/dev/null 2>&1 && { [ -f "pyproject.toml" ] || [ -f "uv.lock" ]; }; then
        echo "Executing with UV..."
        uv run "${TOOL_NAME}" "$@"
    elif [ -f ".venv/bin/${TOOL_NAME}" ]; then
        echo "Executing with venv..."
        .venv/bin/"${TOOL_NAME}" "$@"
    elif command -v "${TOOL_NAME}" >/dev/null 2>&1; then
        echo "Executing with system..."
        "${TOOL_NAME}" "$@"
    else
        echo "Executing as Python module..."
        python3 -m "${TOOL_NAME}" "$@"
    fi
}

# Execute the tool
run_tool "$@"
