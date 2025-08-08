#!/bin/bash
# Pre-commit validation script - reads .pre-commit-config.yaml and executes hooks dynamically

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
DEBUG=false
EXTENDED=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --debug|-d)
            DEBUG=true
            shift
            ;;
        --extended|-e)
            EXTENDED=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--debug|-d] [--extended|-e]"
            echo "  --debug/-d     Show detailed error output"
            echo "  --extended/-e  Show extended information"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Get to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

echo "Running pre-commit checks (reading from .pre-commit-config.yaml)..."
if [ "$DEBUG" = true ]; then
    echo -e "${BLUE}DEBUG: Running in debug mode${NC}"
fi

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

if [ "$EXTENDED" = true ]; then
    echo -e "${BLUE}Found $HOOK_COUNT hooks to execute${NC}"
fi

# Process each hook by index
for ((i=0; i<HOOK_COUNT; i++)); do
    name=$(yq ".repos[0].hooks[$i].name" .pre-commit-config.yaml)
    command=$(yq ".repos[0].hooks[$i].entry" .pre-commit-config.yaml)
    
    if [ "$EXTENDED" = true ]; then
        echo -e "${BLUE}Hook $((i+1))/$HOOK_COUNT: $name${NC}"
        echo -e "${BLUE}  Command: $command${NC}"
    fi
    
    echo -n "Running $name... "
    
    # Capture output for debug mode
    if [ "$DEBUG" = true ]; then
        output=$(eval "$command" 2>&1)
        exit_code=$?
    else
        eval "$command" > /dev/null 2>&1
        exit_code=$?
    fi
    
    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}PASS${NC}"
    else
        # Check if this hook has warning_only comment
        if yq ".repos[0].hooks[$i]" .pre-commit-config.yaml | grep -q "warning_only: true"; then
            echo -e "${YELLOW}WARN${NC}"
            if [ "$DEBUG" = true ]; then
                echo -e "${YELLOW}  Output: $output${NC}"
            else
                echo "  Command: $command (warning only)"
            fi
            WARNED=1
        else
            echo -e "${RED}FAIL${NC}"
            if [ "$DEBUG" = true ]; then
                echo -e "${RED}  Output: $output${NC}"
            else
                echo "  Command: $command"
            fi
            FAILED=1
        fi
    fi
    
    if [ "$EXTENDED" = true ]; then
        echo ""
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
    if [ "$DEBUG" = false ]; then
        echo "Run with --debug to see detailed error output"
    fi
    exit 1
fi
