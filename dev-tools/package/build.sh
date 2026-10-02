#!/bin/bash
set -e

# Parse arguments
QUIET=false
for arg in "$@"; do
    case $arg in
        --quiet|-q)
            QUIET=true
            ;;
    esac
done

# Phase timing: written to stderr so these lines show up in CI logs even when
# --quiet suppresses the rest of this script's stdout output. Phases run
# sequentially (never nested within this script), so one pair of variables
# is enough to track whichever phase is currently open.
_phase_t0=0
_phase_name=""
_phase_start() {
    _phase_name="$1"
    _phase_t0=$(date +%s)
    printf '[timing] %s: start\n' "$_phase_name" >&2
}
_phase_end() {
    printf '[timing] %s: done in %ss\n' "$_phase_name" "$(( $(date +%s) - _phase_t0 ))" >&2
}

if [ "$QUIET" = false ]; then
    echo "INFO: Building open-resource-broker package..."
fi

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Use centralized tool runner
RUN_TOOL="./dev-tools/setup/run_tool.sh"

# Clean previous builds
if [ "$QUIET" = false ]; then
    echo "INFO: Cleaning previous builds..."
fi
rm -rf dist/ build/ -- *.egg-info/

# Verify build dependencies are present (declared in pyproject.toml dev deps)
_phase_start "dependency check/install"
if [ "$QUIET" = false ]; then
    echo "INFO: Checking build dependencies..."
fi
if ! $RUN_TOOL python -c "import build" 2>/dev/null; then
    if [ "$QUIET" = false ]; then
        echo "INFO: Installing build dependencies..."
    fi
    if command -v uv >/dev/null 2>&1; then
        uv pip install build --quiet
    else
        $RUN_TOOL pip install build
    fi
fi
_phase_end

# Compile the Reflex SPA bundle before packaging, unless skipped. The wheel
# is built from the sdist, which does not include dev-tools/, so the bundle
# must already exist on disk before the sdist is created.
case "$(printf '%s' "${ORB_SKIP_UI_BUILD:-}" | tr '[:upper:]' '[:lower:]')" in
    1|true|yes) _skip_ui_build=true ;;
    *) _skip_ui_build=false ;;
esac
if [ "$_skip_ui_build" = true ]; then
    if [ "$QUIET" = false ]; then
        echo "INFO: ORB_SKIP_UI_BUILD set — skipping SPA bundle build."
    fi
elif [ -f "src/orb/ui/_static/index.html" ]; then
    if [ "$QUIET" = false ]; then
        echo "INFO: SPA bundle already present; skipping rebuild."
    fi
else
    _phase_start "SPA build (build_ui.sh)"
    if [ "$QUIET" = false ]; then
        echo "INFO: Building SPA bundle..."
        ./dev-tools/package/build_ui.sh
    else
        ./dev-tools/package/build_ui.sh --quiet
    fi
    _phase_end
fi

# Build package
_phase_start "python -m build"
if [ "$QUIET" = false ]; then
    echo "INFO: Building package..."
fi
BUILD_ARGS="${BUILD_ARGS:-}"
if [ "$QUIET" = true ]; then
    # Suppress stdout only in quiet mode; stderr always surfaces so a build
    # failure is diagnosable instead of showing just the generic "Error 1"
    # from the enclosing make target.
    if [ -n "$BUILD_ARGS" ]; then
        # shellcheck disable=SC2086
        $RUN_TOOL python -m build --no-isolation $BUILD_ARGS >/dev/null
    else
        $RUN_TOOL python -m build --no-isolation >/dev/null
    fi
else
    # Normal output
    if [ -n "$BUILD_ARGS" ]; then
        # shellcheck disable=SC2086
        $RUN_TOOL python -m build --no-isolation $BUILD_ARGS
    else
        $RUN_TOOL python -m build --no-isolation
    fi
fi
_phase_end

if [ "$QUIET" = true ]; then
    # Show essential info even in quiet mode
    echo "SUCCESS: Package built successfully!"
    echo "INFO: Files created:"
    ls -1 dist/*
else
    echo "SUCCESS: Package built successfully!"
    echo "INFO: Files created:"
    ls -la dist/

    echo ""
    echo "INFO: Next steps:"
    echo "  • Test installation: make test-install"
    echo "  • Publish to test PyPI: make publish-test"
    echo "  • Publish to PyPI: make publish"
fi
