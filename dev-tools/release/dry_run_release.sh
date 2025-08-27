#!/bin/bash
set -e

# Dry-run release script that simulates the entire process
# Usage: dry_run_release.sh <bump|set> <patch|minor|major|VERSION> [prerelease_type]

if [ $# -lt 2 ]; then
    echo "Usage: $0 <bump|set> <patch|minor|major|VERSION> [alpha|beta|rc]"
    exit 1
fi

COMMAND=$1
VERSION_ARG=$2
PRERELEASE_TYPE=$3

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Get current version
CURRENT_VERSION=$(yq '.project.version' .project.yml)
echo "Current version: $CURRENT_VERSION"

# Calculate what the new version would be
if [ "$COMMAND" = "set" ]; then
    NEW_VERSION=$VERSION_ARG
elif [ "$COMMAND" = "bump" ]; then
    # Parse current version
    if [[ "$CURRENT_VERSION" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)(-([a-z]+)\.([0-9]+))?$ ]]; then
        MAJOR=${BASH_REMATCH[1]}
        MINOR=${BASH_REMATCH[2]}
        PATCH=${BASH_REMATCH[3]}
    else
        echo "ERROR: Invalid version format: $CURRENT_VERSION"
        exit 1
    fi
    
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
            exit 1
            ;;
    esac
    
    NEW_VERSION="$MAJOR.$MINOR.$PATCH"
    
    # Add pre-release suffix if specified
    if [ -n "$PRERELEASE_TYPE" ]; then
        NEW_VERSION="$NEW_VERSION-$PRERELEASE_TYPE.1"
    fi
fi

echo "New version: $NEW_VERSION"
echo "DRY RUN: Would update .project.yml version to $NEW_VERSION"

# Simulate release creation
TAG_NAME="v$NEW_VERSION"
echo ""
echo "Creating release: $TAG_NAME"
echo "Release Creator - Version: $NEW_VERSION"
echo "=================================="

# Check branch
current_branch=$(git branch --show-current)
if [ "$current_branch" != "main" ]; then
    echo "WARNING: Creating release from branch '$current_branch' (not main)"
    echo "Non-interactive mode: Proceeding with release from $current_branch"
fi

# Set commit range
FROM_COMMIT=$(git rev-list --max-parents=0 HEAD)
TO_COMMIT="HEAD"
echo "No previous releases, using first commit: ${FROM_COMMIT:0:8}"
echo "Release range: ${FROM_COMMIT:0:8}..${TO_COMMIT:0:8}"

# Show what would be created
echo ""
echo "DRY RUN: Would create release with:"
echo "  Tag: $TAG_NAME"
echo "  Range: ${FROM_COMMIT:0:8}..${TO_COMMIT:0:8}"
echo "  Pre-release: $([[ "$NEW_VERSION" =~ -alpha|-beta|-rc ]] && echo "yes" || echo "no")"
echo "  Backfill: ${ALLOW_BACKFILL:-false}"
