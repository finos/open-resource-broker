# Release Management Guide

This guide covers the complete release management system for the Open Host Factory Plugin, including standard releases, pre-releases, and special cases.

## Overview

The project uses automated release management with:
- **Semantic Versioning** (SemVer) with pre-release support
- **GitHub Releases** with automatic tag creation
- **PyPI Publishing** for both stable and pre-releases
- **Container Registry** publishing with structured tagging
- **Automated Release Notes** generation

## Standard Release Workflow

### Basic Releases

```bash
# Patch release (1.0.0 -> 1.0.1)
make release-patch

# Minor release (1.0.0 -> 1.1.0)
make release-minor

# Major release (1.0.0 -> 2.0.0)
make release-major
```

### What Happens During a Release

1. **Version Bump**: Updates `.project.yml` with new version
2. **Validation**: Checks working directory, branch, and commit ranges
3. **Tag Creation**: Creates and pushes git tag (e.g., `v1.0.1`)
4. **GitHub Release**: Creates GitHub release with generated notes
5. **Automated Publishing**: Triggers PyPI and container registry publishing

## Pre-release Workflow

### Creating Pre-releases

Pre-releases support alpha, beta, and rc (release candidate) stages:

```bash
# Alpha releases
make release-patch-alpha    # 1.0.0 -> 1.0.1-alpha.1
make release-minor-alpha    # 1.0.0 -> 1.1.0-alpha.1
make release-major-alpha    # 1.0.0 -> 2.0.0-alpha.1

# Beta releases
make release-patch-beta     # 1.0.0 -> 1.0.1-beta.1
make release-minor-beta     # 1.0.0 -> 1.1.0-beta.1
make release-major-beta     # 1.0.0 -> 2.0.0-beta.1

# Release candidates
make release-patch-rc       # 1.0.0 -> 1.0.1-rc.1
make release-minor-rc       # 1.0.0 -> 1.1.0-rc.1
make release-major-rc       # 1.0.0 -> 2.0.0-rc.1
```

### Promoting Pre-releases

Move between pre-release stages without changing the base version:

```bash
# Increment within same stage
make promote-alpha          # 1.1.0-alpha.1 -> 1.1.0-alpha.2

# Promote to next stage
make promote-beta           # 1.1.0-alpha.2 -> 1.1.0-beta.1
make promote-rc             # 1.1.0-beta.1 -> 1.1.0-rc.1

# Final stable release
make promote-stable         # 1.1.0-rc.1 -> 1.1.0
```

### Complete Pre-release Example

```bash
# Start new feature development
make release-minor-alpha    # 0.1.0 -> 0.2.0-alpha.1

# Continue alpha testing
make promote-alpha          # 0.2.0-alpha.1 -> 0.2.0-alpha.2

# Move to beta testing
make promote-beta           # 0.2.0-alpha.2 -> 0.2.0-beta.1

# Critical fix during beta
make release-patch-beta     # 0.2.0-beta.1 -> 0.2.1-beta.1

# Release candidate
make promote-rc             # 0.2.1-beta.1 -> 0.2.1-rc.1

# Final stable release
make promote-stable         # 0.2.1-rc.1 -> 0.2.1
```

## Custom Releases

### Specific Version Override

Set an exact version instead of bumping:

```bash
# Set specific version
RELEASE_VERSION=1.5.0 make release-version

# Set specific pre-release
RELEASE_VERSION=2.0.0-beta.3 make release-version
```

### Custom Commit Ranges

Control which commits are included in the release:

```bash
# Release from specific commit to HEAD
FROM_COMMIT=abc1234 make release-minor

# Release between two specific commits
FROM_COMMIT=abc1234 TO_COMMIT=def5678 make release-minor

# Release from first commit to specific commit
FROM_COMMIT=$(git rev-list --max-parents=0 HEAD) TO_COMMIT=abc1234 make release-major
```

### Backfill Releases

Create releases for historical commit ranges (non-linear release history):

```bash
# Backfill release (requires specific version and end commit)
RELEASE_VERSION=0.1.5 TO_COMMIT=abc1234 make release-backfill

# Backfill with custom range
RELEASE_VERSION=0.1.5 FROM_COMMIT=def5678 TO_COMMIT=abc1234 make release-backfill
```

## Environment Variables

### Core Variables

- **`RELEASE_VERSION`**: Override version (use with `release-version`/`release-backfill` only)
- **`FROM_COMMIT`**: Start commit for release range (optional, smart defaults)
- **`TO_COMMIT`**: End commit for release range (optional, defaults to HEAD)
- **`DRY_RUN`**: Test mode - shows what would happen without making changes

### Control Variables

- **`ALLOW_BACKFILL`**: Enable non-linear releases (automatically set for backfill targets)
- **`ALLOW_RELEASE_FROM_BRANCH`**: Allow releases from non-main branches

