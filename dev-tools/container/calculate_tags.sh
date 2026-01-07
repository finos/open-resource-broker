#!/bin/bash
# Calculate container tags for current context

set -euo pipefail

# Get version from make
VERSION=$(make -s get-version)

# Get Python versions from project config (same source as container build matrix)
PYTHON_VERSIONS=$(make -s print-PYTHON_VERSIONS 2>/dev/null || echo "3.10 3.11 3.12 3.13")

# Convert space-separated versions to comma-separated python tags
PYTHON_TAGS=""
if [[ -n "$PYTHON_VERSIONS" ]]; then
    PYTHON_TAGS=$(echo "$PYTHON_VERSIONS" | tr ' ' '\n' | sed 's/^/python/' | tr '\n' ',' | sed 's/,$//')
fi

# Determine if this is a release
IS_RELEASE="${IS_RELEASE:-false}"
if [[ "$IS_RELEASE" == "true" ]]; then
    # Release tags
    PRIMARY_TAGS="latest,$VERSION"
elif [[ "${GITHUB_REF:-}" == "refs/heads/main" ]]; then
    # Main branch tags
    PRIMARY_TAGS="main,dev"
else
    # Feature/PR branches - no special tags
    PRIMARY_TAGS=""
    PYTHON_TAGS=""
fi

# Output in format expected by GitHub Actions
echo "primary-tags=$PRIMARY_TAGS"
echo "python-tags=$PYTHON_TAGS"
