# Changelog management targets

# @SECTION Changelog Management
changelog-generate: dev-install  ## Generate full changelog from git history
	@echo "Generating full changelog from git history..."
	./dev-tools/release/changelog_manager.py generate

changelog-update: dev-install  ## Update changelog for current version
	@echo "Updating changelog for version $(VERSION)..."
	./dev-tools/release/changelog_manager.py update --version $(VERSION)

changelog-validate: dev-install  ## Validate changelog format and content
	@echo "Validating changelog format..."
	./dev-tools/release/changelog_manager.py validate

changelog-preview: dev-install  ## Preview changelog changes for current commits
	@echo "Previewing changelog changes..."
	./dev-tools/release/changelog_manager.py preview

changelog-delete: dev-install  ## Delete version from changelog (usage: make changelog-delete VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make changelog-delete VERSION=1.0.0"; \
		exit 1; \
	fi
	@echo "Deleting version $(VERSION) from changelog..."
	./dev-tools/release/changelog_manager.py delete --version $(VERSION)

changelog-backfill: dev-install  ## Handle backfill release in changelog
	@echo "Handling backfill release in changelog..."
	./dev-tools/release/changelog_manager.py backfill

changelog-regenerate: dev-install  ## Regenerate entire changelog (destructive)
	@echo "WARNING: This will regenerate the entire changelog from git history"
	@echo "This is destructive and will overwrite manual changes"
	@read -p "Are you sure? (y/N): " confirm && [ "$$confirm" = "y" ]
	./dev-tools/release/changelog_manager.py generate --force

changelog-sync-check: dev-install  ## Check if changelog is in sync with git history
	@echo "Checking changelog synchronization..."
	./dev-tools/release/changelog_manager.py validate --sync-check

# @SECTION Release Notes Management
release-notes-generate: dev-install  ## Generate release notes for current version
	@echo "Generating release notes for version $(VERSION)..."
	./dev-tools/release/changelog_manager.py update $(VERSION)

release-notes-preview: dev-install  ## Preview release notes for current version
	@echo "Previewing release notes for version $(VERSION)..."
	./dev-tools/release/release_notes.sh $(VERSION) --preview

release-backfill: dev-install  ## Backfill historical releases
	@echo "Backfilling historical releases..."
	./dev-tools/release/release_backfill.sh

# @SECTION Git and Release Utilities
git-changelog-since: dev-install  ## Show git log since last release (for manual changelog)
	@echo "Git commits since last release:"
	@last_tag=$$(git describe --tags --abbrev=0 2>/dev/null || echo "HEAD~10"); \
	git log --oneline --no-merges "$${last_tag}..HEAD" || \
	git log --oneline --no-merges -10

git-unreleased-commits: dev-install  ## Count unreleased commits
	@last_tag=$$(git describe --tags --abbrev=0 2>/dev/null || echo "HEAD~10"); \
	count=$$(git rev-list --count "$${last_tag}..HEAD" 2>/dev/null || git rev-list --count HEAD~10..HEAD); \
	echo "Unreleased commits: $$count"

git-last-release: dev-install  ## Show information about last release
	@echo "Last release information:"
	@git describe --tags --abbrev=0 2>/dev/null || echo "No releases found"
	@last_tag=$$(git describe --tags --abbrev=0 2>/dev/null); \
	if [ -n "$$last_tag" ]; then \
		echo "Release date: $$(git log -1 --format=%ci $$last_tag)"; \
		echo "Commits since: $$(git rev-list --count $$last_tag..HEAD)"; \
	fi
