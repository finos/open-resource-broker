#!/bin/bash
set -e

# Centralized tool execution script with environment setup
# Usage: run_tool.sh <tool_name> [args...]
# Handles environment setup and tries different execution methods

TOOL_NAME="$1"
shift  # Remove tool name from arguments

# Walk upward from $PWD looking for a UV/Python project root.
# Prints the path on success; prints nothing and returns 1 if not found.
find_project_root() {
    dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -f "$dir/pyproject.toml" ] || [ -f "$dir/uv.lock" ]; then
            echo "$dir"
            return 0
        fi
        dir=$(dirname "$dir")
    done
    return 1
}

# True if `uv` is on PATH AND functional in the given project root.
# Defends against `uv` being installed but unusable (e.g. broken Python pin).
uv_available() {
    project_root="$1"
    command -v uv >/dev/null 2>&1 || return 1
    [ -f "$project_root/pyproject.toml" ] || return 1
    (cd "$project_root" && uv --version >/dev/null 2>&1)
}

# True if .venv at the given path is usable in the current process
# (interpreter shebang/symlink resolves and runs). A `.venv/` mounted into a
# container from a different host will pass `[ -f activate ]` but fail this.
venv_usable() {
    venv_dir="$1"
    [ -f "$venv_dir/bin/activate" ] || return 1
    "$venv_dir/bin/python" -c '' 2>/dev/null
}

# When run_tool is invoked from a subdirectory, relative path arguments must be
# rewritten relative to the project root before being handed to `uv run`.
# Echoes the (space-separated, single-line) adjusted argv.
adjust_relative_args() {
    project_root="$1"
    shift
    out=""
    for arg in "$@"; do
        case "$arg" in
            .*)
                rel=$(realpath --relative-to="$project_root" "$PWD/$arg" 2>/dev/null \
                    || python3 -c "import os; print(os.path.relpath(os.path.join('$PWD', '$arg'), '$project_root'))")
                out="$out $rel"
                ;;
            *)
                out="$out $arg"
                ;;
        esac
    done
    echo "$out"
}

setup_environment() {
    project_root=$(find_project_root || true)

    if [ -n "$project_root" ] && uv_available "$project_root"; then
        echo "Using UV-managed environment..."
        return 0
    fi

    # Walk upward looking for a usable .venv. A .venv whose interpreter is
    # not executable (e.g. mounted into a container) is skipped, not used.
    dir="$PWD"
    while [ "$dir" != "/" ]; do
        if [ -d "$dir/.venv" ]; then
            if venv_usable "$dir/.venv"; then
                echo "Using existing .venv environment..."
                # shellcheck disable=SC1091
                source "$dir/.venv/bin/activate"
                return 0
            fi
            if [ -f "$dir/.venv/bin/activate" ]; then
                echo "Ignoring unusable .venv at $dir/.venv (interpreter not executable)..."
            fi
        fi
        dir=$(dirname "$dir")
    done

    echo "Using system environment..."
}

run_tool() {
    setup_environment

    echo "Running ${TOOL_NAME}..."

    project_root=$(find_project_root || echo "")

    if [ -n "$project_root" ] && uv_available "$project_root"; then
        echo "Executing with UV..."
        if [ "$PWD" != "$project_root" ]; then
            # shellcheck disable=SC2086
            adjusted_args=$(adjust_relative_args "$project_root" "$@")
            if [ -n "$adjusted_args" ]; then
                # shellcheck disable=SC2086
                (cd "$project_root" && uv run --no-sync "${TOOL_NAME}" $adjusted_args)
            else
                (cd "$project_root" && uv run --no-sync "${TOOL_NAME}")
            fi
        else
            uv run --no-sync "${TOOL_NAME}" "$@"
        fi
    elif [ -f ".venv/bin/${TOOL_NAME}" ] && venv_usable ".venv"; then
        echo "Executing with venv..."
        .venv/bin/"${TOOL_NAME}" "$@"
    elif command -v "${TOOL_NAME}" >/dev/null 2>&1; then
        echo "Executing with system..."
        "${TOOL_NAME}" "$@"
    else
        echo "Executing as Python module..."
        if [ -n "$project_root" ] && uv_available "$project_root"; then
            if [ "$PWD" != "$project_root" ]; then
                adjusted_args=$(adjust_relative_args "$project_root" "$@")
                if [ -n "$adjusted_args" ]; then
                    # shellcheck disable=SC2086
                    (cd "$project_root" && uv run --no-sync python -m "${TOOL_NAME}" $adjusted_args)
                else
                    (cd "$project_root" && uv run --no-sync python -m "${TOOL_NAME}")
                fi
            else
                uv run --no-sync python -m "${TOOL_NAME}" "$@"
            fi
        else
            python3 -m "${TOOL_NAME}" "$@"
        fi
    fi
}

# Execute the tool
run_tool "$@"
