#!/bin/bash
set -e

# Historical Build Script
# Builds packages for historical commits using overlay approach

COMMIT_HASH="$1"
VERSION="$2"
DRY_RUN="${DRY_RUN:-false}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

if [ -z "$COMMIT_HASH" ] || [ -z "$VERSION" ]; then
    echo "Usage: $0 <commit-hash> <version>"
    echo ""
    echo "Examples:"
    echo "  $0 abc123 0.0.1"
    echo "  DRY_RUN=true $0 abc123 0.0.1"
    exit 1
fi

log_info "Building historical package $VERSION from commit $COMMIT_HASH"

if [ "$DRY_RUN" = "true" ]; then
    log_warn "[DRY RUN] Would build package $VERSION from $COMMIT_HASH"
    exit 0
fi

# Verify commit exists
if ! git rev-parse --verify "$COMMIT_HASH" >/dev/null 2>&1; then
    log_error "Commit $COMMIT_HASH does not exist"
    exit 1
fi

# Store current state
CURRENT_BRANCH=$(git branch --show-current)
TEMP_BRANCH="historical-build-$VERSION-$(date +%s)"

log_info "Creating temporary branch: $TEMP_BRANCH"
git checkout -b "$TEMP_BRANCH" "$COMMIT_HASH" -q

# Overlay approach: Create modern pyproject.toml with historical source
log_info "Applying overlay build system with version $VERSION"

# Create modern pyproject.toml
cat > pyproject.toml << EOF
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "open-hostfactory-plugin"
version = "$VERSION"
description = "Cloud provider integration plugin for IBM Spectrum Symphony Host Factory"
authors = [{name = "AWS Professional Services", email = "aws-proserve@amazon.com"}]
license = {text = "Apache-2.0"}
readme = "README.md"
requires-python = ">=3.9"
dependencies = []

[project.urls]
Homepage = "https://github.com/awslabs/open-hostfactory-plugin"
Repository = "https://github.com/awslabs/open-hostfactory-plugin"

[tool.setuptools.packages.find]
where = ["."]
include = ["src*"]
EOF

# Remove any conflicting setup.py (overlay approach always uses modern pyproject.toml)
if [ -f "setup.py" ]; then
    log_info "Removing setup.py (using modern pyproject.toml)"
    rm -f setup.py
fi

# Build package
log_info "Building package with overlay approach"
python -m pip install build --quiet 2>/dev/null || true
python -m build --wheel --outdir dist/ || {
    log_error "Package build failed"
    git checkout "$CURRENT_BRANCH" -q
    git branch -D "$TEMP_BRANCH" -q 2>/dev/null || true
    exit 1
}

# Cleanup
if [ "${KEEP_ARTIFACTS:-false}" = "true" ]; then
    log_info "Keeping build artifacts (KEEP_ARTIFACTS=true)"
    # Move wheel to temp location before cleanup
    if [ -f "dist/open_hostfactory_plugin-${VERSION}-py3-none-any.whl" ]; then
        mv "dist/open_hostfactory_plugin-${VERSION}-py3-none-any.whl" "/tmp/wheel-${VERSION}.whl"
    fi
    git clean -fd  # Remove untracked files including pyproject.toml
    git reset --hard HEAD  # Reset any tracked file changes
    git checkout "$CURRENT_BRANCH" -q
    git branch -D "$TEMP_BRANCH" -q
    # Restore wheel
    mkdir -p dist
    if [ -f "/tmp/wheel-${VERSION}.whl" ]; then
        mv "/tmp/wheel-${VERSION}.whl" "dist/open_hostfactory_plugin-${VERSION}-py3-none-any.whl"
    fi
else
    log_info "Cleaning up temporary branch"
    git clean -fd  # Remove untracked files
    git reset --hard HEAD  # Reset tracked files
    git checkout "$CURRENT_BRANCH" -q
    git branch -D "$TEMP_BRANCH" -q
fi

log_info "Historical package $VERSION built successfully!"
log_info "  - Built from commit $COMMIT_HASH"
log_info "  - Package: $(find dist -name "*.whl" -type f 2>/dev/null | tail -1)"
