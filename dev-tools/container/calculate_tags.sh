#!/bin/bash
# Calculate container tags for current context

set -euo pipefail

# Get version from make
VERSION=$(make -s get-version)

# Determine if this is a release
IS_RELEASE="${IS_RELEASE:-false}"
if [[ "$IS_RELEASE" == "true" ]]; then
    # Release tags
    PRIMARY_TAGS="latest,$VERSION"
    PYTHON_TAGS="python3.9,python3.10,python3.11,python3.12,python3.13"
elif [[ "${GITHUB_REF:-}" == "refs/heads/main" ]]; then
    # Main branch tags
    PRIMARY_TAGS="main,dev"
    PYTHON_TAGS="python3.9,python3.10,python3.11,python3.12,python3.13"
else
    # Feature/PR branches - no special tags
    PRIMARY_TAGS=""
    PYTHON_TAGS=""
fi

# Output in format expected by GitHub Actions
echo "primary-tags=$PRIMARY_TAGS"
echo "python-tags=$PYTHON_TAGS"
