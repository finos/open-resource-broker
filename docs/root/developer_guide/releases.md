# Release Management Guide

This guide covers the release management system for the Open Resource Broker.

## Release Process Overview

The project uses semantic-release for automated version management with a quality-gated CI/CD pipeline.

### CI/CD Pipeline Flow

```
Push to main → Quality Gates → Development Artifacts → Release Decision → Production Artifacts
     ↓              ↓                    ↓                    ↓                ↓
  CI Quality    Dev Containers      Semantic Release    Prod Containers    GitHub Release
  CI Tests      Dev PyPI Publish    (if "release:")     Prod PyPI Publish  Release Assets
  Security      Dev Docs Deploy                         Prod Docs Deploy   SBOM Upload
```

## Development Workflow

### Daily Development
Every push to `main` triggers:
1. **Quality Gates** - Tests, linting, security scans must pass
2. **Development Artifacts** - If quality passes:
   - TestPyPI publishing (versions like `0.2.3.devXXXXX`)
   - Development containers (`main`, `dev` tags)
   - Development documentation deployment

### Creating a Release

When ready to release:

```bash
# Make changes with conventional commits
git commit -m "feat: add new feature"
git commit -m "fix: resolve critical bug"

# Create release commit (triggers semantic-release)
git commit -m "release: version 1.2.0 with new features and bug fixes"
git push origin main
```

**What happens:**
1. Quality gates run first
2. Development artifacts build
3. Semantic-release calculates version based on commits:
   - `fix:` → patch version (1.0.1)
   - `feat:` → minor version (1.1.0)
   - `BREAKING CHANGE:` → major version (2.0.0)
4. GitHub release is created
5. Production pipeline builds all production artifacts

## Artifact Locations

### Development Artifacts (Every Push)
- **PyPI**: `test.pypi.org/project/open-resource-broker`
- **Containers**: `ghcr.io/awslabs/open-resource-broker:main`
- **Documentation**: GitHub Pages (development)

### Production Artifacts (On Release)
- **PyPI**: `pypi.org/project/open-resource-broker`
- **Containers**: `ghcr.io/awslabs/open-resource-broker:latest`
- **Documentation**: GitHub Pages (production)
- **Assets**: GitHub Releases (wheel, source, SBOM)

## Container Tags

### Development Tags
- `main` - Latest main branch build
- `dev` - Alias for main
- `0.2.3.devXXXXX-python3.12` - Specific dev version

### Production Tags  
- `latest` - Latest production release
- `1.0.0` - Specific version
- `python3.12` - Latest release with specific Python version

## Manual Release (Emergency)

For emergency releases or specific versions:

```bash
# Trigger production pipeline directly
gh workflow run prod-release.yml -f version=1.0.1
```

## Troubleshooting

### Release Not Created
Ensure commit message contains "release:" prefix:
```bash
# ✅ Correct
git commit -m "release: add new feature"

# ❌ Incorrect  
git commit -m "feat: add new feature"
```

### Quality Gates Failing
Development and production artifacts only build after quality gates pass. Check:
- Test failures in CI Tests workflow
- Linting errors in CI Quality workflow  
- Security issues in Security Code Scanning workflow

### Production Pipeline Not Triggered
1. Verify semantic-release created a GitHub release
2. Check the release is published (not draft)
3. Monitor Production Release Pipeline workflow

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
- Installation: `pip install --index-url https://test.pypi.org/simple/ open-resource-broker`

### PyPI (Production Environment)
- Stable releases: `1.0.1`
- Installation: `pip install open-resource-broker`

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
