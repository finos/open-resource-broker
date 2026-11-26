#!/bin/bash
set -e

# Historical Build Script
# Builds packages for historical commits using overlay approach

COMMIT_HASH="$1"
VERSION="$2"
DRY_RUN="${DRY_RUN:-false}"

# Resolved from this script, because the historical commit does not contain the lock file.
PACKAGE_BUILD_LOCK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.github/requirements/package-build.txt"

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

# Build in a separate detached worktree so the caller's checkout (branch,
# modified and untracked files) is never touched. The wheel goes to the
# caller's dist/ by absolute path because the worktree is removed on exit.
REPO_ROOT="$(git rev-parse --show-toplevel)"
OUT_DIR="$REPO_ROOT/dist"
WORKTREE="$(mktemp -d)"

cleanup() {
    # Keep going on errors so every step gets its chance; the exit status of
    # the script is not changed
    set +e
    cd "$REPO_ROOT" || return
    git worktree remove --force "${WORKTREE:?}" >/dev/null 2>&1
    rm -rf "${WORKTREE:?}"
    git worktree prune
}
trap cleanup EXIT

log_info "Creating temporary worktree for $COMMIT_HASH"
git worktree add --detach -q "$WORKTREE" "$COMMIT_HASH"
cd "$WORKTREE"

# Overlay approach: Create modern pyproject.toml with historical source
log_info "Applying overlay build system with version $VERSION"

# Create modern pyproject.toml
cat > pyproject.toml << EOF
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "open-resource-broker"
version = "$VERSION"
description = "Cloud provider integration plugin for IBM Spectrum Symphony Host Factory"
authors = [{name = "Open Resource Broker Maintainers", email = "open-resource-broker-maintainers@lists.finos.org"}]
license = {text = "Apache-2.0"}
readme = "README.md"
requires-python = ">=3.9"
dependencies = []

[project.urls]
Homepage = "https://github.com/finos/open-resource-broker"
Repository = "https://github.com/finos/open-resource-broker"

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
python -m pip install --quiet --require-hashes --no-deps -r "$PACKAGE_BUILD_LOCK" || {
    log_error "Installing the build tooling failed"
    exit 1
}
python -m build --wheel --outdir "$OUT_DIR" || {
    log_error "Package build failed"
    exit 1
}

log_info "Historical package $VERSION built successfully!"
log_info "  - Built from commit $COMMIT_HASH"
log_info "  - Package: $OUT_DIR/open_resource_broker-${VERSION}-py3-none-any.whl"
