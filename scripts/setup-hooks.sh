#!/bin/bash
# Setup script for git hooks
# Run this once after cloning the repository

set -e

echo "Setting up git hooks..."

# Configure git to use .githooks/ directory
git config core.hooksPath .githooks

echo "Git hooks configured!"
echo "Hooks will now run from .githooks/ (version controlled)"
echo ""
echo "Installed hooks:"
ls -1 .githooks/ | grep -v README
