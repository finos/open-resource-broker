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
git clone https://github.com/finos/open-resource-broker.git
cd open-resource-broker

# Fast development setup with uv
make dev-install-uv

# Or manually with uv
uv pip install -e ".[dev]"

# Setup git hooks (required for beads integration)
git config core.hooksPath .githooks
```

### Traditional Setup

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install with all development dependencies
make dev-install-pip

# Or manually
pip install -e ".[dev]"

# Setup git hooks (required for beads integration)
git config core.hooksPath .githooks
```

### Optional Dependencies

The package supports several optional feature groups:

```bash
# Minimal install (CLI only, 10 dependencies)
pip install -e .

# With CLI colors
pip install -e ".[cli]"

# With API server
pip install -e ".[api]"

# With monitoring
pip install -e ".[monitoring]"

# All features
pip install -e ".[all]"

# Development (includes all features + dev tools)
pip install -e ".[dev]"
```

**Feature Groups:**
- `[cli]`: Rich console output (rich, rich-argparse)
- `[api]`: REST API server (fastapi, uvicorn, jinja2)
- `[monitoring]`: Observability (opentelemetry, prometheus, psutil)
- `[dev]`: Development tools (pytest, ruff, mypy, etc.)
- `[all]`: All optional features (cli + api + monitoring)

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

### Commit Message Format

Follow conventional commits format (simplified):

```
type: description

[optional body with details]
```

**Types:**
- `feat:` - New feature or capability
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `refactor:` - Code restructuring without behavior change
- `test:` - Test additions or fixes
- `chore:` - Maintenance tasks (dependencies, tooling)
- `perf:` - Performance improvements

**Rules:**
- No scope in parentheses (keep simple)
- No task IDs or internal tracking references
- Description is lowercase, no period at end
- Body explains what/why, not how
- Keep commits focused and atomic

**Examples:**
```bash
git commit -m "fix: requests show --all helpful error"
git commit -m "feat: add LAUNCHING state to machine lifecycle"
git commit -m "docs: update CLI usage examples"
git commit -m "refactor: extract validation logic to separate service"
```

### Artifact Locations

**Development Artifacts:**
- TestPyPI: `test.pypi.org/project/open-resource-broker`
- Containers: `ghcr.io/finos/open-resource-broker:main`

**Production Artifacts:**
- PyPI: `pypi.org/project/open-resource-broker`
- Containers: `ghcr.io/finos/open-resource-broker:latest`

### Automated Publishing

The project uses a secure three-tier publishing strategy:

1. **PR Comments** (`/package`) → TestPyPI only with dev versions
2. **Merge to main/develop** → TestPyPI with dev versions
3. **GitHub Releases** → Production PyPI with release versions

**Security**: Comment triggers and branch pushes can never publish to production PyPI.

## Code Quality

### Running Checks Locally

Before pushing, run the full quality gate suite:

```bash
# 1. Type checking (must pass — enforced in CI)
make ci-quality-pyright
# or: uv run pyright src/

# 2. Linting (must pass — enforced in CI)
make ci-quality-ruff
# or: uv run ruff check src/

# 3. Architecture checks (must pass — enforced in CI)
make ci-arch-clean        # Clean Architecture layer boundaries
make ci-arch-cqrs         # CQRS pattern compliance
make ci-arch-imports      # Import validation
make ci-arch-lint-imports # import-linter layer-boundary contracts
# or: uv run ./dev-tools/scripts/check_architecture.py
#     uv run ./dev-tools/scripts/validate_cqrs.py
#     uv run lint-imports

# 4. Unit tests (must pass — enforced in CI)
make ci-tests-unit
# or: uv run pytest tests/ --ignore=tests/onaws -q

# Run everything in one shot
make ci-check
```

### Layer-Boundary Contracts (import-linter)

