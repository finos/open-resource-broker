#!/bin/bash
set -e

# Update release notes for existing release (preserving manual edits)
# Usage: update_release_notes.sh <version> [from_commit] <to_commit>

if [ $# -lt 2 ]; then
    echo "Usage: $0 <version> [from_commit] <to_commit>"
    echo "Example: $0 0.0.1a1 abc123 def456"
    echo "Example: $0 0.0.1a1 def456  # Single commit release"
    exit 1
fi

VERSION="$1"
if [ $# -eq 3 ]; then
    FROM_COMMIT="$2"
    TO_COMMIT="$3"
else
    # Single commit release
    FROM_COMMIT="$2"
    TO_COMMIT="$2"
fi

TAG_NAME="v$VERSION"

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

echo "Updating release notes for $TAG_NAME (preserving manual edits)..."

# Check if release exists
if ! gh release view "$TAG_NAME" >/dev/null 2>&1; then
    echo "ERROR: Release $TAG_NAME does not exist"
    exit 1
fi

# Get current release notes
CURRENT_NOTES=$(gh release view "$TAG_NAME" --json body --jq '.body')

# Generate new structured sections
NEW_NOTES=$("$SCRIPT_DIR/release_notes.sh" "$FROM_COMMIT" "$TO_COMMIT" "$VERSION")

# Extract sections from new notes
WHATS_CHANGED=$(echo "$NEW_NOTES" | sed -n '/## What'\''s Changed/,/### Contributors/p' | sed '$d')
CONTRIBUTORS=$(echo "$NEW_NOTES" | sed -n '/### Contributors/,/\*\*Changelog\*\*/p' | sed '$d')
CHANGELOG=$(echo "$NEW_NOTES" | grep '^\*\*Changelog\*\*:')

# Preserve manual content by removing our managed sections from current notes
MANUAL_CONTENT=$(echo "$CURRENT_NOTES" | sed '/## What'\''s Changed/,$d')

# Combine manual content with updated structured sections
UPDATED_NOTES="$MANUAL_CONTENT"
[ -n "$MANUAL_CONTENT" ] && UPDATED_NOTES="$UPDATED_NOTES

"
UPDATED_NOTES="$UPDATED_NOTES$WHATS_CHANGED

$CONTRIBUTORS

$CHANGELOG"

# Update the release notes
gh release edit "$TAG_NAME" --notes "$UPDATED_NOTES"

echo "Release notes updated for $TAG_NAME (manual edits preserved)"
