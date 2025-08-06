# PyPI Publishing Setup Guide

This guide explains how to configure PyPI publishing for the Open Host Factory Plugin.

## Required Secrets

The following GitHub repository secrets must be configured:

### 1. Production PyPI Token
- **Secret Name:** `PYPI_API_TOKEN`
- **Description:** API token for publishing to production PyPI
- **How to get:**
  1. Go to [PyPI Account Settings](https://pypi.org/manage/account/)
  2. Scroll to "API tokens" section
  3. Click "Add API token"
  4. Name: `open-hostfactory-plugin-github-actions`
  5. Scope: Select "Entire account" or specific project
  6. Copy the generated token (starts with `pypi-`)

### 2. Test PyPI Token
- **Secret Name:** `TEST_PYPI_API_TOKEN`
- **Description:** API token for publishing to Test PyPI
- **How to get:**
  1. Go to [Test PyPI Account Settings](https://test.pypi.org/manage/account/)
  2. Follow same steps as production PyPI
  3. Copy the generated token

## Setting Up GitHub Secrets

1. Go to your GitHub repository
2. Navigate to **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add both secrets:
   - Name: `PYPI_API_TOKEN`, Value: `pypi-AgE...` (your production token)
   - Name: `TEST_PYPI_API_TOKEN`, Value: `pypi-AgE...` (your test token)

## Publishing Workflow

### Automatic Publishing (Recommended)
- **Trigger:** Creating a GitHub release with tag `v*.*.*`
- **Target:** Production PyPI
- **Process:** Fully automated via GitHub Actions

### Manual Publishing
```bash
# Test PyPI
gh workflow run publish.yml -f environment=test-pypi

# Production PyPI  
gh workflow run publish.yml -f environment=pypi
```

## Package Registration

### First-time Setup
1. **Register on PyPI:**
   - Production: https://pypi.org/account/register/
   - Test: https://test.pypi.org/account/register/

2. **Reserve Package Name:**
   ```bash
   # Build package locally
   python -m build
   
   # Upload to Test PyPI first
   python -m twine upload --repository testpypi dist/*
   
   # Then to Production PyPI
   python -m twine upload dist/*
   ```

3. **Verify Package:**
   ```bash
   # Test installation from Test PyPI
   pip install --index-url https://test.pypi.org/simple/ open-hostfactory-plugin
   
   # Test installation from Production PyPI
   pip install open-hostfactory-plugin
   ```

## Security Best Practices

### Token Security
- **Scope Limitation:** Use project-scoped tokens when possible
- **Token Rotation:** Rotate tokens every 6-12 months
- **Access Control:** Limit repository access to necessary team members

### Publishing Security
- **Two-Factor Authentication:** Enable 2FA on PyPI accounts
- **Release Verification:** Always verify releases after publishing
- **Dependency Scanning:** Monitor for dependency vulnerabilities

## Troubleshooting

### Common Issues

1. **403 Forbidden Error:**
   - Check token validity and scope
   - Verify package name isn't already taken
   - Ensure token has upload permissions

2. **Package Already Exists:**
   - PyPI doesn't allow overwriting existing versions
   - Increment version number in `pyproject.toml`
   - Use `--skip-existing` flag for re-uploads

3. **Build Failures:**
   - Check `pyproject.toml` configuration
   - Verify all required files are included
   - Test build locally: `python -m build`

### Debug Commands
```bash
# Test token validity
python -m twine check dist/*

# Verbose upload
python -m twine upload --verbose dist/*

# Check package metadata
python -m twine check dist/*
```

## Workflow Configuration

The publish workflow supports:
- **Environments:** `test-pypi`, `pypi`
- **Triggers:** Release creation, manual dispatch
- **Features:** SBOM generation, artifact upload, deployment summaries

### Environment Variables
```yaml
env:
  PYTHON_VERSION: '3.11'
  PACKAGE_NAME: 'open-hostfactory-plugin'
```

## Monitoring

### Post-Publication Checks
1. **Package Availability:** Verify package appears on PyPI
2. **Installation Test:** Test installation in clean environment
3. **Dependency Resolution:** Check dependency compatibility
4. **Documentation:** Verify README renders correctly on PyPI

### Metrics to Monitor
- Download statistics
- Version adoption rates
- Issue reports related to packaging
- Security vulnerability reports
