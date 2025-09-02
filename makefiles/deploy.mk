# Deployment targets
# Build, publish, containers, releases, and documentation

# @SECTION Build & Deploy
build: clean dev-install  ## Build package
	VERSION=$${VERSION:-$$(make -s get-version)} $(MAKE) generate-pyproject && \
	VERSION=$${VERSION:-$$(make -s get-version)} BUILD_ARGS="$(BUILD_ARGS)" ./dev-tools/package/build.sh

build-test: build  ## Build and test package installation
	./dev-tools/package/test_install.sh

build-historical:  ## Build historical release (usage: make build-historical COMMIT=abc123 VERSION=0.0.1)
	@if [ -z "$(COMMIT)" ] || [ -z "$(VERSION)" ]; then \
		echo "Usage: make build-historical COMMIT=<hash> VERSION=<version>"; \
		exit 1; \
	fi
	@./dev-tools/release/historical_release.sh $(COMMIT) $(VERSION)

publish: build  ## Publish to PyPI (interactive)
	./dev-tools/package/publish.sh pypi

publish-test: build  ## Publish to test PyPI
	./dev-tools/package/publish.sh testpypi

# @SECTION Container targets
container-build:  ## Build Docker image
	REGISTRY=$(CONTAINER_REGISTRY) \
	VERSION=$${VERSION:-$$(make -s get-version)} \
	IMAGE_NAME=$(CONTAINER_IMAGE) \
	./dev-tools/scripts/container_build.sh

container-run:  ## Run Docker container
	docker run -p 8000:8000 $(PROJECT):latest

docker-compose-up:  ## Start with docker-compose
	docker-compose -f deployment/docker/docker-compose.yml up -d

docker-compose-down:  ## Stop docker-compose
	docker-compose -f deployment/docker/docker-compose.yml down

# Container build targets (multi-Python support)
container-build-multi: dev-install  ## Build container images for all Python versions
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "Building container for Python $$py_ver..."; \
		REGISTRY=$(CONTAINER_REGISTRY) \
		VERSION=$(VERSION) \
		IMAGE_NAME=$(CONTAINER_IMAGE) \
		PYTHON_VERSION=$$py_ver \
		MULTI_PYTHON=true \
		./dev-tools/scripts/container_build.sh; \
	done
	@echo "Tagging default Python $(DEFAULT_PYTHON_VERSION) as latest..."
	@docker tag $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$(DEFAULT_PYTHON_VERSION) $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)

container-build-single: dev-install  ## Build container image for single Python version (usage: make container-build-single PYTHON_VERSION=3.11)
	@if [ -z "$(PYTHON_VERSION)" ]; then \
		echo "Error: PYTHON_VERSION is required. Usage: make container-build-single PYTHON_VERSION=3.11"; \
		exit 1; \
	fi
	REGISTRY=$(CONTAINER_REGISTRY) \
	VERSION=$${VERSION:-$$(make -s get-version)} \
	CONTAINER_TAG_PREFIX=$${CONTAINER_TAG_PREFIX:-} \
	IMAGE_NAME=$(CONTAINER_IMAGE) \
	PYTHON_VERSION=$(PYTHON_VERSION) \
	MULTI_PYTHON=true \
	./dev-tools/scripts/container_build.sh

container-push-multi: container-build-multi  ## Push all container images to registry
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "Pushing $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver"; \
		docker push $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver; \
	done
	@echo "Pushing $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)"
	@docker push $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)

container-show-version:  ## Show current version and tags that would be created
	@echo "Package & Version Information"
	@echo "============================="
	@echo "Package Name: $(CONTAINER_IMAGE)"
	@echo "Version: $(VERSION)"
	@echo "Registry: $(CONTAINER_REGISTRY)"
	@echo "Python Versions: $(PYTHON_VERSIONS)"
	@echo "Default Python: $(DEFAULT_PYTHON_VERSION)"
	@echo ""
	@echo "Container tags that would be created:"
	@for py_ver in $(PYTHON_VERSIONS); do \
		echo "  - $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$$py_ver"; \
	done
	@echo "  - $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION) (default: Python $(DEFAULT_PYTHON_VERSION))"

# @SECTION Documentation
# Documentation targets
docs: docs-build  ## Build documentation (main docs entry point)

