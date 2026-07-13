#!/bin/bash
set -euo pipefail

# mike_deploy.sh — deploy versioned docs via mike to the gh-pages branch,
# then export the branch content to docs/site for Pages artifact upload.
#
# Usage:
#   mike_deploy.sh dev [--push]
#       Deploy/update the 'dev' alias from the current working tree.
#
#   mike_deploy.sh release <X.Y> [--push]
#       Deploy version X.Y, update the 'latest' alias, and set latest as default.
#
# The --push flag (optional, must come last) pushes gh-pages to origin.
# Omit it for local-only builds (PR validation, local testing).
# Workflows pass --push on main/release deployments only.

MODE="${1:-}"
PUSH=false

case "$MODE" in
    dev)
        VERSION_ARG=""
        PUSH_ARG="${2:-}"
        ;;
    release)
        VERSION_ARG="${2:-}"
        if [ -z "$VERSION_ARG" ]; then
            echo "ERROR: 'release' mode requires a version argument (e.g. mike_deploy.sh release 1.7)" >&2
            exit 1
        fi
        PUSH_ARG="${3:-}"
        ;;
    *)
        echo "ERROR: Unknown mode '${MODE}'. Use: dev [--push] | release <X.Y> [--push]" >&2
        exit 1
        ;;
esac

if [ "${PUSH_ARG:-}" = "--push" ]; then
    PUSH=true
fi

echo "==> mike_deploy.sh: mode=${MODE}${VERSION_ARG:+ version=${VERSION_ARG}} push=${PUSH}"

# Configure git identity in CI (mike needs a committer to write to gh-pages).
if [ -n "${CI:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
    echo "==> Configuring git identity for CI..."
    git config --global user.name "github-actions[bot]"
    git config --global user.email "github-actions[bot]@users.noreply.github.com"
fi

# Ensure local gh-pages branch exists for mike to write to.
# The branch may not exist yet on origin (first deploy creates it).
# If it does exist on origin, fetch it so mike sees the existing version history.
echo "==> Checking for gh-pages branch on origin..."
if git ls-remote --exit-code --heads origin gh-pages >/dev/null 2>&1; then
    echo "==> Found gh-pages on origin — fetching..."
    git fetch origin gh-pages:gh-pages 2>/dev/null || git fetch origin gh-pages
else
    echo "==> gh-pages branch not found on origin — mike will initialise it on first deploy."
fi

# Run mike deploy.
echo "==> Running mike deploy (mode=${MODE})..."
case "$MODE" in
    dev)
        uv run mike deploy \
            --config-file docs/mkdocs.yml \
            --branch gh-pages \
            dev

        # Set root default to 'dev' only when no 'latest' alias exists yet
        # (i.e. pre-first-release).  Once a release sets default=latest we
        # must never stomp it back to dev on subsequent dev pushes.
        echo "==> Checking whether a 'latest' alias exists..."
        if uv run mike list \
                --config-file docs/mkdocs.yml \
                --branch gh-pages 2>/dev/null \
                | grep -q '\blatest\b'; then
            echo "==> 'latest' alias found — leaving default unchanged (root stays on latest)."
        else
            echo "==> 'latest' alias not found — setting default to 'dev' so site root is reachable."
            uv run mike set-default \
                --config-file docs/mkdocs.yml \
                --branch gh-pages \
                dev
        fi
        ;;
    release)
        uv run mike deploy \
            --config-file docs/mkdocs.yml \
            --branch gh-pages \
            --update-aliases \
            "${VERSION_ARG}" latest
        echo "==> Setting 'latest' as default..."
        uv run mike set-default \
            --config-file docs/mkdocs.yml \
            --branch gh-pages \
            latest
        ;;
esac
echo "==> mike deploy complete."

# Optionally push gh-pages to origin.
if [ "$PUSH" = "true" ]; then
    echo "==> Pushing gh-pages to origin..."
    git push origin gh-pages
    echo "==> Push complete."
fi

# Export the gh-pages branch content to docs/site for artifact upload.
# actions/upload-pages-artifact expects a plain directory tree.
echo "==> Exporting gh-pages branch to docs/site..."
rm -rf docs/site
mkdir -p docs/site

# Use git archive to extract the branch content — avoids worktree cleanup issues
# in CI environments where the worktree directory may already be in use.
git archive gh-pages | tar -x -C docs/site

# Strip any stray .git file/directory that git archive might have included.
rm -rf docs/site/.git

echo "==> Verifying docs/site content..."
if [ ! -f "docs/site/versions.json" ]; then
    echo "ERROR: docs/site/versions.json not found — mike deploy may have failed." >&2
    exit 1
fi

# Root index.html (redirect to default alias) is created by 'mike set-default'.
# Both paths now call set-default, so index.html must always be present after deploy.
if [ ! -f "docs/site/index.html" ]; then
    echo "ERROR: docs/site/index.html not found after ${MODE} deploy — root redirect is missing." >&2
    exit 1
fi

echo "==> docs/site contents (top-level):"
ls -1 docs/site/

echo "==> versions.json:"
cat docs/site/versions.json

echo "==> mike_deploy.sh complete. docs/site is ready for Pages artifact upload."
