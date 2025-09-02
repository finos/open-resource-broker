# PyPI Publishing Setup Guide

This guide explains how to configure PyPI publishing for the Open Host Factory Plugin using **Trusted Publishing** (OIDC-based authentication).

## Trusted Publishing Overview

Trusted Publishing uses OpenID Connect (OIDC) to authenticate with PyPI without requiring API tokens. This is more secure and eliminates the need to manage secrets.

## Required Setup

### 1. Configure Trusted Publisher on PyPI

1. Go to [PyPI Publishing Settings](https://pypi.org/manage/account/publishing/)
2. Click "Add a new pending publisher"
3. Fill in the details:
   - **PyPI Project Name:** `open-hostfactory-plugin`
   - **Owner:** `awslabs` (your GitHub organization/username)
   - **Repository name:** `open-hostfactory-plugin`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`
4. Click "Add"

### 2. Configure Trusted Publisher on Test PyPI

1. Go to [Test PyPI Publishing Settings](https://test.pypi.org/manage/account/publishing/)
2. Follow the same steps as above, but use:
   - **Environment name:** `testpypi`

### 3. GitHub Environment Setup

The workflow uses GitHub environments for additional security:

1. Go to your GitHub repository
2. Navigate to **Settings** → **Environments**
3. Create two environments:
   - **Name:** `pypi` (for production)
   - **Name:** `testpypi` (for testing)
4. Optionally add protection rules (e.g., required reviewers for production)

## Publishing Workflow

### Automatic Publishing (Recommended)
- **Trigger:** Creating a GitHub release with tag `v*.*.*`
- **Target:** Production PyPI
- **Process:** Fully automated via GitHub Actions with trusted publishing

### Manual Publishing
```bash
# Test PyPI
gh workflow run publish.yml -f environment=testpypi

# Production PyPI  
gh workflow run publish.yml -f environment=pypi
```

## Security Benefits

**No API tokens to manage** - eliminates secret rotation concerns  
**OIDC-based authentication** - more secure than static tokens  
**Automatic attestations** - digital signatures for all packages  
**Scoped permissions** - `id-token: write` only in publishing job  
**Environment protection** - optional approval workflows  

## Migration from API Tokens

If migrating from API tokens:

1. Set up trusted publishers (steps above)
2. Test with TestPyPI first
3. Remove old `PYPI_API_TOKEN` and `TEST_PYPI_API_TOKEN` secrets
4. Update workflow (already done in this repository)

## Troubleshooting

### Common Issues

**"Trusted publishing exchange failure"**
- Verify publisher configuration matches exactly
- Check environment names match workflow
- Ensure `id-token: write` permission is set

**"Environment not found"**
- Create GitHub environments in repository settings
- Verify environment names in workflow match PyPI configuration

### Verification

To verify trusted publishing is working:
1. Check workflow logs for "Trusted publishing exchange successful"
2. Look for attestation generation messages
3. Verify packages appear with attestation badges on PyPI

## References

- [PyPI Trusted Publishing Guide](https://docs.pypi.org/trusted-publishers/)
- [GitHub OIDC Documentation](https://docs.github.com/en/actions/deployment/security-hardening-your-deployments/about-security-hardening-with-openid-connect)
- [PyPA Publishing Action](https://github.com/pypa/gh-action-pypi-publish)
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