docs-build: dev-install  ## Build documentation locally with mike (no push)
	@echo "Building documentation locally with mike..."
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --update-aliases latest
	@echo "Documentation built with mike versioning"

ci-docs-build:  ## Build documentation for CI PR testing (matches docs.yml PR builds)
	@dev-tools/scripts/ci_docs_build.sh

ci-docs-build-for-pages:  ## Build documentation for GitHub Pages deployment (no push)
	@dev-tools/scripts/ci_docs_build_for_pages.sh

docs-serve: dev-install  ## Serve versioned documentation locally with live reload
	@echo "Starting versioned documentation server at http://127.0.0.1:8000"
	@echo "Press Ctrl+C to stop the server"
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike serve

docs-deploy: dev-install  ## Deploy documentation locally (for testing deployment)
	@echo "Deploying documentation locally with mike..."
	@echo "WARNING: This will commit to your local gh-pages branch"
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --update-aliases latest
	@echo "Documentation deployed locally. Use 'git push origin gh-pages' to publish."

ci-docs-deploy:  ## Deploy documentation to GitHub Pages (matches docs.yml main branch)
	@dev-tools/scripts/ci_docs_deploy.sh

docs-deploy-version: dev-install  ## Deploy specific version (usage: make docs-deploy-version VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make docs-deploy-version VERSION=1.0.0"; \
		exit 1; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike deploy --push --update-aliases $(VERSION) latest

docs-list-versions:  ## List all documentation versions
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike list

docs-delete-version:  ## Delete a documentation version (usage: make docs-delete-version VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make docs-delete-version VERSION=1.0.0"; \
		exit 1; \
	fi
	@if [ ! -f "$(BIN)/mike" ]; then \
		echo "Mike not found, installing development dependencies..."; \
		$(MAKE) dev-install; \
	fi
	cd $(DOCS_DIR) && ../$(BIN)/mike delete $(VERSION)

docs-clean:  ## Clean documentation build files
	rm -rf $(DOCS_BUILD_DIR)

# @SECTION Release Management
# Standard releases
release-patch: ## Bump patch version and create release (1.0.0 -> 1.0.1)
	@$(MAKE) _release BUMP_TYPE=patch

release-minor: ## Bump minor version and create release (1.0.0 -> 1.1.0)
	@$(MAKE) _release BUMP_TYPE=minor

release-major: ## Bump major version and create release (1.0.0 -> 2.0.0)
	@$(MAKE) _release BUMP_TYPE=major

# Pre-releases - Alpha
release-patch-alpha: ## Bump patch and create alpha release (1.0.0 -> 1.0.1-alpha.1)
	@$(MAKE) _release BUMP_TYPE=patch PRERELEASE_TYPE=alpha

release-minor-alpha: ## Bump minor and create alpha release (1.0.0 -> 1.1.0-alpha.1)
	@$(MAKE) _release BUMP_TYPE=minor PRERELEASE_TYPE=alpha

release-major-alpha: ## Bump major and create alpha release (1.0.0 -> 2.0.0-alpha.1)
	@$(MAKE) _release BUMP_TYPE=major PRERELEASE_TYPE=alpha

# Pre-releases - Beta
release-patch-beta: ## Bump patch and create beta release (1.0.0 -> 1.0.1-beta.1)
	@$(MAKE) _release BUMP_TYPE=patch PRERELEASE_TYPE=beta

release-minor-beta: ## Bump minor and create beta release (1.0.0 -> 1.1.0-beta.1)
	@$(MAKE) _release BUMP_TYPE=minor PRERELEASE_TYPE=beta

release-major-beta: ## Bump major and create beta release (1.0.0 -> 2.0.0-beta.1)
	@$(MAKE) _release BUMP_TYPE=major PRERELEASE_TYPE=beta

# Pre-releases - RC
release-patch-rc: ## Bump patch and create rc release (1.0.0 -> 1.0.1-rc.1)
	@$(MAKE) _release BUMP_TYPE=patch PRERELEASE_TYPE=rc

release-minor-rc: ## Bump minor and create rc release (1.0.0 -> 1.1.0-rc.1)
	@$(MAKE) _release BUMP_TYPE=minor PRERELEASE_TYPE=rc

