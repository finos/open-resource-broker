# Release Management Guide

This guide covers the complete release management system for the Open Host Factory Plugin.

## Release Process Overview

### Automated Release Flow

The system creates releases automatically based on a scheduled progression:

```
Developer workflow → Scheduled releases → Manual stable release
```

### What Happens During Development

**Pull Request Creation**:
- Automated tests execute
- No releases are created

**Merge to Main Branch**:
- Code is integrated
- No immediate release occurs
- Changes await scheduled release cycles

### Scheduled Release Automation

**Daily Alpha Releases (5 AM UTC)**:
- System checks for commits since last alpha release
- If changes exist: creates alpha version (e.g., `1.0.1-alpha.1`)
- Publishes to TestPyPI for testing
- If no changes: no action taken

**Weekly Beta Releases (Monday 6 AM UTC)**:
- System checks for alpha releases to promote
- If new alphas exist: creates beta version (e.g., `1.0.1-beta.1`)
- Publishes to TestPyPI for broader testing
- If no new alphas: no action taken

**Bi-weekly RC Releases (Wednesday 11 AM UTC)**:
- System checks for beta releases to promote
- If new betas exist: creates release candidate (e.g., `1.0.1-rc.1`)
- Publishes to TestPyPI for final testing
- If no new betas: no action taken

**Manual Stable Releases**:
- Developer executes: `make release`
- Creates stable version (e.g., `1.0.1`)
- Publishes to production PyPI

## Manual Release Commands

### Local Testing Commands
```bash
make release-alpha-if-needed    # Check if alpha release needed
make release-beta-if-needed     # Check if beta release needed  
make release-rc-if-needed       # Check if RC release needed
```

### Force Release Creation
```bash
make release-patch-alpha        # Create alpha release
make release-patch-beta         # Create beta release
make release-patch-rc           # Create RC release
make release                    # Create stable release
```

### Version Increment Options
```bash
make release-patch              # 1.0.0 → 1.0.1
make release-minor              # 1.0.0 → 1.1.0  
make release-major              # 1.0.0 → 2.0.0
```

## Publication Targets

### TestPyPI (Testing Environment)
- Alpha releases: `1.0.1-alpha.1`
- Beta releases: `1.0.1-beta.1`
- RC releases: `1.0.1-rc.1`
- Installation: `pip install --index-url https://test.pypi.org/simple/ open-hostfactory-plugin`

### PyPI (Production Environment)
- Stable releases: `1.0.1`
- Installation: `pip install open-hostfactory-plugin`

## Release Schedule

| Frequency | Release Type | Version Format | Target |
|-----------|--------------|----------------|--------|
| Daily 5 AM UTC | Alpha | `1.0.1-alpha.1` | TestPyPI |
| Monday 6 AM UTC | Beta | `1.0.1-beta.1` | TestPyPI |
| Wednesday 11 AM UTC | RC | `1.0.1-rc.1` | TestPyPI |
| Manual | Stable | `1.0.1` | PyPI |

## Version Format Specification

All versions follow PEP440 compliance:

- Stable: `1.0.1`
- Alpha: `1.0.1-alpha.1`
- Beta: `1.0.1-beta.1`
- RC: `1.0.1-rc.1`
- Development: `1.0.1.dev123456`

## Release Type Definitions

- **Alpha**: Initial development releases with potential instability
- **Beta**: More stable releases suitable for broader testing
- **RC**: Release candidates approaching production readiness
- **Stable**: Production-ready releases

## Commit Message Requirements

Version increments are determined by commit message format:

```bash
feat: add new feature     # Minor version increment (1.0.0 → 1.1.0)
fix: resolve bug         # Patch version increment (1.0.0 → 1.0.1)
BREAKING CHANGE: ...     # Major version increment (1.0.0 → 2.0.0)
```

## Emergency Release Process

For critical production issues:

```bash
git checkout -b hotfix/issue-description
# Implement fix
git commit -m "fix: critical issue description"
# Merge to main
make release
```

## Troubleshooting

### Check Current Status
```bash
make version-show               # Display current version
git tag -l "v*" --sort=-version:refname | head -5  # Show recent tags
```

### Manual Package Build
```bash
make build                      # Build package locally
```

### Release Validation
Scheduled releases automatically skip execution when no changes are detected since the last release of that type.

## Technical Implementation

The release system uses:
- Semantic-release for version calculation and tagging
- GitHub Actions for automation
- PyPI trusted publishing for secure package distribution
- Makefile targets for local execution and testing

All release commands can be executed locally for testing and validation before automated execution.
