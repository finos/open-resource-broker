# Changelog Management

Comprehensive guide to automated changelog management using conventional commits and git-changelog.

## Overview

The project uses automated changelog generation based on:
- **Conventional Commits**: Structured commit messages (feat:, fix:, docs:, etc.)
- **git-changelog**: Python tool for generating Keep a Changelog format
- **Automated Integration**: Updates during release process
- **Full Lifecycle Support**: Creation, updates, backfills, deletions

## Quick Start

### Basic Usage

```bash
# Preview changes for current branch
make changelog-preview

# Update changelog for release
make changelog-update

# Validate changelog format
make changelog-validate

# Regenerate entire changelog
make changelog-regenerate
```

### Release Integration

Changelog is automatically updated during releases:

```bash
# Standard releases (includes changelog update)
make release-minor
make release-patch
make release-major

# Pre-releases (includes changelog update)
make release-minor-alpha
make release-patch-beta
make release-minor-rc
```

## Conventional Commits

The changelog is generated from conventional commit messages:

### Supported Types

| Type | Changelog Section | Description |
|------|------------------|-------------|
| `feat:` | Added | New features |
| `fix:` | Fixed | Bug fixes |
| `docs:` | Documentation | Documentation changes |
| `style:` | Style | Code style changes |
| `refactor:` | Changed | Code refactoring |
| `perf:` | Performance | Performance improvements |
| `test:` | Tests | Test changes |
| `build:` | Build | Build system changes |
| `ci:` | CI/CD | CI/CD changes |
| `chore:` | Maintenance | Maintenance tasks |
| `revert:` | Reverted | Reverted changes |

### Examples

```bash
# Good commit messages
git commit -m "feat: add MCP server support"
git commit -m "fix: resolve container build issue"
git commit -m "docs: update API documentation"
git commit -m "refactor: modernize UV tooling"

# With scope and breaking change
git commit -m "feat(api)!: redesign authentication system

BREAKING CHANGE: API authentication now requires JWT tokens"
```

## Advanced Scenarios

### Backfill Releases

For creating releases from historical commits:

```bash
# Create backfill release with changelog
make release-backfill-with-changelog \
  VERSION=v1.2.3 \
  FROM_COMMIT=abc123 \
  TO_COMMIT=def456
```

This will:
1. Generate changelog for the specific commit range
2. Insert it in chronological order
3. Create the GitHub release
4. Regenerate full changelog to maintain order

### Release Deletion

To delete a release and clean up changelog:

```bash
# Delete release completely
make release-delete VERSION=v1.2.3
```

This will:
1. Delete GitHub release
2. Delete git tag (local and remote)
3. Remove changelog entry
4. Commit changes

### Manual Changelog Fixes

If changelog gets out of sync:

```bash
# Regenerate entire changelog from git history
make changelog-regenerate

# This will:
# 1. Generate fresh changelog from all commits
# 2. Maintain chronological order
# 3. Commit changes
```

## Configuration

### git-changelog Configuration

Configuration in `.git-changelog.toml`:

```toml
[tool.git-changelog]
repository = "awslabs/open-resource-broker"
template = ".changelog-template.md"
output = "CHANGELOG.md"
convention = "conventional"

sections = [
    { name = "feat", title = "Added" },
    { name = "fix", title = "Fixed" },
    # ... more sections
]
```

### Custom Template

Template in `.changelog-template.md` follows Keep a Changelog format with:
- Unreleased section for ongoing changes
- Version sections with dates
- Categorized changes by type
- GitHub comparison links

## Workflow Integration

### GitHub Actions

Changelog validation runs on:
- Pull requests touching changelog files
- Pushes to main branch
- Manual workflow dispatch

Validation includes:
- Format checking
- Content validation
- Sync verification with git history
- Preview generation for PRs

### Release Workflow

During releases:
1. **Update**: Changelog updated with new version
2. **Commit**: Changes committed to repository
3. **Release**: GitHub release created with changelog content
4. **Validation**: CI validates changelog format

## Troubleshooting

### Common Issues

**Changelog out of sync:**
```bash
make changelog-regenerate
```

**Missing dependencies:**
```bash
make changelog-install-deps
```

**Invalid format:**
```bash
make changelog-validate
```

**Preview changes:**
```bash
make changelog-preview
```

### Manual Fixes

If automatic generation fails:

1. **Check commit messages**: Ensure conventional format
2. **Validate configuration**: Check `.git-changelog.toml`
3. **Review template**: Verify `.changelog-template.md`
4. **Regenerate**: Use `make changelog-regenerate`

### Debugging

Enable debug output:

```bash
# Verbose changelog generation
DEBUG=true make changelog-generate

# Check dependencies
make changelog-check-deps

# Show status
make changelog-status
```

## Best Practices

### Commit Messages

1. **Use conventional format**: `type(scope): description`
2. **Be descriptive**: Clear, concise descriptions
3. **Include breaking changes**: Use `!` and `BREAKING CHANGE:`
4. **Group related changes**: Logical commit boundaries

### Release Process

1. **Preview first**: Always preview changes before release
2. **Validate format**: Run validation before committing
3. **Review generated content**: Check changelog accuracy
4. **Test backfills**: Verify chronological order

### Maintenance

1. **Regular validation**: Run `make changelog-validate` in CI
2. **Sync checks**: Monitor changelog sync with git history
3. **Template updates**: Keep template current with project needs
4. **Dependency updates**: Keep git-changelog updated

## Integration Examples

### Local Development

```bash
# Before creating PR
make changelog-preview

# Before release
make changelog-validate
make changelog-update
```

### CI/CD Pipeline

```yaml
# In GitHub Actions
- name: Validate changelog
  run: make changelog-validate

- name: Update changelog for release
  run: make changelog-update
```

### Release Automation

```bash
# Full release with changelog
make release-minor
# Includes: changelog-update → version-bump → create-release → changelog-commit
```

This comprehensive system ensures accurate, automated changelog maintenance throughout the project lifecycle.
