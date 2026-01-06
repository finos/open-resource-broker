# Contributing to Open Resource Broker

Thank you for your interest in contributing to the Open Resource Broker! This guide will help you get started with development and testing.

## Development Setup

### Prerequisites

- Python 3.10+ (tested on 3.10, 3.11, 3.12, 3.13, 3.14)
- Docker and Docker Compose
- AWS CLI (for AWS provider testing)

### Quick Setup with UV (Recommended)

```bash
# Clone repository
git clone https://github.com/awslabs/open-resource-broker.git
cd open-resource-broker

# Fast development setup with uv
make dev-install-uv

# Or manually with uv
uv pip install -e ".[dev]"
```

### Traditional Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install development dependencies
make dev-install-pip
```

## Testing

### Local Testing

```bash
# Run all tests
make test

# Run with coverage
make test-coverage

# Run specific test categories
make test-unit
make test-integration
```

### CI/CD Pipeline

The project uses a multi-stage CI/CD pipeline:

```
Push → Quality Gates → Development Artifacts → Release Decision → Production Artifacts
```

**Development Workflow:**
1. Push to main → Quality gates run (tests, linting, security)
2. If quality passes → Development artifacts build (TestPyPI, dev containers)
3. Commit with "release:" prefix → Semantic-release creates GitHub release
4. Release event → Production artifacts build (PyPI, production containers)

**To create a release:**
```bash
# Make changes with conventional commits
git commit -m "feat: add new feature"
git commit -m "fix: resolve bug"

# When ready to release
git commit -m "release: version with new features and fixes"
git push origin main
```

**Version calculation:**
- `fix:` → patch version (1.0.1)
- `feat:` → minor version (1.1.0)  
- `BREAKING CHANGE:` → major version (2.0.0)

### Artifact Locations

**Development Artifacts:**
- TestPyPI: `test.pypi.org/project/open-resource-broker`
- Containers: `ghcr.io/awslabs/open-resource-broker:main`

**Production Artifacts:**
- PyPI: `pypi.org/project/open-resource-broker`
- Containers: `ghcr.io/awslabs/open-resource-broker:latest`

### Automated Publishing

The project uses a secure three-tier publishing strategy:

1. **PR Comments** (`/package`) → TestPyPI only with dev versions
2. **Merge to main/develop** → TestPyPI with dev versions
3. **GitHub Releases** → Production PyPI with release versions

**Security**: Comment triggers and branch pushes can never publish to production PyPI.

## Code Quality

### Formatting and Linting

We use Ruff for code formatting and linting (replaces Black, isort, flake8, pylint).

Since pre-commit hooks cannot be installed due to git configuration:

1. **Enable format-on-save** in your IDE (see .vscode/settings.json)
2. **Run before committing**: `make pre-commit`
3. **Let CI auto-format**: If you forget, CI will auto-format and commit

### IDE Setup
- **VS Code**: Install Ruff extension, settings already configured
- **PyCharm**: Install Ruff plugin, enable format-on-save

```bash
# Format code (auto-fix what can be fixed)
make format

# Check code quality (enforced rules)
make lint

# Check extended rules (warnings only)
make lint-optional

# Run all pre-commit checks locally
make pre-commit

# Type checking
make type-check
```

### Pre-commit Hooks

```bash
# Install pre-commit hooks
pre-commit install

# Run on all files
pre-commit run --all-files
```

## Architecture

The plugin follows Clean Architecture principles:

- **Domain Layer**: Core business logic (`src/domain/`)
- **Application Layer**: Use cases and handlers (`src/application/`)
- **Infrastructure Layer**: External integrations (`src/infrastructure/`)
- **Interface Layer**: CLI and API (`src/interface/`, `src/api/`)

### Key Patterns

- **CQRS**: Command Query Responsibility Segregation
- **DDD**: Domain-Driven Design with rich domain models
- **Dependency Injection**: Comprehensive DI container
- **Strategy Pattern**: Pluggable provider implementations

## Pull Request Guidelines

### Before Submitting

1. **Run tests locally**: `make test`
2. **Run pre-commit checks**: `make pre-commit`
3. **Update documentation** if needed
4. **Add tests** for new functionality

### PR Description

Please include:
- **What**: Brief description of changes
- **Why**: Motivation and context
- **How**: Implementation approach
- **Testing**: How you tested the changes

### Testing Your PR

Use comment commands to test your changes:

```bash
# Test the full CI pipeline
/test

