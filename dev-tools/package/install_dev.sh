#!/bin/bash
set -e

echo "INFO: Installing open-hostfactory-plugin in development mode..."

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

# Hybrid installation approach
if command -v uv >/dev/null 2>&1; then
    echo "INFO: Using uv for faster development installation..."

    # Upgrade pip and build tools first with uv
    echo "INFO: Upgrading pip and build tools with uv..."
    uv pip install --upgrade pip setuptools wheel

    # Install in editable mode using uv
    echo "INFO: Installing package in editable mode with uv..."
    uv pip install -e ".[dev]"

    echo "SUCCESS: Development installation completed with uv!"
    echo "TIP: uv provided faster dependency resolution and installation"
else
    echo "INFO: Using pip (uv not available)..."

    # Upgrade pip and build tools first
    echo "INFO: Upgrading pip and build tools..."
    python -m pip install --upgrade pip setuptools wheel

    # Install in editable mode using the venv's Python explicitly
    echo "INFO: Installing package in editable mode..."
    python -m pip install -e ".[dev]"

    echo "SUCCESS: Development installation completed with pip!"
    echo "TIP: Install uv for faster development setup: pip install uv"
fi

# Verify installation
echo ""
echo "INFO: Verifying installation..."
if python -c "import src.domain.base.entities" 2>/dev/null; then
    echo "SUCCESS: Package imports working correctly"
else
    echo "WARNING: Package import verification failed"
fi

# Show installed packages count
PACKAGE_COUNT=$(python -m pip list | wc -l)
echo "INFO: Installed packages: $PACKAGE_COUNT"

echo ""
echo "INFO: Development environment ready!"
echo "  • Run tests: make test"
echo "  • Run linting: make lint"
echo "  • Start API: python -m src.interface.api"
echo "  • CLI help: python -m src.interface.cli --help"
python -m pip install -e .

# Install development dependencies
echo "INFO Installing development dependencies..."
python -m pip install -r requirements-dev.txt

echo "SUCCESS Development installation complete!"
echo ""
echo "INFO Testing installation..."

# Test commands
if command -v ohfp &> /dev/null; then
    echo "SUCCESS ohfp command available"
    # Test the command works
    if ohfp --help > /dev/null 2>&1; then
        echo "SUCCESS ohfp --help works"
    else
        echo "WARNING ohfp command found but --help failed"
    fi
else
    echo "ERROR ohfp command not found"
fi

if command -v open-hostfactory-plugin &> /dev/null; then
    echo "SUCCESS open-hostfactory-plugin command available"
    # Test the command works
    if open-hostfactory-plugin --help > /dev/null 2>&1; then
        echo "SUCCESS open-hostfactory-plugin --help works"
    else
        echo "WARNING open-hostfactory-plugin command found but --help failed"
    fi
else
    echo "ERROR open-hostfactory-plugin command not found"
fi

echo ""
echo "INFO: Available commands:"
echo "  ohfp --help                        # Short command"
echo "  open-hostfactory-plugin --help     # Long command"
echo ""
echo "INFO: Example usage:"
echo "  ohfp templates list"
echo "  ohfp machines request basic-template 2"
echo "  open-hostfactory-plugin providers health"
echo ""
echo "INFO: Host Factory integration:"
echo "  USE_LOCAL_DEV=true ./scripts/requestMachines.sh basic-template 2"
