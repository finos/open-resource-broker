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

if [ "$QUIET" = false ]; then
    echo "INFO: Building open-hostfactory-plugin package..."
fi

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Use centralized tool runner
RUN_TOOL="./dev-tools/scripts/run_tool.sh"

# Clean previous builds
if [ "$QUIET" = false ]; then
    echo "INFO: Cleaning previous builds..."
fi
rm -rf dist/ build/ -- *.egg-info/

# Install build dependencies if needed
if [ "$QUIET" = false ]; then
    echo "INFO: Checking build dependencies..."
fi
if ! $RUN_TOOL python -c "import build" 2>/dev/null; then
    if [ "$QUIET" = false ]; then
        echo "INFO: Installing build dependencies..."
    fi
    if command -v uv >/dev/null 2>&1; then
        $RUN_TOOL uv add --dev build >/dev/null 2>&1
    else
        $RUN_TOOL pip install build >/dev/null 2>&1
    fi
fi

# Build package
if [ "$QUIET" = false ]; then
    echo "INFO: Building package..."
fi
BUILD_ARGS="${BUILD_ARGS:-}"
if [ "$QUIET" = true ]; then
    # Suppress all output in quiet mode
    if [ -n "$BUILD_ARGS" ]; then
        # shellcheck disable=SC2086
        $RUN_TOOL python -m build $BUILD_ARGS >/dev/null 2>&1
    else
        $RUN_TOOL python -m build >/dev/null 2>&1
    fi
else
    # Normal output
    if [ -n "$BUILD_ARGS" ]; then
        # shellcheck disable=SC2086
        $RUN_TOOL python -m build $BUILD_ARGS 2>/dev/null
    else
        $RUN_TOOL python -m build 2>/dev/null
    fi
fi

if [ "$QUIET" = true ]; then
    # Show essential info even in quiet mode
    echo "SUCCESS: Package built successfully!"
    echo "INFO: Files created:"
    ls -1 dist/
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
