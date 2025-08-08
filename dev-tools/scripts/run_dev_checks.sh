#!/bin/bash
# Run development checks in container using existing pre-commit script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

cd "$PROJECT_ROOT"

# Generate pyproject.toml first
echo "Generating pyproject.toml..."
make generate-pyproject

# Build dev tools container
echo "Building dev tools container..."
docker build -t ohfp-dev-tools -f dev-tools/docker/Dockerfile.dev-tools .

# Run checks using our existing pre-commit Python script in container
case "${1:-all}" in
    all)
        echo "Running all pre-commit checks in container..."
        docker run --rm -v "$PWD:/app" ohfp-dev-tools ./dev-tools/scripts/pre_commit_check.py
        ;;
    format)
        echo "Running format in container..."
        docker run --rm -v "$PWD:/app" ohfp-dev-tools make format
        ;;
    help|*)
        echo "Usage: $0 [all|format]"
        echo "  all    - Run all pre-commit checks in container"
        echo "  format - Auto-format code in container (modifies files)"
        ;;
esac
