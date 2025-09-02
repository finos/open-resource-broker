#!/bin/bash
set -e

# Changelog setup script
# Sets up complete changelog automation system

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

echo "Setting up changelog automation system..."
echo ""

# Step 1: Install git-changelog
log_step "1. Installing git-changelog"
if command -v uv >/dev/null 2>&1; then
    uv pip install git-changelog
else
    pip install git-changelog
fi
log_info "git-changelog installed"

# Step 2: Verify configuration files exist
log_step "2. Verifying configuration files"

if [ ! -f dev-tools/release/simple_changelog.py ]; then
    log_warn "Missing simple_changelog.py - should have been created"
    exit 1
fi
log_info "Changelog generator exists: dev-tools/release/simple_changelog.py"

if [ ! -f dev-tools/release/changelog_manager.py ]; then
    log_warn "Missing changelog_manager.py - should have been created"
    exit 1
fi
log_info "Changelog manager exists: dev-tools/release/changelog_manager.py"

# Step 3: Make scripts executable
log_step "3. Setting up scripts"
chmod +x dev-tools/release/changelog_manager.py
chmod +x dev-tools/release/simple_changelog.py
chmod +x dev-tools/release/delete_release.sh
log_info "Scripts made executable"

# Step 4: Generate initial changelog if it doesn't exist
log_step "4. Setting up initial changelog"
if [ ! -f CHANGELOG.md ]; then
    log_info "Generating initial changelog..."
    python3 dev-tools/release/changelog_manager.py generate
    log_info "Initial changelog generated"
else
    log_info "CHANGELOG.md already exists"
    
    # Ask if user wants to regenerate
    if [ -t 0 ]; then
        read -p "Regenerate changelog from git history? (y/N): " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            log_info "Regenerating changelog..."
            python3 dev-tools/release/changelog_manager.py generate
            log_info "Changelog regenerated"
        fi
    fi
fi

# Step 5: Validate setup
log_step "5. Validating setup"
python3 dev-tools/release/changelog_manager.py validate
log_info "Changelog validation passed"

# Step 6: Test preview functionality
log_step "6. Testing preview functionality"
log_info "Preview of recent changes:"
echo "----------------------------------------"
python3 dev-tools/release/changelog_manager.py preview || log_warn "No recent changes to preview"
echo "----------------------------------------"

# Step 7: Show available commands
log_step "7. Available commands"
echo ""
echo "Changelog management commands:"
echo "  make changelog-generate      # Generate full changelog"
echo "  make changelog-update        # Update for release"
echo "  make changelog-preview       # Preview changes"
echo "  make changelog-validate      # Validate format"
echo "  make changelog-regenerate    # Regenerate and commit"
echo "  make changelog-status        # Show status"
echo ""
echo "Release commands (with changelog):"
echo "  make release-minor           # Minor release with changelog"
echo "  make release-patch           # Patch release with changelog"
echo "  make release-delete VERSION=v1.2.3  # Delete release"
echo ""
echo "Advanced commands:"
echo "  make release-backfill-with-changelog  # Backfill with changelog"
echo "  make changelog-regenerate             # Fix sync issues"
echo ""

# Step 8: Final status
log_step "8. Setup complete"
echo ""
echo "Python environment verified"
echo "Scripts configured and executable"
echo "Changelog initialized"
echo "Validation passed"
echo ""
log_info "Changelog automation system is ready!"
echo ""
echo "Next steps:"
echo "1. Review generated CHANGELOG.md"
echo "2. Test with: make changelog-preview"
echo "3. Integrate with releases: make release-minor"
echo "4. Read docs: docs/root/developer_guide/changelog_management.md"
