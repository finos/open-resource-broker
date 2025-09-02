# Changelog Management Targets
# Comprehensive changelog automation with git-changelog

# @SECTION Changelog Management

changelog-generate: dev-install changelog-install-deps  ## Generate full changelog from git history
	@echo "Generating complete changelog from git history..."
	@python3 dev-tools/release/changelog_manager.py generate
	@echo "Changelog generated: CHANGELOG.md"

changelog-update: dev-install changelog-install-deps  ## Update changelog for current version
	@echo "Updating changelog for release..."
	@$(eval VERSION := $(shell make -s get-version))
	@python3 dev-tools/release/changelog_manager.py update "v$(VERSION)"
	@echo "Changelog updated for v$(VERSION)"

changelog-preview: dev-install changelog-install-deps  ## Preview changelog changes
	@echo "Previewing changelog changes..."
	@python3 dev-tools/release/changelog_manager.py preview

changelog-validate: dev-install changelog-install-deps  ## Validate changelog format and content
	@echo "Validating changelog..."
	@python3 dev-tools/release/changelog_manager.py validate
	@echo "Changelog validation passed"

changelog-regenerate: dev-install  ## Regenerate entire changelog (use after backfills/deletions)
	@echo "Regenerating complete changelog..."
	@python3 dev-tools/release/changelog_manager.py generate
	@git add CHANGELOG.md
	@if ! git diff --cached --quiet; then \
		git commit -m "docs: regenerate changelog from git history"; \
		echo "Changelog regenerated and committed"; \
	else \
		echo "Changelog regenerated (no changes)"; \
	fi

# Release deletion with changelog cleanup
release-delete:  ## Delete release and update changelog (VERSION=v1.2.3)
	@if [ -z "$(VERSION)" ]; then \
		echo "Usage: make release-delete VERSION=v1.2.3"; \
		exit 1; \
	fi
	@echo "Deleting release $(VERSION)..."
	@./dev-tools/release/delete_release.sh "$(VERSION)"
	@echo "✓ Release $(VERSION) deleted"

# Backfill release with changelog handling
release-backfill-with-changelog: dev-install  ## Create backfill release with changelog (VERSION=v1.2.3 FROM_COMMIT=abc TO_COMMIT=def)
	@if [ -z "$(VERSION)" ] || [ -z "$(FROM_COMMIT)" ] || [ -z "$(TO_COMMIT)" ]; then \
		echo "Usage: make release-backfill-with-changelog VERSION=v1.2.3 FROM_COMMIT=abc123 TO_COMMIT=def456"; \
		exit 1; \
	fi
	@echo "Creating backfill release $(VERSION)..."
	@python3 dev-tools/release/changelog_manager.py backfill "$(VERSION)" "$(FROM_COMMIT)" "$(TO_COMMIT)"
	@ALLOW_BACKFILL=true FROM_COMMIT="$(FROM_COMMIT)" TO_COMMIT="$(TO_COMMIT)" ./dev-tools/release/release_creator.sh
	@echo "✓ Backfill release $(VERSION) created with changelog"

# Development helpers
changelog-install-deps:  ## Install changelog dependencies
	@echo "Installing changelog dependencies..."
	@if command -v uv >/dev/null 2>&1; then \
		uv pip install git-changelog; \
	else \
		pip install git-changelog; \
	fi
	@echo "git-changelog installed"

changelog-setup:  ## Setup changelog configuration and templates
	@echo "Setting up changelog configuration..."
	@if [ ! -f .git-changelog.toml ]; then \
		echo "✓ Changelog configuration already exists"; \
	fi
	@if [ ! -f .changelog-template.md ]; then \
		echo "✓ Changelog template already exists"; \
	fi
	@echo "✓ Changelog setup complete"

# Integration with existing release targets
changelog-commit:  ## Commit changelog changes
	@if git diff --quiet CHANGELOG.md; then \
		echo "No changelog changes to commit"; \
	else \
		echo "Committing changelog changes..."; \
		git add CHANGELOG.md; \
		git commit -m "docs: update changelog for v$(shell make -s get-version)"; \
		echo "✓ Changelog changes committed"; \
	fi

# Validation helpers
changelog-check-deps:  ## Check if changelog dependencies are installed
	@echo "Checking changelog dependencies..."
	@python3 -c "import git_changelog" 2>/dev/null || (echo "❌ git-changelog not installed. Run: make changelog-install-deps" && exit 1)
	@echo "✓ All changelog dependencies available"

changelog-status:  ## Show changelog status and recent changes
	@echo "=== CHANGELOG STATUS ==="
	@if [ -f CHANGELOG.md ]; then \
		echo "✓ CHANGELOG.md exists"; \
		echo "Size: $$(wc -l CHANGELOG.md | awk '{print $$1}') lines"; \
		echo "Last modified: $$(stat -f "%Sm" -t "%Y-%m-%d %H:%M" CHANGELOG.md 2>/dev/null || stat -c "%y" CHANGELOG.md 2>/dev/null || echo "unknown")"; \
	else \
		echo "❌ CHANGELOG.md does not exist"; \
	fi
	@echo ""
	@echo "=== RECENT UNRELEASED CHANGES ==="
	@python3 dev-tools/release/changelog_manager.py preview --from-commit $$(git tag -l "v*" --sort=-version:refname | head -1) 2>/dev/null || echo "No recent changes"
