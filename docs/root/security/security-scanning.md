# Security Scanning Guide

This document provides comprehensive guidance on the security scanning tools and processes implemented in the Open Host Factory Plugin project.

## Overview

The project implements a multi-layered security scanning approach that includes:

- **Static Application Security Testing (SAST)**
- **Dependency Vulnerability Scanning**
- **Container Security Scanning**
- **Infrastructure as Code Security**
- **Software Bill of Materials (SBOM) Generation**
- **Secret Detection**

## Security Tools

### 1. Bandit - Python Security Linter

**Purpose**: Static analysis of Python code for common security issues.

**Usage**:
```bash
# Run via Makefile
make security

# Run directly
python -m bandit -r src/ -f sarif -o bandit-results.sarif
```

**Configuration**: `.bandit.yaml`

**Output Formats**:
- SARIF (for GitHub Security tab)
- JSON (for CI/CD processing)
- Text (for human review)

### 2. Safety - Dependency Vulnerability Scanner

**Purpose**: Check Python dependencies for known security vulnerabilities.

**Usage**:
```bash
# Check current environment
python -m safety check

# Generate JSON report
python -m safety check --json --output safety-report.json
```

**Features**:
- CVE database integration
- Severity scoring
- Remediation suggestions

### 3. Trivy - Container Vulnerability Scanner

**Purpose**: Comprehensive container image security scanning.

**Usage**:
```bash
# Scan container image
make security-container

# Direct usage
trivy image --format sarif --output trivy-results.sarif myimage:latest
```

**Scan Types**:
- OS package vulnerabilities
- Language-specific vulnerabilities
- Configuration issues
- Secret detection

### 4. Hadolint - Dockerfile Security Linter

**Purpose**: Best practices and security analysis for Dockerfiles.

**Usage**:
```bash
# Scan Dockerfile
hadolint Dockerfile --format sarif > hadolint-results.sarif
```

**Checks**:
- Security best practices
- Performance optimizations
- Maintainability issues

### 5. CodeQL - Semantic Code Analysis

**Purpose**: Deep semantic analysis of code for security vulnerabilities.

**Configuration**: `.github/codeql/codeql-config.yml`

**Features**:
- Advanced dataflow analysis
- Custom security queries
- Integration with GitHub Security tab

### 6. Semgrep - Static Analysis

**Purpose**: Fast, customizable static analysis for security patterns.

**Usage**: Automated in CI/CD pipeline

**Rule Sets**:
- Security audit rules
- Secret detection
- Python-specific security patterns

## SARIF Integration

All security tools generate SARIF (Static Analysis Results Interchange Format) files for standardized reporting.

### SARIF Files Generated

- `bandit-results.sarif` - Python security issues
- `trivy-results.sarif` - Container vulnerabilities
- `hadolint-results.sarif` - Dockerfile issues
- `semgrep.sarif` - Static analysis results
- `codeql-results.sarif` - CodeQL analysis

### GitHub Security Tab

SARIF files are automatically uploaded to GitHub's Security tab, providing:

- Centralized vulnerability dashboard
- Issue tracking and management
- Integration with pull requests
- Historical trend analysis

## Software Bill of Materials (SBOM)

### SBOM Generation

The project generates comprehensive SBOMs in multiple formats:

```bash
# Generate all SBOM files
make sbom-generate
```

### SBOM Formats

**SPDX (Software Package Data Exchange)**:
- `python-sbom-spdx.json` - Python dependencies
- `project-sbom-spdx.json` - Full project
- `container-sbom-spdx.json` - Container image

**CycloneDX**:
- `python-sbom-cyclonedx.json` - Python dependencies
- `project-sbom-cyclonedx.json` - Full project
- `container-sbom-cyclonedx.json` - Container image

### SBOM Tools

- **pip-audit**: Python package SBOM generation
- **Syft**: Universal SBOM generator
- **Docker Scout**: Container SBOM analysis

## CI/CD Integration

### GitHub Actions Workflows

**Security Workflow** (`.github/workflows/security.yml`):
- Runs on push, PR, and weekly schedule
- Generates SARIF files
- Uploads to GitHub Security tab

**CodeQL Workflow** (`.github/workflows/codeql.yml`):
- Deep semantic analysis
- Weekly scheduled scans
- Custom query configuration

**Container Security** (`.github/workflows/container-security.yml`):
- Multi-tool container scanning
- Dockerfile security analysis
- SARIF integration

**SBOM Generation** (`.github/workflows/sbom.yml`):
- Automated SBOM creation
- Multiple format support
- Release artifact attachment

### Pre-commit Hooks

Security checks integrated into pre-commit workflow:

```yaml
# Security checks in .pre-commit-config.yaml
- id: bandit-security-check
- id: safety-dependency-check
- id: secrets-detection
- id: dockerfile-security
```

## Local Development

### Running Security Scans

**Quick Security Check**:
```bash
make security
```

**Full Security Suite**:
```bash
make security-full
```

**Comprehensive Report**:
```bash
make security-report
```

**Custom Security Scan**:
```bash
python scripts/security/security_scan.py
```

### SARIF Validation

Validate SARIF files before upload:

```bash
python scripts/security/validate_sarif.py *.sarif
```

## Security Policies

### Vulnerability Response

1. **Critical/High Severity**: Address within 24-48 hours
2. **Medium Severity**: Address within 1 week
3. **Low Severity**: Address in next release cycle

### Dependency Management

- Weekly automated dependency updates via Dependabot
- Security-focused dependency scanning
- Automated vulnerability alerts

### Container Security

- Regular base image updates
- Multi-stage builds for minimal attack surface
- Non-root user execution
- Security scanning in CI/CD

## Reporting and Monitoring

### Security Dashboard

Access security information through:

- **GitHub Security Tab**: Centralized vulnerability view
- **CI/CD Artifacts**: Detailed scan reports
- **Local Reports**: Generated security summaries

### Metrics and KPIs

- Vulnerability detection rate
- Mean time to remediation
- Security scan coverage
- False positive rate

## Best Practices

### Development

1. **Secure Coding**: Follow OWASP guidelines
2. **Dependency Management**: Regular updates and vulnerability monitoring
3. **Secret Management**: No hardcoded secrets, use environment variables
4. **Input Validation**: Sanitize all external inputs
5. **Error Handling**: Avoid information disclosure

### Infrastructure

1. **Container Security**: Minimal base images, regular updates
2. **Network Security**: Principle of least privilege
3. **Access Control**: Role-based permissions
4. **Monitoring**: Comprehensive logging and alerting

### CI/CD

1. **Automated Scanning**: Every commit and PR
2. **Quality Gates**: Block deployments on critical issues
3. **SARIF Integration**: Standardized reporting
4. **Artifact Security**: Signed releases and SBOMs

## Troubleshooting

### Common Issues

**Bandit False Positives**:
```python
# Use # nosec comment for false positives
password = get_password_from_env()  # nosec B105
```

**Safety Dependency Conflicts**:
```bash
# Update specific package
pip install --upgrade package-name
```

**Container Scan Failures**:
```bash
# Check Docker daemon
docker info

# Rebuild image
docker build --no-cache -t myimage:latest .
```

### Getting Help

- Review security scan reports in CI/CD artifacts
- Check GitHub Security tab for detailed findings
- Consult tool-specific documentation
- Contact security team for critical issues

## References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [SARIF Specification](https://sarifweb.azurewebsites.net/)
- [SPDX Specification](https://spdx.dev/)
- [CycloneDX Specification](https://cyclonedx.org/)
- [GitHub Security Features](https://docs.github.com/en/code-security)
