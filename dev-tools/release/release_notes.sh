#!/bin/bash
set -e

# Release notes generator
# Usage: release_notes.sh <from_commit> <to_commit> [version]

if [ $# -lt 2 ]; then
    echo "Usage: $0 <from_commit> <to_commit> [version]"
    exit 1
fi

FROM_COMMIT=$1
TO_COMMIT=$2
VERSION=${3:-""}

# Get to project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"
cd "$PROJECT_ROOT"

# Validate commits exist
if ! git rev-parse --verify "$FROM_COMMIT" >/dev/null 2>&1; then
    echo "Error: FROM_COMMIT '$FROM_COMMIT' does not exist"
    exit 1
fi

if ! git rev-parse --verify "$TO_COMMIT" >/dev/null 2>&1; then
    echo "Error: TO_COMMIT '$TO_COMMIT' does not exist"
    exit 1
fi

# Extract GitHub username from email
extract_github_user() {
    local email="$1"
    if echo "$email" | grep -q "users.noreply.github.com"; then
        echo "$email" | sed 's/.*+//' | sed 's/@.*//'
    else
        echo "$email" | sed 's/@.*$//' | sed 's/\..*$//'
    fi
}

# Load custom configuration if available
load_config() {
    local config_file="$PROJECT_ROOT/.github/release-notes.conf"
    if [ -f "$config_file" ]; then
        # Source the config file to override default patterns
        # shellcheck source=/dev/null
        source "$config_file"
    fi
}

# Default patterns (can be overridden by config)
BREAKING_PATTERN="${BREAKING_PATTERN:-^[a-f0-9]+ .*(breaking|BREAKING|break)}"
FEATURES_PATTERN="${FEATURES_PATTERN:-^[a-f0-9]+ (feat|add|implement|create)}"
BUGS_PATTERN="${BUGS_PATTERN:-^[a-f0-9]+ (fix|bug|resolve|patch)}"
DOCS_PATTERN="${DOCS_PATTERN:-^[a-f0-9]+ (doc|readme|guide|update.*doc)}"

# Get repository info with fallback
get_repo_info() {
    if command -v gh >/dev/null 2>&1; then
        gh repo view --json owner,name --jq '.owner.login + "/" + .name' 2>/dev/null && return
    fi
    
    # Fallback to git remote
    local remote_url
    remote_url=$(git remote get-url origin 2>/dev/null || echo "")
    if [[ "$remote_url" =~ github\.com[:/]([^/]+/[^/]+)(\.git)?$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        echo "owner/repo"
    fi
}

generate_notes() {
    local from_commit=$1
    local to_commit=$2
    local version=$3
    local repo_info
    repo_info=$(get_repo_info)
    
    # Load configuration
    load_config
    
    # Check if this is a single-commit historical release
    if [ "$from_commit" = "$to_commit" ]; then
        local commit_msg_full commit_msg_first_line
        commit_msg_full=$(git show --no-patch --format="%B" "$to_commit")
        commit_msg_first_line=$(echo "$commit_msg_full" | head -1)
        
        # Check if commit has substantial content (more than just the first line)
        local line_count
        line_count=$(echo "$commit_msg_full" | wc -l | tr -d ' ')
        
        if [ "$line_count" -gt 1 ]; then
            # Use full commit message for detailed commits
            echo "## $commit_msg_first_line"
            echo ""
            echo "$commit_msg_full" | tail -n +2 | sed 's/^$//' | sed '/^$/N;/^\n$/d'
            echo ""
            
            # Add What's Changed section for single commit
            echo "## What's Changed"
            echo ""
            
            # Categorize the single commit
            local commit_line
            commit_line=$(git log --oneline --no-merges -1 "$to_commit")
            local breaking features bugs docs other
            breaking=$(echo "$commit_line" | grep -iE "$BREAKING_PATTERN" || true)
            features=$(echo "$commit_line" | grep -iE "$FEATURES_PATTERN" | grep -viE "(breaking|BREAKING|break)" || true)
            bugs=$(echo "$commit_line" | grep -iE "$BUGS_PATTERN" || true)
            docs=$(echo "$commit_line" | grep -iE "$DOCS_PATTERN" || true)
            other=$(echo "$commit_line" | grep -viE "($BREAKING_PATTERN|$FEATURES_PATTERN|$BUGS_PATTERN|$DOCS_PATTERN)" || true)
            
            [ -n "$breaking" ] && echo "### Breaking Changes" && echo "- $breaking" && echo ""
            [ -n "$features" ] && echo "### Features" && echo "- $features" && echo ""
            [ -n "$bugs" ] && echo "### Bug Fixes" && echo "- $bugs" && echo ""
            [ -n "$docs" ] && echo "### Documentation" && echo "- $docs" && echo ""
            [ -n "$other" ] && echo "### Other Changes" && echo "- $other" && echo ""
            
            # Add contributors section
            local contributor
            contributor=$(git log --format="%ae" -1 "$to_commit")
            if [ -n "$contributor" ]; then
                local github_user
                github_user=$(extract_github_user "$contributor")
                echo "### Contributors"
                echo "- [@$github_user](https://github.com/$github_user)"
                echo ""
            fi
            
            echo "**Changelog**: https://github.com/$repo_info/commit/$to_commit"
            return 0
        else
            # Use enhanced context for short commit messages
            local context=""
            if echo "$commit_msg_first_line" | grep -qi "poc\|proof.*concept"; then
                context="This proof-of-concept release demonstrates the initial feasibility and core architecture of the Open Host Factory Plugin."
            elif echo "$commit_msg_first_line" | grep -qi "mvp" && ! echo "$commit_msg_first_line" | grep -qi "pre-mvp\|pre.*mvp"; then
                context="This MVP release provides the essential functionality needed for production deployment with core features fully implemented."
            elif echo "$commit_msg_first_line" | grep -qi "pre-mvp\|pre.*mvp"; then
                context="This pre-MVP release includes foundational components and early feature implementations leading toward the minimum viable product."
            elif echo "$version" | grep -qi "rc0\|rc1"; then
                context="This release candidate represents a significant milestone in the development timeline with stable core functionality."
            elif echo "$version" | grep -qi "alpha\|a[0-9]"; then
                context="This alpha release includes experimental features and early implementations for testing and feedback."
            else
                context="This historical release represents an important milestone in the project's development timeline."
            fi
            
            echo "## $commit_msg_first_line"
            echo ""
            echo "$context"
            echo ""
            
            # Add What's Changed section for single commit
            echo "## What's Changed"
            echo ""
            
            # Categorize the single commit
            local commit_line
            commit_line=$(git log --oneline --no-merges -1 "$to_commit")
            local breaking features bugs docs other
            breaking=$(echo "$commit_line" | grep -iE "$BREAKING_PATTERN" || true)
            features=$(echo "$commit_line" | grep -iE "$FEATURES_PATTERN" | grep -viE "(breaking|BREAKING|break)" || true)
            bugs=$(echo "$commit_line" | grep -iE "$BUGS_PATTERN" || true)
            docs=$(echo "$commit_line" | grep -iE "$DOCS_PATTERN" || true)
            other=$(echo "$commit_line" | grep -viE "($BREAKING_PATTERN|$FEATURES_PATTERN|$BUGS_PATTERN|$DOCS_PATTERN)" || true)
            
            [ -n "$breaking" ] && echo "### Breaking Changes" && echo "- $breaking" && echo ""
            [ -n "$features" ] && echo "### Features" && echo "- $features" && echo ""
            [ -n "$bugs" ] && echo "### Bug Fixes" && echo "- $bugs" && echo ""
            [ -n "$docs" ] && echo "### Documentation" && echo "- $docs" && echo ""
            [ -n "$other" ] && echo "### Other Changes" && echo "- $other" && echo ""
            
            # Add contributors section
            local contributor
            contributor=$(git log --format="%ae" -1 "$to_commit")
            if [ -n "$contributor" ]; then
                local github_user
                github_user=$(extract_github_user "$contributor")
                echo "### Contributors"
                echo "- [@$github_user](https://github.com/$github_user)"
                echo ""
            fi
            
            echo "**Changelog**: https://github.com/$repo_info/commit/$to_commit"
            return 0
        fi
    fi
    
    # Validate commit range has changes
    if ! git rev-list --count "$from_commit..$to_commit" | grep -q '^[1-9]'; then
        echo "## No Changes"
        echo ""
        echo "No commits found between $from_commit and $to_commit."
        return 0
    fi
    
    # Check if this is the first release
    first_commit=$(git rev-list --max-parents=0 HEAD)
    if [ "$from_commit" = "$first_commit" ] && [ -z "$(git tag -l "v*")" ]; then
        echo "## Initial Release"
        echo ""
        echo "First public release of the Open Host Factory Plugin."
        echo ""
        echo "### Features"
        echo "- Cloud provider integration for IBM Spectrum Symphony Host Factory"
        echo "- AWS support with multiple provisioning strategies"
        echo "- REST API with OpenAPI documentation"
        echo "- Clean architecture with dependency injection"
        echo "- Comprehensive testing and CI/CD pipeline"
        echo ""
        echo "**Changelog**: https://github.com/$repo_info/compare/${from_commit:0:8}...${to_commit:0:8}"
        return 0
    fi
    
    # Try to use GitHub's generate-notes API with better error handling
    if command -v gh >/dev/null 2>&1 && [ -n "$version" ]; then
        # Find previous tag for comparison with improved logic
        local previous_tag=""
        if [ "$from_commit" != "$first_commit" ]; then
            # Get all tags that are ancestors of from_commit, sorted by version
            previous_tag=$(git tag -l "v*" --sort=-version:refname --merged "$from_commit" | head -1)
            
            # If no merged tags found, get the most recent tag before from_commit
            if [ -z "$previous_tag" ]; then
                previous_tag=$(git tag -l "v*" --sort=-version:refname | head -1)
            fi
        fi
        
        if [ -n "$previous_tag" ]; then
            echo "Generating notes from $previous_tag to v$version..." >&2
            local api_result
            if api_result=$(timeout 30 gh api "repos/$repo_info/releases/generate-notes" \
                --field tag_name="v$version" \
                --field previous_tag_name="$previous_tag" \
                --jq '.body' 2>/dev/null); then
                # Check if API returned meaningful content (not just changelog link)
                if [ -n "$api_result" ] && [ "$api_result" != "null" ] && ! echo "$api_result" | grep -q "^\\*\\*Full Changelog\\*\\*.*compare.*\$"; then
                    echo "$api_result"
                    return 0
                fi
            fi
            echo "GitHub API returned minimal content, using fallback..." >&2
        fi
    fi
    
    generate_fallback_notes "$from_commit" "$to_commit"
}

generate_fallback_notes() {
    local from_commit=$1
    local to_commit=$2
    local repo_info
    repo_info=$(get_repo_info)
    
    echo "## What's Changed"
    echo ""
    
    # Categorize commits by patterns (configurable)
    local breaking features bugs docs other
    breaking=$(git log --oneline --no-merges "$from_commit..$to_commit" | grep -iE "$BREAKING_PATTERN" || true)
    features=$(git log --oneline --no-merges "$from_commit..$to_commit" | grep -iE "$FEATURES_PATTERN" | grep -viE "(breaking|BREAKING|break)" || true)
    bugs=$(git log --oneline --no-merges "$from_commit..$to_commit" | grep -iE "$BUGS_PATTERN" || true)
    docs=$(git log --oneline --no-merges "$from_commit..$to_commit" | grep -iE "$DOCS_PATTERN" || true)
    other=$(git log --oneline --no-merges "$from_commit..$to_commit" | grep -viE "($BREAKING_PATTERN|$FEATURES_PATTERN|$BUGS_PATTERN|$DOCS_PATTERN)" || true)
    
    [ -n "$breaking" ] && echo "### Breaking Changes" && echo "$breaking" | while IFS= read -r line; do echo "- $line"; done && echo ""
    [ -n "$features" ] && echo "### Features" && echo "$features" | while IFS= read -r line; do echo "- $line"; done && echo ""
    [ -n "$bugs" ] && echo "### Bug Fixes" && echo "$bugs" | while IFS= read -r line; do echo "- $line"; done && echo ""
    [ -n "$docs" ] && echo "### Documentation" && echo "$docs" | while IFS= read -r line; do echo "- $line"; done && echo ""
    [ -n "$other" ] && echo "### Other Changes" && echo "$other" | while IFS= read -r line; do echo "- $line"; done && echo ""
    
    # Extract contributors with GitHub usernames
    local contributors
    contributors=$(git log --format="%ae" --no-merges "$from_commit..$to_commit" | sort -u | grep -v "^$" | while read -r email; do
        local github_user
        github_user=$(extract_github_user "$email")
        echo "- [@$github_user](https://github.com/$github_user)"
    done)
    
    if [ -n "$contributors" ]; then
        echo "### Contributors"
        echo "$contributors"
        echo ""
    fi
    
    echo "**Changelog**: https://github.com/$repo_info/compare/${from_commit:0:8}...${to_commit:0:8}"
}

# Generate and output the notes
generate_notes "$FROM_COMMIT" "$TO_COMMIT" "$VERSION"
