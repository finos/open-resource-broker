#!/bin/bash
set -e

# Release backfill script - creates historical releases
# Usage: RELEASE_VERSION=1.2.3 TO_COMMIT=abc ./release_backfill.sh

RELEASE_VERSION="$1"
TO_COMMIT="$2"
DRY_RUN="${DRY_RUN:-false}"

if [ -z "$RELEASE_VERSION" ] || [ -z "$TO_COMMIT" ]; then
    echo "Usage: $0 <version> <commit>"
    echo "Example: $0 0.0.1rc0 abc123"
    echo "Environment: DRY_RUN=true (optional)"
    exit 1
fi

if [ "$DRY_RUN" = "true" ]; then
    echo "DRY RUN: Would create release $RELEASE_VERSION from $TO_COMMIT"
    exit 0
fi

# Build historical package (keep artifacts)
KEEP_ARTIFACTS=true ./dev-tools/release/build_historical.sh "$TO_COMMIT" "$RELEASE_VERSION"

# Generate enhanced release notes using our release notes script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
RELEASE_NOTES=$("$SCRIPT_DIR/release_notes.sh" "$TO_COMMIT" "$TO_COMMIT" "$RELEASE_VERSION")

# Create GitHub release
if [ -f "dist/open_resource_broker-${RELEASE_VERSION}-py3-none-any.whl" ]; then
    gh release create "v${RELEASE_VERSION}" \
        --title "v${RELEASE_VERSION}" \
        --notes "$RELEASE_NOTES" \
        --target "$TO_COMMIT" \
        --prerelease \
        "dist/open_resource_broker-${RELEASE_VERSION}-py3-none-any.whl"
    echo "Release v${RELEASE_VERSION} created"
else
    echo "ERROR: Package not found: dist/open_resource_broker-${RELEASE_VERSION}-py3-none-any.whl"
    exit 1
fi

# Cleanup
rm -rf dist/ build/ ./*.egg-info/
echo "Cleaned up build artifacts"
