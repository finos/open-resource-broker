#!/bin/bash
set -e

# CI Documentation Deploy Script
# Handles both venv and system-wide installations intelligently

echo "Deploying documentation to GitHub Pages..."

cd docs

# Try different mike installation methods in order of preference
if command -v ../.venv/bin/mike >/dev/null 2>&1; then
    echo "Using venv mike..."
    ../.venv/bin/mike deploy --push --update-aliases latest
elif command -v mike >/dev/null 2>&1; then
    echo "Using system mike..."
    mike deploy --push --update-aliases latest
else
    echo "Using Python module mike..."
    python3 -m mike deploy --push --update-aliases latest
fi

echo "Documentation deployed successfully"
