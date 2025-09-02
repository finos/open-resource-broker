#!/bin/bash
set -e

# Release deletion script with changelog update
# Usage: delete_release.sh <version>

VERSION="$1"
DRY_RUN="${DRY_RUN:-false}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

if [ -z "$VERSION" ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 v1.2.3"
    exit 1
fi

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Validate version exists
if ! git tag -l | grep -q "^${VERSION}$"; then
    log_error "Tag $VERSION does not exist"
    exit 1
fi

# Check if GitHub release exists
if command -v gh >/dev/null 2>&1; then
    if gh release view "$VERSION" >/dev/null 2>&1; then
        GITHUB_RELEASE_EXISTS=true
    else
        GITHUB_RELEASE_EXISTS=false
    fi
else
    log_warn "GitHub CLI not available, skipping GitHub release deletion"
    GITHUB_RELEASE_EXISTS=false
fi

# Show what will be deleted
echo ""
echo "Will delete:"
echo "  Git tag: $VERSION"
if [ "$GITHUB_RELEASE_EXISTS" = "true" ]; then
    echo "  GitHub release: $VERSION"
fi
echo "  Changelog entry: $VERSION"
echo ""

# Confirmation
if [ "$DRY_RUN" = "true" ]; then
    log_info "DRY RUN: Would delete release $VERSION"
    exit 0
fi

if [ -t 0 ] && [ "$CI" != "true" ]; then
    read -p "Are you sure you want to delete release $VERSION? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 1
    fi
fi

# Delete GitHub release
if [ "$GITHUB_RELEASE_EXISTS" = "true" ]; then
    log_info "Deleting GitHub release $VERSION..."
    gh release delete "$VERSION" --yes
fi

# Delete git tag (local and remote)
log_info "Deleting git tag $VERSION..."
git tag -d "$VERSION"

if git ls-remote --tags origin | grep -q "refs/tags/${VERSION}$"; then
    log_info "Deleting remote tag $VERSION..."
    git push origin ":refs/tags/$VERSION"
fi

# Update changelog
log_info "Updating changelog..."
python3 dev-tools/release/changelog_manager.py delete "$VERSION"

# Commit changelog changes
if git diff --quiet CHANGELOG.md; then
    log_info "No changelog changes to commit"
else
    log_info "Committing changelog changes..."
    git add CHANGELOG.md
    git commit -m "docs: remove $VERSION from changelog"
fi

log_info "Release $VERSION deleted successfully"
echo ""
echo "Summary:"
echo "  Git tag deleted (local and remote)"
if [ "$GITHUB_RELEASE_EXISTS" = "true" ]; then
    echo "  GitHub release deleted"
fi
echo "  Changelog updated"
echo "  Changes committed"
