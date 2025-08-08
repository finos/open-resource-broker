#!/bin/bash
set -e

echo "INFO: Building open-hostfactory-plugin package..."

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Ensure we're using the venv's Python explicitly
if [ ! -f ".venv/bin/python" ]; then
    echo "ERROR: Virtual environment not found at .venv/"
    echo "Please create it first: python3 -m venv .venv"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Verify Python version
echo "INFO: Using Python: $(python --version)"
echo "INFO: Python executable: $(which python)"

# Clean previous builds
echo "INFO: Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info/

# Install build dependencies with hybrid approach
if command -v uv >/dev/null 2>&1; then
    echo "INFO: Using uv for faster build dependency installation..."
    if ! python -c "import build" 2>/dev/null; then
        echo "INFO: Installing build dependencies with uv..."
        uv pip install build
    fi
    
    # Build package using uv (if available) or fallback to standard build
    echo "INFO: Building package with uv optimization..."
    python -m build --clean
else
    echo "INFO: Using pip (uv not available)..."
    if ! python -c "import build" 2>/dev/null; then
        echo "INFO: Installing build dependencies..."
        python -m pip install build
    fi
    
    # Build package using the venv's Python
    echo "INFO: Building package..."
    python -m build --clean
fi

echo "SUCCESS: Package built successfully!"
echo "INFO: Files created:"
ls -la dist/

echo ""
echo "INFO: Next steps:"
echo "  • Test installation: ./dev-tools/package/test-install.sh"
echo "  • Publish to test PyPI: ./dev-tools/package/publish.sh testpypi"
echo "  • Publish to PyPI: ./dev-tools/package/publish.sh pypi"
echo ""
if command -v uv >/dev/null 2>&1; then
    echo "TIP: uv was used for faster builds!"
else
    echo "TIP: Install uv for faster builds: pip install uv"
fi