release-major-rc: ## Bump major and create rc release (1.0.0 -> 2.0.0-rc.1)
	@$(MAKE) _release BUMP_TYPE=major PRERELEASE_TYPE=rc

# Promotions
promote-alpha: ## Promote to next alpha version (1.0.0-alpha.1 -> 1.0.0-alpha.2)
	@$(MAKE) _promote PROMOTE_TO=alpha

promote-beta: ## Promote alpha to beta (1.0.0-alpha.2 -> 1.0.0-beta.1)
	@$(MAKE) _promote PROMOTE_TO=beta

promote-rc: ## Promote beta to rc (1.0.0-beta.1 -> 1.0.0-rc.1)
	@$(MAKE) _promote PROMOTE_TO=rc

promote-stable: ## Promote rc to stable (1.0.0-rc.1 -> 1.0.0)
	@$(MAKE) _promote PROMOTE_TO=stable

# Special releases
release-version: ## Create release with specific version (RELEASE_VERSION=1.2.3)
	@$(MAKE) _release_custom

release-backfill: ## Create backfill release (RELEASE_VERSION=1.2.3 TO_COMMIT=abc)
	@$(MAKE) _release_backfill

release-historical:  ## Create and publish historical release
	@$(MAKE) build-historical COMMIT=$(COMMIT) VERSION=$(VERSION)
	@$(MAKE) publish-pypi

# Internal DRY implementations
_release:
	@if [ -n "$(RELEASE_VERSION)" ]; then \
		echo "ERROR: RELEASE_VERSION cannot be used with bump targets"; \
		echo "Use: RELEASE_VERSION=$(RELEASE_VERSION) make release-version"; \
		exit 1; \
	fi
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./dev-tools/release/dry_run_release.sh bump $(BUMP_TYPE) $(PRERELEASE_TYPE); \
	else \
		./dev-tools/release/version_manager.sh bump $(BUMP_TYPE) $(PRERELEASE_TYPE); \
		./dev-tools/release/release_creator.sh; \
	fi

_promote:
	@if [ -n "$(RELEASE_VERSION)" ]; then \
		echo "ERROR: RELEASE_VERSION cannot be used with promotion targets"; \
		echo "Use: RELEASE_VERSION=$(RELEASE_VERSION) make release-version"; \
		exit 1; \
	fi
	@if [ "$(DRY_RUN)" = "true" ]; then \
		echo "DRY RUN: Promotion simulation not yet implemented"; \
		echo "Would promote using: $(PROMOTE_TO)"; \
	else \
		./dev-tools/release/promote_manager.sh $(PROMOTE_TO); \
		./dev-tools/release/release_creator.sh; \
	fi

_release_custom:
	@if [ -z "$(RELEASE_VERSION)" ]; then \
		echo "ERROR: RELEASE_VERSION required"; \
		echo "Usage: RELEASE_VERSION=1.2.3 make release-version"; \
		exit 1; \
	fi
	@if [ "$(DRY_RUN)" = "true" ]; then \
		./dev-tools/release/dry_run_release.sh set $(RELEASE_VERSION); \
	else \
		./dev-tools/release/version_manager.sh set $(RELEASE_VERSION); \
		./dev-tools/release/release_creator.sh; \
	fi

_release_backfill:
	@if [ -z "$(RELEASE_VERSION)" ] || [ -z "$(TO_COMMIT)" ]; then \
		echo "ERROR: RELEASE_VERSION and TO_COMMIT required"; \
		echo "Usage: RELEASE_VERSION=1.2.3 TO_COMMIT=abc make release-backfill"; \
		echo "Optional: FROM_COMMIT=def (defaults to previous release)"; \
		exit 1; \
	fi
	@if [ "$(DRY_RUN)" = "true" ]; then \
		echo "DRY RUN: Backfill simulation"; \
		ALLOW_BACKFILL=true ./dev-tools/release/dry_run_release.sh set $(RELEASE_VERSION); \
	else \
		ALLOW_BACKFILL=true BACKFILL_VERSION=$(RELEASE_VERSION) TO_COMMIT=$(TO_COMMIT) FROM_COMMIT=$(FROM_COMMIT) ./dev-tools/release/release_creator.sh; \
	fi
