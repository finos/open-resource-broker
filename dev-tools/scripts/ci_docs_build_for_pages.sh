#!/bin/bash
set -e

# CI Documentation Build for GitHub Pages Script
# Builds documentation with mkdocs directly (no versioning)
# Output goes to docs/site for GitHub Pages artifact upload

echo "Building documentation for GitHub Pages deployment..."

cd docs

# Clean any existing site directory
rm -rf site

# Use mkdocs build directly for GitHub Pages
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

echo "Verifying site directory exists..."
if [ -d "site" ]; then
    echo "Site directory created successfully"
    echo "Site contents:"
    ls -la site/ | head -10
else
    echo "Error: site directory not found"
    exit 1
fi

echo "Documentation built successfully for GitHub Pages"
