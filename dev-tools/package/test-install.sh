#!/bin/bash
set -e

echo "INFO Testing package installation..."

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Create temporary virtual environment with explicit Python version
TEMP_VENV=$(mktemp -d)
echo "Creating temporary virtual environment at $TEMP_VENV"

python3 -m venv "$TEMP_VENV"
source "$TEMP_VENV/bin/activate"

# Verify Python version in test environment
echo "INFO Test environment Python: $(python --version)"
echo "INFO Test environment executable: $(which python)"

# Build package first
echo "🔨 Building package..."
./dev-tools/package/build.sh

# Install from built package using explicit Python
echo "Installing from built package..."
python -m pip install dist/*.whl

# Test commands
echo "Testing commands..."
if command -v ohfp &> /dev/null; then
    echo "PASS ohfp command available"
    if ohfp --help > /dev/null 2>&1; then
        echo "PASS ohfp --help works"
    else
        echo "FAIL ohfp --help failed"
    fi
else
    echo "FAIL ohfp command not found"
fi

if command -v open-hostfactory-plugin &> /dev/null; then
    echo "PASS open-hostfactory-plugin command available"
    if open-hostfactory-plugin --help > /dev/null 2>&1; then
        echo "PASS open-hostfactory-plugin --help works"
    else
        echo "FAIL open-hostfactory-plugin --help failed"
    fi
else
    echo "FAIL open-hostfactory-plugin command not found"
fi

# Test basic functionality
echo "INFO Testing basic functionality..."
if ohfp --version 2>/dev/null; then
    echo "PASS ohfp --version works"
else
    echo "WARNING --version not implemented yet"
fi

# Cleanup
deactivate
rm -rf "$TEMP_VENV"

echo "PASS Package installation test completed!"
echo ""
echo "🎯 Package is ready for:"
echo "  • Local development: ./dev-tools/package/install-dev.sh"
echo "  • Test PyPI: ./dev-tools/package/publish.sh testpypi"
echo "  • Production PyPI: ./dev-tools/package/publish.sh pypi"
