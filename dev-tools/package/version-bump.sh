#!/bin/bash
set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 <major|minor|patch|VERSION>"
    echo ""
    echo "Examples:"
    echo "  $0 patch    # 1.0.0 -> 1.0.1"
    echo "  $0 minor    # 1.0.0 -> 1.1.0"
    echo "  $0 major    # 1.0.0 -> 2.0.0"
    echo "  $0 1.2.3    # Set specific version"
    exit 1
fi

BUMP_TYPE=$1

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Check if yq is available
if ! command -v yq >/dev/null 2>&1; then
    echo "ERROR: yq is required but not installed."
    echo "Install with: brew install yq (macOS) or apt install yq (Ubuntu)"
    exit 1
fi

# Check if .project.yml exists
if [ ! -f .project.yml ]; then
    echo "ERROR: .project.yml not found in project root"
    exit 1
fi

# Get current version from project config
CURRENT_VERSION=$(yq '.project.version' .project.yml)
echo "Current version: $CURRENT_VERSION"

# Calculate new version
if [[ "$BUMP_TYPE" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    # Specific version provided
    NEW_VERSION=$BUMP_TYPE
else
    # Parse current version
    IFS='.' read -ra VERSION_PARTS <<< "$CURRENT_VERSION"
    MAJOR=${VERSION_PARTS[0]}
    MINOR=${VERSION_PARTS[1]}
    PATCH=${VERSION_PARTS[2]}
    
    case $BUMP_TYPE in
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
            echo "ERROR: Invalid bump type: $BUMP_TYPE"
            echo "Use: major, minor, patch, or specific version (e.g., 1.2.3)"
            exit 1
            ;;
    esac
    
    NEW_VERSION="$MAJOR.$MINOR.$PATCH"
fi

echo "New version: $NEW_VERSION"
echo ""
read -p "Update version to $NEW_VERSION? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "ERROR: Cancelled"
    exit 1
fi

# Update version in .project.yml (single source of truth)
echo "Updating .project.yml..."
yq -i ".project.version = \"$NEW_VERSION\"" .project.yml

# Regenerate pyproject.toml from template with new version
if [ -f pyproject.toml.template ]; then
    echo "Regenerating pyproject.toml from template..."
    make generate-pyproject
fi

echo "Version updated to $NEW_VERSION"
echo ""
echo "Next steps:"
echo "  Review changes: git diff"
echo "  Commit changes: git add -A && git commit -m 'bump: version $CURRENT_VERSION -> $NEW_VERSION'"
echo "  Create tag: git tag v$NEW_VERSION"
echo "  Push changes: git push && git push --tags"
echo "  Build and publish: make build && make publish"
