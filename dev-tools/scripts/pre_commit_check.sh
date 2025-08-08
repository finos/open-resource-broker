#!/bin/bash
"""Pre-commit validation script - reads .pre-commit-config.yaml and executes hooks dynamically."""

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Get to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

echo "Running pre-commit checks (reading from .pre-commit-config.yaml)..."

# Check if config file exists
if [ ! -f ".pre-commit-config.yaml" ]; then
    echo -e "${RED}ERROR: .pre-commit-config.yaml not found${NC}"
    exit 1
fi

# Extract hook entries from config file
HOOKS=$(yq '.repos[0].hooks[].entry' .pre-commit-config.yaml)
NAMES=$(yq '.repos[0].hooks[].name' .pre-commit-config.yaml)

# Convert to arrays
readarray -t hook_entries <<< "$HOOKS"
readarray -t hook_names <<< "$NAMES"

# Track failures
FAILED=0

# Run each hook
for i in "${!hook_entries[@]}"; do
    name="${hook_names[$i]}"
    command="${hook_entries[$i]}"
    
    echo -n "Running $name... "
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        echo -e "${RED}FAIL${NC}"
        echo "  Command: $command"
        FAILED=1
    fi
done

# Summary
echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}SUCCESS: All pre-commit checks passed${NC}"
    echo "Ready to commit!"
    exit 0
else
    echo -e "${RED}FAILED: Some pre-commit checks failed${NC}"
    echo "Please fix the issues above before committing."
    exit 1
fi
