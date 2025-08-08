#!/bin/bash
# Pre-commit validation script - reads .pre-commit-config.yaml and executes hooks dynamically

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check if yq is available
if ! command -v yq >/dev/null 2>&1; then
    echo -e "${RED}ERROR: yq not found. Install with: brew install yq${NC}"
    exit 1
fi

# Track failures and warnings
FAILED=0
WARNED=0

# Get hook count
HOOK_COUNT=$(yq '.repos[0].hooks | length' .pre-commit-config.yaml)

# Process each hook by index
for ((i=0; i<HOOK_COUNT; i++)); do
    name=$(yq ".repos[0].hooks[$i].name" .pre-commit-config.yaml)
    command=$(yq ".repos[0].hooks[$i].entry" .pre-commit-config.yaml)
    
    echo -n "Running $name... "
    
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
    else
        # Check if this hook has warning_only comment
        if yq ".repos[0].hooks[$i]" .pre-commit-config.yaml | grep -q "warning_only: true"; then
            echo -e "${YELLOW}WARN${NC}"
            echo "  Command: $command (warning only)"
            WARNED=1
        else
            echo -e "${RED}FAIL${NC}"
            echo "  Command: $command"
            FAILED=1
        fi
    fi
done

# Summary
echo ""
if [ $FAILED -eq 0 ]; then
    if [ $WARNED -eq 1 ]; then
        echo -e "${YELLOW}SUCCESS: All critical pre-commit checks passed (some warnings)${NC}"
    else
        echo -e "${GREEN}SUCCESS: All pre-commit checks passed${NC}"
    fi
    echo "Ready to commit!"
    exit 0
else
    echo -e "${RED}FAILED: Some pre-commit checks failed${NC}"
    echo "Please fix the issues above before committing."
    exit 1
fi
