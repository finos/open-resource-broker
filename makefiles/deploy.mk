# Build, deployment, and documentation targets

# @SECTION Documentation
# Documentation targets
docs: docs-build  ## Build documentation (main docs entry point)

docs-build: dev-install  ## Build documentation locally with mike (no push)
	@echo "Building documentation locally with mike..."
	@if [ -n "$$CI" ] || [ -n "$$GITHUB_ACTIONS" ]; then \
		echo "CI environment detected, using ci-docs-build..."; \
		$(MAKE) ci-docs-build; \
	else \
		cd $(DOCS_DIR) && ../$(BIN)/mike deploy --update-aliases latest; \
		echo "Documentation built with mike versioning"; \
	fi

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

# Scheduled release targets (used by workflows)
release-alpha-if-needed: ## Create alpha release if there are new commits since last alpha
	@./dev-tools/release/conditional_release.sh alpha

release-beta-if-needed: ## Create beta release if there are new alphas to promote
	@./dev-tools/release/conditional_release.sh beta

release-rc-if-needed: ## Create RC release if there are new betas to promote
	@./dev-tools/release/conditional_release.sh rc

# @SECTION Build & Deploy
build: clean dev-install  ## Build package
	VERSION=$${VERSION:-$$(make -s get-version)} $(MAKE) generate-pyproject && \
	VERSION=$${VERSION:-$$(make -s get-version)} BUILD_ARGS="$(BUILD_ARGS)" ./dev-tools/package/build.sh

build-test: build  ## Build and test package installation
	@echo "Testing package installation..."
	make test-install

