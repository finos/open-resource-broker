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
        # Check if we're in a UV project (current dir or parent dirs)
        current_dir="$PWD"
        while [ "$current_dir" != "/" ]; do
            if [ -f "$current_dir/pyproject.toml" ] || [ -f "$current_dir/uv.lock" ]; then
                echo "Using UV-managed environment..."
                return 0
            fi
            current_dir=$(dirname "$current_dir")
        done
    fi

    # Fallback to .venv if it exists (check parent dirs too)
    current_dir="$PWD"
    while [ "$current_dir" != "/" ]; do
        if [ -f "$current_dir/.venv/bin/activate" ]; then
            echo "Using existing .venv environment..."
            # shellcheck disable=SC1091
            source "$current_dir/.venv/bin/activate"
            return 0
        fi
        current_dir=$(dirname "$current_dir")
    done

    # Use system environment as last resort
    echo "Using system environment..."
}

run_tool() {
    setup_environment

    echo "Running ${TOOL_NAME}..."

    # Find project root for UV
    project_root="$PWD"
    while [ "$project_root" != "/" ]; do
        if [ -f "$project_root/pyproject.toml" ] || [ -f "$project_root/uv.lock" ]; then
            break
        fi
        project_root=$(dirname "$project_root")
    done

    # Try different execution methods in order of preference
    if command -v uv >/dev/null 2>&1 && [ -f "$project_root/pyproject.toml" ]; then
        echo "Executing with UV..."
        # If we're in a subdirectory of the project, adjust paths for UV
        if [ "$PWD" != "$project_root" ]; then
            # We're in a subdirectory (like src/), need to adjust paths
            adjusted_args=""
            for arg in "$@"; do
                case "$arg" in
                    .*)
                        # Relative path - adjust it relative to project root
                        rel_to_root=$(realpath --relative-to="$project_root" "$PWD/$arg" 2>/dev/null || python3 -c "import os; print(os.path.relpath(os.path.join('$PWD', '$arg'), '$project_root'))")
                        adjusted_args="$adjusted_args $rel_to_root"
                        ;;
                    *)
                        # Other arguments - keep as is
                        adjusted_args="$adjusted_args $arg"
                        ;;
                esac
            done
            if [ -n "$adjusted_args" ]; then
                # shellcheck disable=SC2086
                (cd "$project_root" && uv run "${TOOL_NAME}" $adjusted_args)
            else
                (cd "$project_root" && uv run "${TOOL_NAME}")
            fi
        else
            # We're in project root
            uv run "${TOOL_NAME}" "$@"
        fi
    elif [ -f ".venv/bin/${TOOL_NAME}" ]; then
        echo "Executing with venv..."
        .venv/bin/"${TOOL_NAME}" "$@"
    elif command -v "${TOOL_NAME}" >/dev/null 2>&1; then
        echo "Executing with system..."
        "${TOOL_NAME}" "$@"
    else
        echo "Executing as Python module..."
        if command -v uv >/dev/null 2>&1 && [ -f "$project_root/pyproject.toml" ]; then
            # Same path adjustment for python -m
            if [ "$PWD" != "$project_root" ]; then
                adjusted_args=""
                for arg in "$@"; do
                    case "$arg" in
                        .*)
                            rel_to_root=$(realpath --relative-to="$project_root" "$PWD/$arg" 2>/dev/null || python3 -c "import os; print(os.path.relpath(os.path.join('$PWD', '$arg'), '$project_root'))")
                            adjusted_args="$adjusted_args $rel_to_root"
                            ;;
                        *)
                            adjusted_args="$adjusted_args $arg"
                            ;;
                    esac
                done
            if [ -n "$adjusted_args" ]; then
                # shellcheck disable=SC2086
                (cd "$project_root" && uv run python -m "${TOOL_NAME}" $adjusted_args)
            else
                (cd "$project_root" && uv run python -m "${TOOL_NAME}")
            fi
            else
                uv run python -m "${TOOL_NAME}" "$@"
            fi
        else
            python3 -m "${TOOL_NAME}" "$@"
        fi
    fi
}

# Execute the tool
run_tool "$@"
