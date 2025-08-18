#!/bin/bash
set -e

echo "INFO: Building open-hostfactory-plugin package..."

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Use centralized tool runner
RUN_TOOL="./dev-tools/scripts/run_tool.sh"

# Clean previous builds
echo "INFO: Cleaning previous builds..."
rm -rf dist/ build/ -- *.egg-info/

# Install build dependencies if needed
echo "INFO: Checking build dependencies..."
if ! $RUN_TOOL python -c "import build" 2>/dev/null; then
    echo "INFO: Installing build dependencies..."
    if command -v uv >/dev/null 2>&1; then
        $RUN_TOOL uv add --dev build
    else
        $RUN_TOOL pip install build
    fi
fi

# Build package
echo "INFO: Building package..."
BUILD_ARGS="${BUILD_ARGS:-}"
if [ -n "$BUILD_ARGS" ]; then
    # shellcheck disable=SC2086
    $RUN_TOOL python -m build $BUILD_ARGS
else
    $RUN_TOOL python -m build
fi

echo "SUCCESS: Package built successfully!"
echo "INFO: Files created:"
ls -la dist/

echo ""
echo "INFO: Next steps:"
echo "  • Test installation: make test-install"
echo "  • Publish to test PyPI: make publish-test"
echo "  • Publish to PyPI: make publish"
