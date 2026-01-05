#!/bin/bash
# dev-tools/release/publisher.sh
# Publisher for all release artifacts
set -e

echo "=== RELEASE PUBLISHER ==="
echo "Publishing all release artifacts..."

# Build and push containers
if [ "${PUBLISH_CONTAINERS:-true}" = "true" ]; then
    echo "=== PUBLISHING CONTAINERS ==="
    echo "Building and pushing containers..."
    make container-build
    make container-push-multi
    echo "Container publishing complete"
else
    echo "Skipping container publishing (PUBLISH_CONTAINERS not set to true)"
fi

# Deploy documentation
if [ "${PUBLISH_DOCS:-true}" = "true" ]; then
    echo "=== PUBLISHING DOCUMENTATION ==="
    echo "Deploying documentation..."
    make docs-build
    make docs-deploy
    echo "Documentation publishing complete"
else
    echo "Skipping documentation publishing (PUBLISH_DOCS not set to true)"
fi

echo "=== RELEASE PUBLISHER COMPLETE ==="
echo "Note: PyPI publishing is handled by GitHub Actions on release events"
echo "To enable publishing:"
echo "  PUBLISH_CONTAINERS=true PUBLISH_DOCS=true ./publisher.sh"
