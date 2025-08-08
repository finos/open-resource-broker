#!/bin/bash
"""Pre-commit validation script - simulates .pre-commit-config.yaml hooks."""

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

echo "Running pre-commit checks (simulating .pre-commit-config.yaml)..."

# Check if virtual environment exists
if [ ! -f ".venv/bin/python" ]; then
    echo -e "${RED}ERROR: Virtual environment not found at .venv/${NC}"
    echo "Please create it first: python3 -m venv .venv"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Function to run check and report result
run_hook() {
    local name="$1"
    local command="$2"
    
    echo -n "Running $name... "
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        echo "  Command: $command"
        return 1
    fi
}

# Track failures
FAILED=0

echo "Simulating pre-commit hooks from .pre-commit-config.yaml:"
echo ""

# 1. Professional Quality Check
if ! run_hook "professional-quality-check" "python dev-tools/scripts/quality_check.py --strict"; then
    FAILED=1
fi

# 2. Validate Imports
if ! run_hook "validate-imports" "python dev-tools/scripts/validate_imports.py"; then
    FAILED=1
fi

# 3. Import Validation Tests
if ! run_hook "test-import-validation" "python -m pytest tests/test_import_validation.py -v"; then
    FAILED=1
fi

# 4. Check Deprecated Imports
if ! run_hook "check-deprecated-imports" "! grep -r 'from.*request.*value_objects.*import.*MachineStatus' . --include='*.py'"; then
    FAILED=1
fi

# 5. Comprehensive Security Scan
if ! run_hook "comprehensive-security-scan" "python dev-tools/security/security_scan.py"; then
    FAILED=1
fi

# 6. Bandit Security Check (Fallback)
if ! run_hook "bandit-security-check" "python -m bandit -r src/ -f json -q"; then
    FAILED=1
fi

# 7. Safety Dependency Check
if ! run_hook "safety-dependency-check" "python -m safety check --short-report"; then
    FAILED=1
fi

# Additional checks not in pre-commit config but useful
echo ""
echo "Additional validation checks:"

# 8. Workflow validation
if ! run_hook "validate-workflows" "python dev-tools/scripts/validate_workflows.py"; then
    FAILED=1
fi

# 9. CQRS validation
if ! run_hook "validate-cqrs" "python dev-tools/scripts/validate_cqrs.py"; then
    FAILED=1
fi

# 10. Architecture compliance
if ! run_hook "check-architecture" "python dev-tools/scripts/check_architecture.py --warn-only"; then
    FAILED=1
fi

# Summary
echo ""
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}SUCCESS: All pre-commit checks passed${NC}"
    echo "Ready to commit!"
    exit 0
else
    echo -e "${RED}FAILED: Some pre-commit checks failed${NC}"
    echo "Please fix the issues above before committing."
    echo ""
    echo "Quick fixes:"
    echo "  make format  # Fix formatting issues"
    echo "  make lint    # Run all linting checks"
    echo "  make test    # Run tests"
    exit 1
fi
