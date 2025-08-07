#!/bin/bash
set -e

# CI Documentation Build Script
# Handles both venv and system-wide installations intelligently

echo "Building documentation for CI testing..."

cd docs

# Try different mkdocs installation methods in order of preference
if command -v ../.venv/bin/mkdocs >/dev/null 2>&1; then
    echo "Using venv mkdocs..."
    ../.venv/bin/mkdocs build --strict
elif command -v mkdocs >/dev/null 2>&1; then
    echo "Using system mkdocs..."
    mkdocs build --strict
else
    echo "Using Python module mkdocs..."
    python3 -m mkdocs build --strict
fi

echo "Documentation built and validated for CI"
