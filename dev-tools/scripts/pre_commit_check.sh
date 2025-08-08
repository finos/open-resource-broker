#!/bin/bash
"""Pre-commit validation script - run all checks before committing."""

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

echo "Running pre-commit checks..."

# Check if virtual environment exists
if [ ! -f ".venv/bin/python" ]; then
    echo -e "${RED}ERROR: Virtual environment not found at .venv/${NC}"
    echo "Please create it first: python3 -m venv .venv"
    exit 1
fi

# Activate virtual environment
source .venv/bin/activate

# Function to run check and report result
run_check() {
    local name="$1"
    local command="$2"
    
    echo -n "Checking $name... "
    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        return 0
    else
        echo -e "${RED}FAIL${NC}"
        echo "  Run: $command"
        return 1
    fi
}

# Track failures
FAILED=0

# 1. Workflow validation
if ! run_check "workflow YAML syntax" "python dev-tools/scripts/validate_workflows.py"; then
    FAILED=1
fi

# 2. Python syntax
if ! run_check "Python syntax" "python -m py_compile src/**/*.py"; then
    FAILED=1
fi

# 3. Import validation
if ! run_check "import structure" "python dev-tools/scripts/validate_imports.py"; then
    FAILED=1
fi

# 4. CQRS validation
if ! run_check "CQRS patterns" "python dev-tools/scripts/validate_cqrs.py"; then
    FAILED=1
fi

# 5. Architecture compliance
if ! run_check "architecture rules" "python dev-tools/scripts/check_architecture.py --warn-only"; then
    FAILED=1
fi

# 6. Code formatting
if ! run_check "code formatting (black)" "black --check src/ tests/"; then
    FAILED=1
fi

# 7. Import sorting
if ! run_check "import sorting (isort)" "isort --check-only src/ tests/"; then
    FAILED=1
fi

# 8. Basic linting
if ! run_check "basic linting (flake8)" "flake8 src/ tests/"; then
    FAILED=1
fi

# 9. Security scan
if ! run_check "security scan (bandit)" "bandit -r src/ -f json -o /tmp/bandit-report.json"; then
    FAILED=1
fi

# 10. Configuration validation
if ! run_check "configuration files" "yq . .project.yml > /dev/null"; then
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