test-install: build  ## Test package installation in clean environment
	@echo "Testing package installation in clean environment..."
	# Create temporary virtual environment and test installation
	python -m venv /tmp/test-install-env
	/tmp/test-install-env/bin/pip install dist/*.whl
	/tmp/test-install-env/bin/python -c "import $(PACKAGE_NAME_SHORT); print('Package installed successfully')"
	rm -rf /tmp/test-install-env

# @SECTION Unified Release Management
release: dev-install ## Unified release command (forward/historical/analysis)
	@if [ -n "$(COMMIT)" ] && [ -n "$(VERSION)" ]; then \
		echo "Creating historical release $(VERSION) from commit $(COMMIT)"; \
		RELEASE_MODE=historical RELEASE_COMMIT=$(COMMIT) RELEASE_VERSION=$(VERSION) \
		$(call run-tool,semantic-release,version); \
	elif [ "$(MODE)" = "analysis" ]; then \
		echo "Running release analysis"; \
		RELEASE_MODE=analysis ./dev-tools/release/orchestrator.sh; \
	else \
		echo "Running forward release with semantic-release"; \
		$(call run-tool,semantic-release,version); \
	fi

release-historical: ## Historical release (usage: make release-historical COMMIT=abc123 VERSION=0.0.5)
	@if [ -z "$(COMMIT)" ] || [ -z "$(VERSION)" ]; then \
		echo "ERROR: Both COMMIT and VERSION are required"; \
		echo "Usage: make release-historical COMMIT=abc123 VERSION=0.0.5"; \
		exit 1; \
	fi
	@$(MAKE) release COMMIT=$(COMMIT) VERSION=$(VERSION)

release-analysis: ## RC readiness analysis
	@$(MAKE) release MODE=analysis

release-dry-run: dev-install ## Dry run release (replaces old dry_run_release.sh)
	@echo "Running semantic-release dry run..."
	@$(call run-tool,semantic-release,--noop version)

# Version management targets
version-show:  ## Show current version from project config
	@echo "Current version: $(VERSION)"

get-version:  ## Generate unified version (works for PyPI, Docker, Git)
	@if [ "$${IS_RELEASE:-false}" = "true" ]; then \
		echo "$(VERSION)"; \
	else \
		commit=$$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown'); \
		dev_int=$$(python3 -c "print(int('$${commit}'[:6], 16))"); \
		echo "$(VERSION).dev$${dev_int}"; \
	fi

version-bump:  ## Show version bump help
	@echo "Version Management Commands:"
	@echo "  make version-show         - Show current version"
	@echo "  make release              - Create new release"
	@echo "  make release-dry-run      - Test release process"
	@echo ""
	@echo "Current version: $(VERSION)"

# @SECTION Container Management
container-build: dev-install  ## Build container image
	@echo "Building container image..."
	@PYTHON_VERSION=$${PYTHON_VERSION:-$(DEFAULT_PYTHON_VERSION)} \
	IMAGE_NAME=$(CONTAINER_IMAGE) \
	REGISTRY=$(CONTAINER_REGISTRY) \
	VERSION=$(VERSION) \
	PACKAGE_NAME_SHORT=$(PACKAGE_NAME_SHORT) \
	AUTHOR="$(AUTHOR)" \
	LICENSE="$(LICENSE)" \
	REPO_URL="$(REPO_URL)" \
	./dev-tools/scripts/container_build.sh

container-build-single: dev-install  ## Build single-platform container image
	@echo "Building single-platform container image..."
	@if [ -z "$(PYTHON_VERSION)" ]; then \
		echo "ERROR: PYTHON_VERSION environment variable is required for container builds"; \
		exit 1; \
	fi
	@BUILD_DATE=$$(date -u +'%Y-%m-%dT%H:%M:%SZ'); \
	VCS_REF=$$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown'); \
	docker build --load \
		--build-arg BUILD_DATE=$$BUILD_DATE \
		--build-arg VERSION=$(VERSION) \
		--build-arg VCS_REF=$$VCS_REF \
		--build-arg PYTHON_VERSION=$(PYTHON_VERSION) \
		--build-arg PACKAGE_NAME_SHORT=$(PACKAGE_NAME_SHORT) \
		--build-arg AUTHOR="$(AUTHOR)" \
		--build-arg LICENSE="$(LICENSE)" \
		--build-arg REPO_URL="$(REPO_URL)" \
		--build-arg BUILDKIT_DOCKERFILE_CHECK=skip=SecretsUsedInArgOrEnv \
		-t $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)-python$(PYTHON_VERSION) \
		.

container-build-multi: dev-install  ## Build multi-platform container image
	@echo "Building multi-platform container image..."
	if ! docker buildx ls | grep -q multi-arch; then \
		echo "Creating multi-arch builder..."; \
		docker buildx create --name multi-arch --use; \
	fi
	@BUILD_DATE=$$(date -u +'%Y-%m-%dT%H:%M:%SZ'); \
	VCS_REF=$$(git rev-parse --short HEAD 2>/dev/null || echo 'unknown'); \
	PYTHON_VERSION=$${PYTHON_VERSION:-$(DEFAULT_PYTHON_VERSION)}; \
	docker buildx build --platform linux/amd64,linux/arm64 \
		--build-arg BUILD_DATE=$$BUILD_DATE \
		--build-arg VERSION=$(VERSION) \
		--build-arg VCS_REF=$$VCS_REF \
		--build-arg PYTHON_VERSION=$$PYTHON_VERSION \
		--build-arg PACKAGE_NAME_SHORT=$(PACKAGE_NAME_SHORT) \
		--build-arg AUTHOR="$(AUTHOR)" \
		--build-arg LICENSE="$(LICENSE)" \
		--build-arg REPO_URL="$(REPO_URL)" \
		--build-arg BUILDKIT_DOCKERFILE_CHECK=skip=SecretsUsedInArgOrEnv \
		-t $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION) \
		-t $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):latest \
		--push .

container-push-multi: container-build-multi  ## Build and push multi-platform container

container-show-version:  ## Show container version information
	@echo "Container Registry: $(CONTAINER_REGISTRY)"
	@echo "Container Image: $(CONTAINER_IMAGE)"
	@echo "Container Version: $(VERSION)"
	@echo "Full Image Name: $(CONTAINER_REGISTRY)/$(CONTAINER_IMAGE):$(VERSION)"

container-run: container-build  ## Build and run container locally
	@echo "Running container locally..."
	./dev-tools/scripts/container_build.sh

docker-compose-up:  ## Start services with docker-compose
	@echo "Starting services with docker-compose..."
	docker-compose up -d

docker-compose-down:  ## Stop services with docker-compose
	@echo "Stopping services with docker-compose..."
	docker-compose down

# @SECTION Release Management
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

# @SECTION Publishing
# @SECTION Release Management (Semantic Versioning)
release-patch: ## Bump patch version and create release (1.0.0 -> 1.0.1)
	@$(MAKE) _release BUMP_TYPE=patch

release-minor: ## Bump minor version and create release (1.0.0 -> 1.1.0)
	@$(MAKE) _release BUMP_TYPE=minor

release-major: ## Bump major version and create release (1.0.0 -> 2.0.0)
	@$(MAKE) _release BUMP_TYPE=major

# Pre-releases - Alpha
release-patch-alpha: ## Bump patch version and create alpha release (1.0.0 -> 1.0.1a1)
	@$(MAKE) _release BUMP_TYPE=patch PRE_RELEASE=alpha

release-minor-alpha: ## Bump minor version and create alpha release (1.0.0 -> 1.1.0a1)
	@$(MAKE) _release BUMP_TYPE=minor PRE_RELEASE=alpha

release-major-alpha: ## Bump major version and create alpha release (1.0.0 -> 2.0.0a1)
	@$(MAKE) _release BUMP_TYPE=major PRE_RELEASE=alpha

# Pre-releases - Beta
release-patch-beta: ## Bump patch version and create beta release (1.0.0 -> 1.0.1b1)
	@$(MAKE) _release BUMP_TYPE=patch PRE_RELEASE=beta

release-minor-beta: ## Bump minor version and create beta release (1.0.0 -> 1.1.0b1)
	@$(MAKE) _release BUMP_TYPE=minor PRE_RELEASE=beta

release-major-beta: ## Bump major version and create beta release (1.0.0 -> 2.0.0b1)
	@$(MAKE) _release BUMP_TYPE=major PRE_RELEASE=beta

# Pre-releases - RC
release-patch-rc: ## Bump patch version and create RC release (1.0.0 -> 1.0.1rc1)
	@$(MAKE) _release BUMP_TYPE=patch PRE_RELEASE=rc

release-minor-rc: ## Bump minor version and create RC release (1.0.0 -> 1.1.0rc1)
	@$(MAKE) _release BUMP_TYPE=minor PRE_RELEASE=rc

release-major-rc: ## Bump major version and create RC release (1.0.0 -> 2.0.0rc1)
	@$(MAKE) _release BUMP_TYPE=major PRE_RELEASE=rc

# Internal release target
_release:  ## Internal release target
	@if [ -z "$(BUMP_TYPE)" ]; then \
		echo "Error: BUMP_TYPE is required"; \
		exit 1; \
	fi
	@echo "Creating $(BUMP_TYPE) release..."
	@if [ -n "$(PRE_RELEASE)" ]; then \
		echo "Pre-release type: $(PRE_RELEASE)"; \
	fi
	./dev-tools/release/create_release.sh $(BUMP_TYPE) $(PRE_RELEASE)

release-version:  ## Create release for specific version (usage: make release-version VERSION=1.0.0)
	@if [ -z "$(VERSION)" ]; then \
		echo "Error: VERSION is required. Usage: make release-version VERSION=1.0.0"; \
		exit 1; \
	fi
	@echo "Creating release for version $(VERSION)..."
	./dev-tools/release/create_release.sh --version $(VERSION)

update-release-notes:  ## Update release notes for current version
	@echo "Updating release notes for version $(VERSION)..."
	./dev-tools/release/update_release_notes.sh $(VERSION)

build-historical:  ## Build historical release (usage: make build-historical COMMIT=abc123 VERSION=0.0.1)
	@if [ -z "$(COMMIT)" ] || [ -z "$(VERSION)" ]; then \
		echo "Usage: make build-historical COMMIT=<hash> VERSION=<version>"; \
		exit 1; \
	fi
	@echo "Building historical release $(VERSION) from commit $(COMMIT)..."
	./dev-tools/release/build_historical.sh $(COMMIT) $(VERSION)