### Smart Defaults

The system provides intelligent defaults for commit ranges:

- **Normal releases**: Start from commit after latest release
- **Backfill releases**: Start from first commit if `FROM_COMMIT` not specified
- **First release**: Uses entire repository history

## Dry Run Mode

Test any release operation without making changes:

```bash
# Test standard release
DRY_RUN=true make release-minor

# Test pre-release
DRY_RUN=true make release-patch-alpha

# Test custom release
DRY_RUN=true RELEASE_VERSION=1.5.0 make release-version

# Test backfill
DRY_RUN=true RELEASE_VERSION=0.1.5 TO_COMMIT=abc1234 make release-backfill
```

Dry run mode shows:
- What version would be created
- What commit range would be used
- Whether it would be a pre-release
- Any validation errors

## Validation and Safety

### Automatic Validation

The release system performs comprehensive validation:

1. **Working Directory**: Must be clean (no uncommitted changes)
2. **Branch Check**: Warns if not on main branch
3. **Tag Conflicts**: Prevents duplicate tags with helpful error messages
4. **Commit Range**: Validates chronological order and existence
5. **Overlap Detection**: Prevents overlapping releases with suggested fixes

### Error Handling

Common error scenarios and solutions:

#### Tag Already Exists
```
ERROR: Tag 'v1.0.1' already exists
Options:
1. Use a different version: RELEASE_VERSION=1.0.2 make release-version
2. Delete existing tag: git tag -d v1.0.1 && git push origin :refs/tags/v1.0.1
```

#### Overlapping Commits
```
ERROR: FROM_COMMIT 'abc1234' overlaps with existing release v1.0.0
Latest release: v1.0.0 (commit: def5678)
Use this instead:
  FROM_COMMIT=xyz9876 make release-minor
```

#### Invalid Promotion
```
ERROR: Cannot promote to beta from 1.0.0
Beta promotion works from alpha or existing beta versions
```

## Integration with CI/CD

### Automatic Triggers

Releases automatically trigger:

1. **PyPI Publishing**: Stable and pre-releases go to PyPI
2. **Container Publishing**: Multi-architecture container images
3. **Documentation**: Version-specific documentation deployment

### Manual Triggers

You can also manually trigger workflows:

```bash
# Trigger container build for current version
gh workflow run "Container Build and Publish"

# Trigger documentation deployment
gh workflow run "Documentation" --field version=$(make get-version)
```

## Troubleshooting

### Common Issues

1. **GitHub CLI not authenticated**: Run `gh auth login`
2. **Working directory not clean**: Commit or stash changes
3. **No commits since last release**: Nothing to release
4. **Invalid version format**: Use semantic versioning (x.y.z)

### Recovery Scenarios

#### Accidental Release
```bash
# Delete local tag
git tag -d v1.0.1

# Delete remote tag
git push origin :refs/tags/v1.0.1

# Delete GitHub release
gh release delete v1.0.1
```

#### Fix Release Notes
```bash
# Edit existing release
gh release edit v1.0.1 --notes "Updated release notes"
```

## Best Practices

### Release Planning

1. **Use pre-releases** for feature development and testing
2. **Follow semantic versioning** for version bumps
3. **Test with dry-run** before actual releases
4. **Create releases from main branch** for consistency

### Pre-release Strategy

1. **Alpha**: Internal testing, frequent changes
2. **Beta**: External testing, feature-complete
3. **RC**: Final testing, bug fixes only
4. **Stable**: Production-ready release

### Version Strategy

- **Patch**: Bug fixes, security updates
- **Minor**: New features, backward compatible
- **Major**: Breaking changes, major features

## Examples

### Feature Development Cycle

```bash
# Start feature development
make release-minor-alpha        # 1.0.0 -> 1.1.0-alpha.1

# Iterate during development
make promote-alpha              # 1.1.0-alpha.1 -> 1.1.0-alpha.2
make promote-alpha              # 1.1.0-alpha.2 -> 1.1.0-alpha.3

# Feature complete, start beta testing
make promote-beta               # 1.1.0-alpha.3 -> 1.1.0-beta.1

# Bug fixes during beta
make promote-beta               # 1.1.0-beta.1 -> 1.1.0-beta.2

# Release candidate
make promote-rc                 # 1.1.0-beta.2 -> 1.1.0-rc.1

# Final release
make promote-stable             # 1.1.0-rc.1 -> 1.1.0
```

### Hotfix Release

```bash
# Emergency patch
make release-patch              # 1.1.0 -> 1.1.1

# Or test first
DRY_RUN=true make release-patch
make release-patch
```

### Historical Release

```bash
# Create release for historical commits
RELEASE_VERSION=0.9.0 TO_COMMIT=historical-commit make release-backfill
```
