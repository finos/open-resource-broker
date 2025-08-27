#!/bin/bash
set -e

# Version normalization script for all formats and use cases
# Handles: alpha/beta/rc/patch/minor/major/dev/pr releases
# Converts between git tag format and PEP 440 compliant package format

# Usage: version_normalizer.sh <input_version> <output_format>
# Formats: git, package, container, display

if [ $# -lt 2 ]; then
    echo "Usage: $0 <input_version> <output_format>"
    echo ""
    echo "Output formats:"
    echo "  git      - Git tag format (v1.0.1-rc.1)"
    echo "  package  - PEP 440 format (1.0.1rc1)"
    echo "  container- Docker safe format (1.0.1-rc1)"
    echo "  display  - Human readable (1.0.1 RC 1)"
    echo ""
    echo "Examples:"
    echo "  $0 '1.0.1-rc.1' package    # -> 1.0.1rc1"
    echo "  $0 '1.0.1rc1' git          # -> v1.0.1-rc.1"
    echo "  $0 '1.0.1.dev+abc123' package # -> 1.0.1.dev0+abc123"
    exit 1
fi

INPUT_VERSION="$1"
OUTPUT_FORMAT="$2"

# Remove 'v' prefix if present
VERSION="${INPUT_VERSION#v}"

# Parse version components
parse_version() {
    local version="$1"
    
    # Extract base version (X.Y.Z)
    BASE_VERSION=$(echo "$version" | sed -E 's/^([0-9]+\.[0-9]+\.[0-9]+).*/\1/')
    
    # Extract pre-release type and number
    PRERELEASE=""
    PRERELEASE_NUM=""
    
    if [[ "$version" =~ -alpha\.([0-9]+) ]]; then
        PRERELEASE="alpha"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ -beta\.([0-9]+) ]]; then
        PRERELEASE="beta"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ -rc\.([0-9]+) ]]; then
        PRERELEASE="rc"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ alpha([0-9]+) ]]; then
        PRERELEASE="alpha"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ beta([0-9]+) ]]; then
        PRERELEASE="beta"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ rc([0-9]+) ]]; then
        PRERELEASE="rc"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ a([0-9]+) ]]; then
        PRERELEASE="alpha"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ b([0-9]+) ]]; then
        PRERELEASE="beta"
        PRERELEASE_NUM="${BASH_REMATCH[1]}"
    fi
    
    # Extract dev/local version
    DEV_VERSION=""
    LOCAL_VERSION=""
    
    if [[ "$version" =~ \.dev\+([a-f0-9]+) ]]; then
        LOCAL_VERSION="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ \.dev-([a-f0-9]+) ]]; then
        LOCAL_VERSION="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ \.dev([0-9]+) ]]; then
        DEV_VERSION="${BASH_REMATCH[1]}"
    elif [[ "$version" =~ \.dev$ ]]; then
        DEV_VERSION="0"
    fi
}

# Format version for different outputs
format_version() {
    local format="$1"
    local result="$BASE_VERSION"
    
    case "$format" in
        "git")
            if [ -n "$PRERELEASE" ]; then
                result="${result}-${PRERELEASE}.${PRERELEASE_NUM:-1}"
            fi
            if [ -n "$DEV_VERSION" ] || [ -n "$LOCAL_VERSION" ]; then
                if [ -n "$LOCAL_VERSION" ]; then
                    result="${result}.dev-${LOCAL_VERSION}"
                else
                    result="${result}.dev${DEV_VERSION}"
                fi
            fi
            result="v${result}"
            ;;
            
        "package")
            if [ -n "$PRERELEASE" ]; then
                case "$PRERELEASE" in
                    "alpha") result="${result}a${PRERELEASE_NUM:-1}" ;;
                    "beta")  result="${result}b${PRERELEASE_NUM:-1}" ;;
                    "rc")    result="${result}rc${PRERELEASE_NUM:-1}" ;;
                esac
            fi
            if [ -n "$DEV_VERSION" ] || [ -n "$LOCAL_VERSION" ]; then
                if [ -n "$LOCAL_VERSION" ]; then
                    result="${result}.dev0+${LOCAL_VERSION}"
                else
                    result="${result}.dev${DEV_VERSION:-0}"
                fi
            fi
            ;;
            
        "container")
            if [ -n "$PRERELEASE" ]; then
                result="${result}-${PRERELEASE}${PRERELEASE_NUM:-1}"
            fi
            if [ -n "$DEV_VERSION" ] || [ -n "$LOCAL_VERSION" ]; then
                if [ -n "$LOCAL_VERSION" ]; then
                    result="${result}.dev-${LOCAL_VERSION}"
                else
                    result="${result}.dev${DEV_VERSION}"
                fi
            fi
            ;;
            
        "display")
            if [ -n "$PRERELEASE" ]; then
                case "$PRERELEASE" in
                    "alpha") result="${result} Alpha ${PRERELEASE_NUM:-1}" ;;
                    "beta")  result="${result} Beta ${PRERELEASE_NUM:-1}" ;;
                    "rc")    result="${result} RC ${PRERELEASE_NUM:-1}" ;;
                esac
            fi
            if [ -n "$DEV_VERSION" ] || [ -n "$LOCAL_VERSION" ]; then
                if [ -n "$LOCAL_VERSION" ]; then
                    result="${result} (dev+${LOCAL_VERSION})"
                else
                    result="${result} (dev${DEV_VERSION})"
                fi
            fi
            ;;
    esac
    
    echo "$result"
}

# Parse input version
parse_version "$VERSION"

# Output formatted version
format_version "$OUTPUT_FORMAT"
