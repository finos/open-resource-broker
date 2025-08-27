#!/bin/bash
set -e

# Enhanced version manager with pre-release support
# Usage: version_manager.sh <bump|set> <patch|minor|major|VERSION> [prerelease_type]

if [ $# -lt 2 ]; then
    echo "Usage: $0 <bump|set> <patch|minor|major|VERSION> [alpha|beta|rc]"
    echo ""
    echo "Examples:"
    echo "  $0 bump patch           # 1.0.0 -> 1.0.1"
    echo "  $0 bump minor alpha     # 1.0.0 -> 1.1.0-alpha.1"
    echo "  $0 set 1.2.3            # Set specific version"
    echo "  $0 set 1.2.3-beta.1     # Set specific pre-release"
    exit 1
fi

COMMAND=$1
VERSION_ARG=$2
PRERELEASE_TYPE=$3

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
        PRERELEASE=${BASH_REMATCH[5]}
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

# Skip confirmation in non-interactive mode or CI
if [ -t 0 ] && [ "$CI" != "true" ]; then
    # Interactive mode - ask for confirmation
    echo ""
    read -p "Update version to $NEW_VERSION? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Cancelled"
        exit 1
    fi
else
    # Non-interactive mode - proceed automatically
    echo "Non-interactive mode: Updating version to $NEW_VERSION"
fi

# Update .project.yml
yq -i ".project.version = \"$NEW_VERSION\"" .project.yml
echo "Updated .project.yml with version $NEW_VERSION"
