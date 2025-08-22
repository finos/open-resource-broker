# Contributing to Open Host Factory Plugin

Thank you for your interest in contributing to the Open Host Factory Plugin! This guide will help you get started with development and testing.

## Development Setup

### Prerequisites

- Python 3.9+ (tested on 3.9, 3.10, 3.11, 3.12, 3.13)
- Docker and Docker Compose
- AWS CLI (for AWS provider testing)

### Quick Setup with UV (Recommended)

```bash
# Clone repository
git clone https://github.com/awslabs/open-hostfactory-plugin.git
cd open-hostfactory-plugin

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

### PR Comment Commands

You can trigger CI/CD actions by commenting on pull requests:

**Security Note**: Comment triggers are restricted to repository members, owners, and collaborators.

#### Trigger Word Configuration

The trigger words are configurable via environment variables in each workflow:

- **CI Pipeline**: `CI_TRIGGERS="/test,/build,/ci"`
- **Publishing**: `PUBLISH_TRIGGERS="/package"`  
- **Security Scans**: `SECURITY_TRIGGERS="/security"`
- **Container Builds**: `CONTAINER_TRIGGERS="/container"`

To modify trigger words, update the `env` section in the respective workflow files.

#### Testing Commands (Members, Owners, Collaborators)
- **`/test`** - Run full CI pipeline (tests, linting, type checking)
- **`/build`** - Run CI pipeline including package build verification
- **`/ci`** - Same as `/test` - run complete CI pipeline
- **`/security`** - Run security scans
- **`/container`** - Build container images (no registry push for security)

#### Publishing Commands (Members, Owners only)
- **`/package`** - Build and publish to TestPyPI only (never production PyPI)
  - Creates a dev version: `0.1.0.dev20250818125457+abc1234`
  - Publishes to https://test.pypi.org for installation testing
  - Use: `pip install --index-url https://test.pypi.org/simple/ open-hostfactory-plugin`

**Security Note**: Comment triggers can never publish to production PyPI. Only GitHub releases publish to PyPI.

Commands must be exact matches (e.g., `/test` not `/test please`).

### Version Alignment

Package and container versions are aligned:

- **Development**: `0.1.0.dev20250818125457+abc1234` (both PyPI package and container)
- **Release**: `0.1.0` (both PyPI package and container)

### Container Security

- **Comment triggers** (`/container`) - Build only, no registry push
- **Branch pushes** - Build and push with dev tags  
- **Releases** - Build and push with release tags (`latest`, `v0.1.0`)

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
