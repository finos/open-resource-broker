#!/bin/bash
# dev-tools/release/orchestrator.sh
# Release orchestrator for all release types
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== RELEASE ORCHESTRATOR ==="

# Determine release mode
RELEASE_MODE="${RELEASE_MODE:-forward}"
echo "Release mode: $RELEASE_MODE"

case "$RELEASE_MODE" in
    "forward")
        echo "=== FORWARD RELEASE ==="
        echo "Creating new release with semantic versioning..."
        
        # Build and test
        echo "Building and testing..."
        cd "$PROJECT_ROOT"
        make build
        make test-fast
        
        # Publish everything
        echo "Publishing release artifacts..."
        ./dev-tools/release/publisher.sh
        
        echo "Forward release complete"
        ;;
        
    "historical")
        echo "=== HISTORICAL RELEASE ==="
        if [ -z "$COMMIT" ] || [ -z "$VERSION" ]; then
            echo "ERROR: COMMIT and VERSION required for historical releases"
            echo "Usage: RELEASE_MODE=historical COMMIT=abc123 VERSION=1.0.0 $0"
            exit 1
        fi
        
        echo "Creating historical release for commit $COMMIT with version $VERSION"
        ./dev-tools/release/build_historical.sh "$COMMIT" "$VERSION"
        ;;
        
    "analysis")
        echo "=== RC READINESS ANALYSIS ==="
        echo "Analyzing RC readiness..."
        ./dev-tools/release/analyze_rc_readiness.sh
        ;;
        
    *)
        echo "ERROR: Unknown release mode: $RELEASE_MODE"
        echo "Supported modes: forward, historical, analysis"
        exit 1
        ;;
esac

echo "=== RELEASE ORCHESTRATOR COMPLETE ==="