Clean Architecture layering is enforced statically by
[import-linter](https://import-linter.readthedocs.io/). The contracts live in
`.importlinter` at the repo root and are checked in CI (the `lint-imports` leg
of the architecture-validation matrix) and locally via
`make ci-arch-lint-imports`.

Three `forbidden` contracts guard the dependency direction:

- **Domain layer is clean** — `orb.domain` must not import from
  `orb.application`, `orb.infrastructure`, `orb.interface`, `orb.providers`,
  `orb.monitoring`, `orb.cli`, or `argparse`. The domain holds pure business
  logic and stays free of outer layers and CLI frameworks.
- **Application does not import infrastructure concretes** — `orb.application`
  must not import `orb.infrastructure`, `orb.providers`, or `orb.monitoring`.
  Handlers depend on abstract ports only; concretes are wired via DI.
- **Infrastructure does not import upward** — `orb.infrastructure` must not
  import `orb.interface` or `orb.cli`.

The contracts are currently **green with zero violations** — the tree already
satisfies them. Treat any new failure as a real layering regression: fix the
offending import (invert the dependency, move the shared type to a lower layer,
or inject via a port) rather than relaxing the contract.

### Formatting and Linting

We use Ruff for code formatting and linting (replaces Black, isort, flake8, pylint).

```bash
# Format code (auto-fix what can be fixed)
make format

# Check code quality (enforced rules)
make lint

# Check extended rules (warnings only)
make lint-optional
```

### Pre-commit Hooks

Git hooks live in `.githooks/` and are activated by:

```bash
git config core.hooksPath .githooks
```

`git commit` automatically runs all non-manual checks via the pre-commit tool (reads
`.pre-commit-config.yaml`, only checks staged files, skips slow/manual-stage hooks).

To run the full suite including security scans and other slow checks:

```bash
make pre-commit-full
```

To run only the standard (non-manual) checks manually:

```bash
make pre-commit
# or directly:
pre-commit run --all-files
```

Hooks that run on every commit (enforced):
- `format-fix` — auto-formats Python with Ruff
- `ruff-check` — linting (required rules)
- `pyright` — type checking
- `validate-cqrs` — CQRS pattern compliance
- `check-architecture` — Clean Architecture layer boundaries
- `validate-imports` — import validation

Additional hooks run via `make pre-commit-full` (manual stage):
- `bandit` — security analysis
- `detect-secrets` — hardcoded secret detection
- `validate-workflows` — GitHub Actions YAML validation

### IDE Setup
- **VS Code**: Install Ruff extension, settings already configured
- **PyCharm**: Install Ruff plugin, enable format-on-save

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
2. **Run pre-commit checks**: `make pre-commit-full`
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
docker run -p 8000:8000 image server start --foreground

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

## Governance

### Roles

The project community consists of Contributors and Maintainers:
* A **Contributor** is anyone who submits a contribution to the project. (Contributions may include code, issues, comments, documentation, media, or any combination of the above.)
* A **Maintainer** is a Contributor who, by virtue of their contribution history, has been given write access to project repositories and may merge approved contributions.
* The **Lead Maintainer** is the project's interface with the FINOS team and Board. They are responsible for approving [quarterly project reports](https://community.finos.org/docs/governance/#project-governing-board-reporting) and communicating on behalf of the project. The Lead Maintainer is elected by a vote of the Maintainers.

### Contribution Rules

Anyone is welcome to submit a contribution to the project. The rules below apply to all contributions. (The key words "MUST", "SHALL", "SHOULD", "MAY", etc. in this document are to be interpreted as described in [IETF RFC 2119](https://www.ietf.org/rfc/rfc2119.txt).)

* All contributions MUST be submitted as pull requests, including contributions by Maintainers.
* All pull requests SHOULD be reviewed by a Maintainer (other than the Contributor) before being merged.
* Pull requests for non-trivial contributions SHOULD remain open for a review period sufficient to give all Maintainers a sufficient opportunity to review and comment on them.
* After the review period, if no Maintainer has an objection to the pull request, any Maintainer MAY merge it.
* If any Maintainer objects to a pull request, the Maintainers SHOULD try to come to consensus through discussion. If not consensus can be reached, any Maintainer MAY call for a vote on the contribution.

### Maintainer Voting

The Maintainers MAY hold votes only when they are unable to reach consensus on an issue. Any Maintainer MAY call a vote on a contested issue, after which Maintainers SHALL have 36 hours to register their votes. Votes SHALL take the form of "+1" (agree), "-1" (disagree), "+0" (abstain). Issues SHALL be decided by the majority of votes cast. If there is only one Maintainer, they SHALL decide any issue otherwise requiring a Maintainer vote. If a vote is tied, the Lead Maintainer MAY cast an additional tie-breaker vote.

The Maintainers SHALL decide the following matters by consensus or, if necessary, a vote:
* Contested pull requests
* Election and removal of the Lead Maintainer
* Election and removal of Maintainers

All Maintainer votes MUST be carried out transparently, with all discussion and voting occurring in public, either:
* in comments associated with the relevant issue or pull request, if applicable;
* on the project mailing list or other official public communication channel; or
* during a regular, minuted project meeting.

### Maintainer Qualifications

Any Contributor who has made a substantial contribution to the project MAY apply (or be nominated) to become a Maintainer. The existing Maintainers SHALL decide whether to approve the nomination according to the Maintainer Voting process above.

### Changes to this Document

This document MAY be amended by a vote of the Maintainers according to the Maintainer Voting process above.

The current list of maintainers is in [MAINTAINERS.md](MAINTAINERS.md).

## Code of Conduct

This project follows the [AWS Open Source Code of Conduct](https://aws.github.io/code-of-conduct).

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.
