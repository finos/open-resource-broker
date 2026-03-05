#!/bin/sh
# Install git hooks for this repository
# Run this once after cloning: ./dev-tools/setup/install-hooks.sh

set -e

REPO_ROOT=$(git rev-parse --show-toplevel)
HOOKS_DIR="$REPO_ROOT/.githooks"

echo "Installing git hooks..."

# Set core.hooksPath to our tracked hooks directory
git config core.hooksPath "$HOOKS_DIR"

# Make all hooks executable
chmod +x "$HOOKS_DIR"/*

echo "Git hooks installed from $HOOKS_DIR"
echo "Hooks active: $(ls "$HOOKS_DIR")"
