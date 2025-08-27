#!/bin/bash
set -e

# Enhanced version manager with pre-release support
# Usage: version_manager.sh <bump|set> <patch|minor|major|VERSION> [prerelease_type]

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

# Parse arguments for force flag
FORCE_FLAG=false
ARGS=()
for arg in "$@"; do
    case $arg in
        --force|--yes|-y)
            FORCE_FLAG=true
            ;;
        *)
            ARGS+=("$arg")
            ;;
    esac
done

log_debug "Starting version_manager.sh with args: ${ARGS[*]}"
log_debug "ALLOW_BACKFILL=${ALLOW_BACKFILL:-false}"
log_debug "FORCE_FLAG=${FORCE_FLAG}"

if [ ${#ARGS[@]} -lt 2 ]; then
    echo "Usage: $0 <bump|set> <patch|minor|major|VERSION> [alpha|beta|rc] [--force]"
    echo ""
    echo "Examples:"
    echo "  $0 bump patch           # 1.0.0 -> 1.0.1"
    echo "  $0 bump minor alpha     # 1.0.0 -> 1.1.0-alpha.1"
    echo "  $0 set 1.2.3            # Set specific version"
    echo "  $0 set 1.2.3-beta.1     # Set specific pre-release"
    echo "  $0 set 1.2.3 --force    # Set version without confirmation"
    exit 1
fi

COMMAND=${ARGS[0]}
VERSION_ARG=${ARGS[1]}
PRERELEASE_TYPE=${ARGS[2]}

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Check dependencies
if ! command -v yq >/dev/null 2>&1; then
    echo "ERROR: yq is required but not installed."
    echo "Install with: brew install yq (macOS) or apt install yq (Ubuntu)"
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
    if [[ "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-([a-z]+)\.([0-9]+))?$ ]]; then
        MAJOR=${BASH_REMATCH[1]}
        MINOR=${BASH_REMATCH[2]}
        PATCH=${BASH_REMATCH[3]}
        # These variables are used for future pre-release functionality
        # shellcheck disable=SC2034
        PRERELEASE=${BASH_REMATCH[5]}
        # shellcheck disable=SC2034
        PRERELEASE_NUM=${BASH_REMATCH[6]}
    else
        echo "ERROR: Invalid version format: $version"
        exit 1
    fi
}

if [ "$COMMAND" = "set" ]; then
    # Set specific version
    NEW_VERSION=$VERSION_ARG
    echo "Setting version to: $NEW_VERSION"
    
elif [ "$COMMAND" = "bump" ]; then
    # Parse current version
    parse_version "$CURRENT_VERSION"
    
    # Apply version bump
    case $VERSION_ARG in
        major)
            MAJOR=$((MAJOR + 1))
            MINOR=0
            PATCH=0
            ;;
        minor)
            MINOR=$((MINOR + 1))
            PATCH=0
            ;;
        patch)
            PATCH=$((PATCH + 1))
            ;;
        *)
            echo "ERROR: Invalid bump type: $VERSION_ARG"
            echo "Use: major, minor, or patch"
            exit 1
            ;;
    esac
    
    # Build new version
    NEW_VERSION="$MAJOR.$MINOR.$PATCH"
    
    # Add pre-release suffix if specified
    if [ -n "$PRERELEASE_TYPE" ]; then
        NEW_VERSION="$NEW_VERSION-$PRERELEASE_TYPE.1"
    fi
    
else
    echo "ERROR: Invalid command: $COMMAND"
    echo "Use: bump or set"
    exit 1
fi

echo "New version: $NEW_VERSION"

# Dry run check
if [ "$DRY_RUN" = "true" ]; then
    echo "DRY RUN: Would update .project.yml version to $NEW_VERSION"
    # In dry-run, temporarily update version for downstream scripts
    export RELEASE_DRY_RUN_VERSION="$NEW_VERSION"
    exit 0
fi

# Skip confirmation in non-interactive mode, CI, force flag, or backfill mode
if [ "$FORCE_FLAG" = "true" ] || [ "$ALLOW_BACKFILL" = "true" ] || [ ! -t 0 ] || [ "$CI" = "true" ]; then
    # Non-interactive mode - proceed automatically
    log_info "Updating version to $NEW_VERSION (non-interactive mode)"
else
    # Interactive mode - ask for confirmation
    echo ""
    read -p "Update version to $NEW_VERSION? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 1
    fi
fi

# Update .project.yml
yq -i ".project.version = \"$NEW_VERSION\"" .project.yml
echo "Updated .project.yml with version $NEW_VERSION"
