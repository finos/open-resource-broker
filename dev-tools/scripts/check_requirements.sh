#!/bin/bash

# Exit on error
set -e

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_section() {
    echo -e "\n${BLUE}=== $1 ===${NC}\n"
}

# Check if virtual environment exists
if [ -d "$PROJECT_ROOT/.venv" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# Install safety if not already installed
if ! command -v safety &> /dev/null; then
    log_info "Installing safety..."
    pip install safety
fi

# Install pip-audit if not already installed
if ! command -v pip-audit &> /dev/null; then
    log_info "Installing pip-audit..."
    if command -v uv &> /dev/null; then
        uv tool install pip-audit
    else
        pip install --user pip-audit
    fi
fi

# Check all requirements files
REQUIREMENTS_FILES=(
    "requirements.txt"
    "requirements-dev.txt"
)

EXIT_CODE=0

for req_file in "${REQUIREMENTS_FILES[@]}"; do
    if [ -f "$PROJECT_ROOT/$req_file" ]; then
        log_section "Checking $req_file"

        # Check for security vulnerabilities using safety
        log_info "Running safety check..."
        if ! safety check -r "$PROJECT_ROOT/$req_file"; then
            log_error "Security vulnerabilities found in $req_file"
            EXIT_CODE=1
        fi

        # Check for security vulnerabilities using pip-audit
        log_info "Running pip-audit check..."
        if ! pip-audit -r "$PROJECT_ROOT/$req_file"; then
            log_error "Security vulnerabilities found in $req_file by pip-audit"
            EXIT_CODE=1
        fi

        # Check for outdated packages
        log_info "Checking for outdated packages..."
        pip list --outdated --format=json | python -c '
import sys, json
pkgs = json.load(sys.stdin)
for pkg in pkgs:
    print(f"{pkg["name"]}: {pkg["version"]} -> {pkg["latest_version"]}")
'

        # Validate requirements format
        log_info "Validating requirements format..."
        if ! pip check -r "$PROJECT_ROOT/$req_file"; then
            log_error "Invalid requirements format in $req_file"
            EXIT_CODE=1
        fi

        # Check for duplicate requirements
        log_info "Checking for duplicate requirements..."
        DUPLICATES=$(sort "$PROJECT_ROOT/$req_file" | uniq -d)
        if [ ! -z "$DUPLICATES" ]; then
            log_error "Duplicate requirements found in $req_file:"
            echo "$DUPLICATES"
            EXIT_CODE=1
        fi

        # Check for conflicts
        log_info "Checking for dependency conflicts..."
        if ! pip check; then
            log_error "Dependency conflicts found"
            EXIT_CODE=1
        fi

        # Generate requirements.txt from setup.py if it exists
        if [ -f "$PROJECT_ROOT/setup.py" ]; then
            log_info "Checking setup.py dependencies..."
            TMP_REQ=$(mktemp)
            pip-compile "$PROJECT_ROOT/setup.py" --output-file "$TMP_REQ"
            SETUP_DEPS=$(cat "$TMP_REQ")
            FILE_DEPS=$(cat "$PROJECT_ROOT/$req_file")

            # Compare dependencies
            if [ "$SETUP_DEPS" != "$FILE_DEPS" ]; then
                log_warn "Dependencies in $req_file differ from setup.py"
                log_info "Consider updating $req_file using:"
                echo "pip-compile setup.py --output-file $req_file"
            fi
            rm "$TMP_REQ"
        fi
    else
        log_warn "File $req_file not found"
    fi
done

# Generate dependency graph
log_section "Generating dependency graph"
if command -v pipdeptree &> /dev/null; then
    pipdeptree --graph-output png > "$PROJECT_ROOT/dependency-graph.png"
    log_info "Dependency graph saved to dependency-graph.png"
else
    log_warn "pipdeptree not installed. Install with: pip install pipdeptree"
fi

# Final summary
if [ $EXIT_CODE -eq 0 ]; then
    log_section "All checks passed successfully!"
else
    log_section "Some checks failed. Please review the output above."
fi

# Cleanup
if [ -d "$PROJECT_ROOT/.venv" ]; then
    deactivate
fi

exit $EXIT_CODE
