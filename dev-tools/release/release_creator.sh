#!/bin/bash
set -e

# GitHub release creator with validation and conflict handling
# Supports: FROM_COMMIT, TO_COMMIT, ALLOW_BACKFILL, DRY_RUN env vars

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_debug() {
    if [ "${DEBUG:-false}" = "true" ] || [ "${VERBOSE:-false}" = "true" ]; then
        echo -e "${BLUE}[DEBUG]${NC} $1"
    fi
}

log_debug "Starting release_creator.sh"
log_debug "ALLOW_BACKFILL=${ALLOW_BACKFILL:-false}"
log_debug "FROM_COMMIT=${FROM_COMMIT:-unset}"
log_debug "TO_COMMIT=${TO_COMMIT:-unset}"
log_debug "DRY_RUN=${DRY_RUN:-false}"

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Check dependencies
if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: GitHub CLI (gh) is required but not installed."
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is required but not installed."
    exit 1
fi

# Get current version (force fresh read, not cached)
if [ "$DRY_RUN" = "true" ] && [ -n "$RELEASE_DRY_RUN_VERSION" ]; then
    VERSION="$RELEASE_DRY_RUN_VERSION"
else
    VERSION=$(yq '.project.version' .project.yml)
fi
TAG_NAME="v$VERSION"

echo "Creating release: $TAG_NAME"

validate_working_directory() {
    # Skip validation in dry-run mode
    if [ "$DRY_RUN" = "true" ]; then
        return 0
    fi
    
    # Check if working directory is clean (skip in backfill mode)
    if [ "${ALLOW_BACKFILL:-false}" != "true" ] && ! git diff-index --quiet HEAD --; then
        log_error "Working directory has uncommitted changes"
        echo "Commit or stash changes before creating a release"
        exit 1
    elif [ "${ALLOW_BACKFILL:-false}" = "true" ]; then
        log_debug "Skipping working directory check in backfill mode"
    fi
}

validate_branch() {
    # Check if we're on main branch (unless overridden)
    current_branch=$(git branch --show-current)
    if [ "$current_branch" != "main" ] && [ "$ALLOW_RELEASE_FROM_BRANCH" != "true" ]; then
        echo "WARNING: Creating release from branch '$current_branch' (not main)"
        echo "Use ALLOW_RELEASE_FROM_BRANCH=true to suppress this warning"
        
        # Skip confirmation in non-interactive mode, dry-run, CI, or backfill mode
        if [ "$DRY_RUN" = "true" ] || [ "$CI" = "true" ] || [ ! -t 0 ] || [ "$ALLOW_BACKFILL" = "true" ]; then
            log_info "Non-interactive mode: Proceeding with release from $current_branch"
            return 0
        fi
        
        read -p "Continue anyway? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
}

check_tag_conflicts() {
    local tag_name=$1
    
    # Check if tag already exists
    if git tag -l | grep -q "^$tag_name$"; then
        echo "ERROR: Tag '$tag_name' already exists"
        echo ""
        echo "Existing tag points to: $(git rev-parse --short "$tag_name")"
        echo "Current HEAD points to: $(git rev-parse --short HEAD)"
        echo ""
        echo "Options:"
        echo "1. Use a different version: RELEASE_VERSION=x.y.z make release-version"
        echo "2. Delete existing tag: git tag -d $tag_name && git push origin :refs/tags/$tag_name"
        echo "3. Update existing release (not supported - create new version instead)"
        exit 1
    fi
    
    # Check if GitHub release exists
    if gh release view "$tag_name" >/dev/null 2>&1; then
        echo "ERROR: GitHub release '$tag_name' already exists"
        echo "Use 'gh release view $tag_name' to see existing release"
        exit 1
    fi
}

validate_commit_range() {
    local from_commit=$1
    local to_commit=$2
    
    # Check if commits exist
    if ! git rev-parse --verify "$from_commit" >/dev/null 2>&1; then
        echo "ERROR: FROM_COMMIT '$from_commit' does not exist"
        exit 1
    fi
    
    if ! git rev-parse --verify "$to_commit" >/dev/null 2>&1; then
        echo "ERROR: TO_COMMIT '$to_commit' does not exist"
        exit 1
    fi
    
    # Check chronological order
    if ! git merge-base --is-ancestor "$from_commit" "$to_commit" 2>/dev/null; then
        echo "ERROR: FROM_COMMIT '$from_commit' is not an ancestor of TO_COMMIT '$to_commit'"
        echo "Hint: FROM_COMMIT should be older than TO_COMMIT"
        exit 1
    fi
    
    # Check for empty range
    if [ "$from_commit" = "$to_commit" ]; then
        echo "ERROR: FROM_COMMIT and TO_COMMIT are the same commit"
        exit 1
    fi
}

