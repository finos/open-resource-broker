#!/bin/bash
set -e

# Pre-release promotion manager
# Usage: promote_manager.sh <alpha|beta|rc|stable>

# Colors for output
GREEN='\033[0;32m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

if [ $# -ne 1 ]; then
    echo "Usage: $0 <alpha|beta|rc|stable>"
    echo ""
    echo "Examples:"
    echo "  $0 alpha    # 1.0.0a1 -> 1.0.0a2 OR 1.0.0rc0 -> 1.0.1a1"
    echo "  $0 beta     # 1.0.0a2 -> 1.0.0b1"
    echo "  $0 rc       # 1.0.0b1 -> 1.0.0rc1"
    echo "  $0 stable   # 1.0.0rc1 -> 1.0.0"
    exit 1
fi

PROMOTE_TO=$1

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Check dependencies
if ! command -v yq >/dev/null 2>&1; then
    echo "ERROR: yq is required but not installed."
    exit 1
fi

if [ ! -f .project.yml ]; then
    echo "ERROR: .project.yml not found in project root"
    exit 1
fi

# Get current version
CURRENT_VERSION=$(yq '.project.version' .project.yml)
echo "Current version: $CURRENT_VERSION"

parse_version() {
    local version=$1
    # PEP 440 format: 1.0.0, 1.0.0a1, 1.0.0b1, 1.0.0rc1
    if [[ "$version" =~ ^([0-9]+\.[0-9]+\.[0-9]+)([abc]|rc)?([0-9]+)?$ ]]; then
        BASE_VERSION=${BASH_REMATCH[1]}
        CURRENT_PRERELEASE=${BASH_REMATCH[2]}
        CURRENT_NUMBER=${BASH_REMATCH[3]}
    else
        echo "ERROR: Invalid version format: $version"
        echo "Expected PEP 440 format: 1.0.0, 1.0.0a1, 1.0.0b1, 1.0.0rc1"
        exit 1
    fi
}

# Parse current version
parse_version "$CURRENT_VERSION"

# Determine new version based on promotion target
case $PROMOTE_TO in
    alpha)
        if [ "$CURRENT_PRERELEASE" = "a" ]; then
            # Increment alpha number
            NEW_NUMBER=$((CURRENT_NUMBER + 1))
            NEW_VERSION="${BASE_VERSION}a${NEW_NUMBER}"
        elif [ "$CURRENT_PRERELEASE" = "rc" ] || [ -z "$CURRENT_PRERELEASE" ]; then
            # Start new alpha cycle from RC or stable
            # Increment patch version and start alpha.1
            IFS='.' read -r MAJOR MINOR PATCH <<< "$BASE_VERSION"
            NEW_PATCH=$((PATCH + 1))
            NEW_BASE_VERSION="${MAJOR}.${MINOR}.${NEW_PATCH}"
            NEW_VERSION="${NEW_BASE_VERSION}a1"
            echo "Starting new alpha cycle: $CURRENT_VERSION -> $NEW_VERSION"
        else
            echo "ERROR: Cannot promote to alpha from $CURRENT_VERSION"
            echo "Alpha promotion works from existing alpha, RC, or stable versions"
            exit 1
        fi
        ;;
    beta)
        if [ "$CURRENT_PRERELEASE" = "a" ]; then
            # Promote alpha to beta.1
            NEW_VERSION="${BASE_VERSION}b1"
        elif [ "$CURRENT_PRERELEASE" = "b" ]; then
            # Increment beta number
            NEW_NUMBER=$((CURRENT_NUMBER + 1))
            NEW_VERSION="${BASE_VERSION}b${NEW_NUMBER}"
        else
            echo "ERROR: Cannot promote to beta from $CURRENT_VERSION"
            echo "Beta promotion works from alpha or existing beta versions"
            exit 1
        fi
        ;;
    rc)
        if [ "$CURRENT_PRERELEASE" = "b" ]; then
            # Promote beta to rc1
            NEW_VERSION="${BASE_VERSION}rc1"
        elif [ "$CURRENT_PRERELEASE" = "rc" ]; then
            # Increment rc number
            NEW_NUMBER=$((CURRENT_NUMBER + 1))
            NEW_VERSION="${BASE_VERSION}rc${NEW_NUMBER}"
        else
            echo "ERROR: Cannot promote to rc from $CURRENT_VERSION"
            echo "RC promotion works from beta or existing rc versions"
            exit 1
        fi
        ;;
    stable)
        if [[ "$CURRENT_PRERELEASE" =~ ^(a|b|rc)$ ]]; then
            # Promote any pre-release to stable
            NEW_VERSION="$BASE_VERSION"
        else
            echo "ERROR: Cannot promote to stable from $CURRENT_VERSION"
            echo "Stable promotion works from pre-release versions (alpha/beta/rc)"
            exit 1
        fi
        ;;
    *)
        echo "ERROR: Invalid promotion target: $PROMOTE_TO"
        echo "Use: alpha, beta, rc, or stable"
        exit 1
        ;;
esac

echo "Promoting to: $NEW_VERSION"

# Dry run check
if [ "$DRY_RUN" = "true" ]; then
    echo "DRY RUN: Would promote $CURRENT_VERSION -> $NEW_VERSION"
    exit 0
fi

# Skip confirmation in non-interactive mode or CI
if [ -t 0 ] && [ "$CI" != "true" ]; then
    # Interactive mode - ask for confirmation
    echo ""
    read -p "Promote version to $NEW_VERSION? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 1
    fi
else
    # Non-interactive mode - proceed automatically
    echo "Non-interactive mode: Promoting to $NEW_VERSION"
fi

# Version will be updated via changelog PR instead of direct commit
log_info "Version promotion prepared: $CURRENT_VERSION -> $NEW_VERSION"
log_info "Version update will be handled by changelog PR during release creation"

# For stable promotion, consolidate release notes from pre-releases
if [ "$PROMOTE_TO" = "stable" ]; then
    echo ""
    echo "Note: Stable promotion will consolidate notes from all pre-releases"
    echo "This includes all changes from alpha, beta, and rc versions of $BASE_VERSION"
fi

# Export the new version for use by release_creator.sh
export PROMOTED_VERSION="$NEW_VERSION"
echo "PROMOTED_VERSION=$NEW_VERSION" >> "$GITHUB_ENV" 2>/dev/null || true