# Build and test package installation
/package
```

The bot will add a reaction to confirm the command was received.

## Container Development

### Building Containers

```bash
# Build container locally
make container-build

# Test container
make container-test
```

### Container Commands

The container supports multiple entry points:

```bash
# CLI usage
docker run --rm image --version

# API server
docker run -p 8000:8000 image system serve

# Health check
docker run --rm image --health
```

## Documentation

### Building Docs

```bash
# Build documentation
make docs-build

# Serve locally
make docs-serve
```

### Documentation Structure

- `README.md` - Main project documentation
- `docs/` - Detailed documentation
- `CONTRIBUTING.md` - This file
- `docs/deployment/` - Deployment guides

## Release Management

The project uses automated release management with semantic versioning and pre-release support. All releases are created through Makefile targets that handle version bumping, validation, and GitHub release creation.

### Quick Reference

```bash
# Standard releases
make release-patch              # 1.0.0 -> 1.0.1
make release-minor              # 1.0.0 -> 1.1.0
make release-major              # 1.0.0 -> 2.0.0

# Pre-releases
make release-minor-alpha        # 1.0.0 -> 1.1.0-alpha.1
make release-patch-beta         # 1.0.0 -> 1.0.1-beta.1
make release-major-rc           # 1.0.0 -> 2.0.0-rc.1

# Promotions
make promote-alpha              # 1.1.0-alpha.1 -> 1.1.0-alpha.2
make promote-beta               # 1.1.0-alpha.2 -> 1.1.0-beta.1
make promote-stable             # 1.1.0-rc.1 -> 1.1.0

# Custom releases
RELEASE_VERSION=1.5.0 make release-version
DRY_RUN=true make release-minor # Test without changes
```

### Environment Variables

- **`RELEASE_VERSION`**: Override version (use with `release-version`/`release-backfill`)
- **`FROM_COMMIT`**: Start commit (optional, smart defaults)
- **`TO_COMMIT`**: End commit (optional, defaults to HEAD)
- **`DRY_RUN`**: Test mode without making changes
- **`ALLOW_BACKFILL`**: Enable non-linear releases

### Pre-release Workflow

1. **Alpha**: `make release-minor-alpha` - Internal testing
2. **Beta**: `make promote-beta` - External testing
3. **RC**: `make promote-rc` - Final testing
4. **Stable**: `make promote-stable` - Production release

### Validation

The system automatically validates:
- Working directory cleanliness
- Commit range validity
- Tag conflicts (prevents duplicates)
- Release overlap detection

### Integration

Releases automatically trigger:
- PyPI publishing (stable and pre-releases)
- Container registry publishing
- Documentation deployment

For complete documentation, see [Release Management Guide](docs/docs/developer_guide/releases.md).

## Release Process

### Version Management

- **Development**: `0.1.0.dev20250818125457+abc1234`
- **Release Candidates**: `0.1.0rc1`
- **Releases**: `0.1.0`

### Creating Releases

1. **Update version** in `.project.yml`
2. **Create GitHub release** with tag `v0.1.0`
3. **Automatic publishing** to PyPI via trusted publishing

## Security

### Trusted Publishing

The project uses PyPI Trusted Publishing (OIDC) instead of API tokens:

- **No secrets to manage** - authentication via GitHub OIDC
- **Automatic attestations** - digital signatures for packages
- **Environment protection** - optional approval workflows

### Reporting Security Issues

Please see our [Security Policy](SECURITY.md) for responsible disclosure procedures.

## Getting Help

- **Documentation**: Comprehensive guides in `docs/`
- **Issues**: GitHub Issues for bug reports and feature requests
- **Discussions**: Community discussions and questions

## Code of Conduct

This project follows the [AWS Open Source Code of Conduct](https://aws.github.io/code-of-conduct).

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