check_overlapping_releases() {
    local from_commit=$1
    local to_commit=$2
    
    # Skip overlap check in backfill mode
    if [ "$ALLOW_BACKFILL" = "true" ]; then
        echo "BACKFILL MODE: Skipping overlap validation"
        return 0
    fi
    
    # Get the latest release tag and its commit
    latest_tag=$(git tag -l "v*" --sort=-version:refname | head -1)
    
    if [ -n "$latest_tag" ]; then
        latest_tag_commit=$(git rev-list -n 1 "$latest_tag")
        
        # Check if FROM_COMMIT is before or at the latest release
        if git merge-base --is-ancestor "$from_commit" "$latest_tag_commit" || \
           [ "$from_commit" = "$latest_tag_commit" ]; then
            
            # Find the commit right after the latest release
            next_commit=$(git rev-list --reverse "$latest_tag_commit..HEAD" | head -1)
            
            if [ -n "$next_commit" ]; then
                echo "ERROR: FROM_COMMIT '$from_commit' overlaps with existing release $latest_tag"
                echo ""
                echo "Latest release: $latest_tag (commit: ${latest_tag_commit:0:8})"
                echo "Use this instead:"
                echo "  FROM_COMMIT=$next_commit make release-..."
                echo ""
                echo "Or use default (automatically starts after latest release):"
                echo "  make release-..."
            else
                echo "ERROR: No new commits since latest release $latest_tag"
                echo "Nothing to release!"
            fi
            exit 1
        fi
    fi
}

# Set smart defaults for commit range
set_commit_defaults() {
    if [ -z "$FROM_COMMIT" ]; then
        if [ "$ALLOW_BACKFILL" = "true" ]; then
            # Backfill mode: default to first commit
            FROM_COMMIT=$(git rev-list --max-parents=0 HEAD)
            echo "Backfill mode: Using first commit as FROM_COMMIT: ${FROM_COMMIT:0:8}"
        else
            # Normal mode: default to after last release
            latest_tag=$(git tag -l "v*" --sort=-version:refname | head -1)
            if [ -n "$latest_tag" ]; then
                FROM_COMMIT=$(git rev-list -n 1 "$latest_tag")
                echo "Using commit after latest release ($latest_tag): ${FROM_COMMIT:0:8}"
            else
                FROM_COMMIT=$(git rev-list --max-parents=0 HEAD)
                echo "No previous releases, using first commit: ${FROM_COMMIT:0:8}"
            fi
        fi
    fi
    
    TO_COMMIT=${TO_COMMIT:-HEAD}
    echo "Release range: ${FROM_COMMIT:0:8}..${TO_COMMIT:0:8}"
}

create_release() {
    local tag_name=$1
    local from_commit=$2
    local to_commit=$3
    
    # Generate release notes
    echo "Generating release notes..."
    NOTES=$(./dev-tools/release/release_notes.sh "$from_commit" "$to_commit")
    
    # Determine release flags
    RELEASE_FLAGS=""
    if [[ "$VERSION" =~ -alpha|-beta|-rc ]]; then
        RELEASE_FLAGS="--prerelease"
        echo "Creating pre-release: $tag_name"
    else
        echo "Creating stable release: $tag_name"
    fi
    
    # Create git tag
    echo "Creating git tag: $tag_name"
    git tag "$tag_name" "$to_commit"
    
    # Push tag
    echo "Pushing tag to remote..."
    git push origin "$tag_name"
    
    # Create GitHub release
    echo "Creating GitHub release..."
    gh release create "$tag_name" $RELEASE_FLAGS --notes "$NOTES"
    
    echo ""
    echo "✅ Release created successfully!"
    echo "Tag: $tag_name"
    echo "GitHub: https://github.com/$(gh repo view --json owner,name --jq '.owner.login + "/" + .name')/releases/tag/$tag_name"
}

# Main execution
echo "Release Creator - Version: $VERSION"
echo "=================================="

# Validation
validate_working_directory
validate_branch
check_tag_conflicts "$TAG_NAME"

# Set commit range defaults
set_commit_defaults

# Validate commit range
validate_commit_range "$FROM_COMMIT" "$TO_COMMIT"
check_overlapping_releases "$FROM_COMMIT" "$TO_COMMIT"

# Dry run check
if [ "$DRY_RUN" = "true" ]; then
    echo ""
    echo "DRY RUN: Would create release with:"
    echo "  Tag: $TAG_NAME"
    echo "  Range: ${FROM_COMMIT:0:8}..${TO_COMMIT:0:8}"
    echo "  Pre-release: $([[ "$VERSION" =~ -alpha|-beta|-rc ]] && echo "yes" || echo "no")"
    echo "  Backfill: ${ALLOW_BACKFILL:-false}"
    exit 0
fi

# Final confirmation
if [ "$DRY_RUN" != "true" ]; then
    echo ""
    echo "Ready to create release:"
    echo "  Tag: $TAG_NAME"
    echo "  Range: ${FROM_COMMIT:0:8}..${TO_COMMIT:0:8}"
    echo "  Pre-release: $([[ "$VERSION" =~ -alpha|-beta|-rc ]] && echo "yes" || echo "no")"
    
    # Skip confirmation in non-interactive mode, CI, or backfill mode
    if [ "$ALLOW_BACKFILL" = "true" ] || [ ! -t 0 ] || [ "$CI" = "true" ]; then
        log_info "Non-interactive mode: Creating release"
    else
        echo ""
        read -p "Create release? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "Cancelled"
            exit 1
        fi
    fi
fi

# Create the release
create_release "$TAG_NAME" "$FROM_COMMIT" "$TO_COMMIT"
