# SDK code generation targets
#
# Generated code is NOT committed to source control.  It is produced on demand
# (generate-on-build) so published artifacts still ship the models.
#
# Prerequisites:
#   - Java runtime (JDK 17+ recommended; JDK 11 minimum)
#     macOS: brew install openjdk
#     Ubuntu/Debian: apt-get install default-jdk
#   - openapi-generator-cli JAR v7.23.0 is downloaded on first use.  Depending
#     on how it was installed the jar lands in one of:
#       ~/.openapi-generator-cli/   (openapi-generator-cli version-manager set)
#       ~/.npm/_npx/…               (npx cache copy)
#       ~/.openapi-generator/       (legacy)
#     All three are searched.  No manual download is needed — just run
#     `make sdk-go-generate`.  Override with OPENAPI_GENERATOR_JAR=/path/to.jar.
#
# Usage:
#   make sdk-generate              # (re)generate ALL language SDKs
#   make sdk-go-generate           # (re)generate the Go SDK only
#   make sdk-check-drift           # generate all + build + test to prove the
#                                  # spec produces buildable code (all five langs)
#   make sdk-go-check-drift        # same, Go only

# @SECTION SDK Code Generation

OPENAPI_SPEC             := sdk/spec/openapi.json

# Pinned openapi-generator version.  Bump this variable when upgrading.
OPENAPI_GENERATOR_VERSION := 7.23.0

# Resolve the JAR path.  Priority:
#   1. Explicit override: set OPENAPI_GENERATOR_JAR in the environment
#   2. Find via `find` across all known install locations.  version-manager set
#      stores the jar under ~/.openapi-generator-cli/; npx leaves a copy in
#      ~/.npm/_npx; ~/.openapi-generator is the legacy location.
OPENAPI_GENERATOR_SEARCH_DIRS := $(HOME)/.openapi-generator-cli $(HOME)/.npm/_npx $(HOME)/.openapi-generator
OPENAPI_GENERATOR_JAR ?= $(shell find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1)

SDK_GO_GENERATED  := sdk/go/internal/generated
SDK_JAVA_GENERATED  := sdk/java/generated
SDK_KOTLIN_GENERATED  := sdk/kotlin/generated
SDK_CSHARP_GENERATED  := sdk/csharp/generated
SDK_TYPESCRIPT_GENERATED := sdk/typescript/generated

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
check-java:  ## Verify that a Java runtime is available
	@command -v java > /dev/null 2>&1 || \
		{ echo "ERROR: Java runtime not found. Install Java 17+ (e.g. 'brew install openjdk' on macOS)"; exit 1; }

# Ensure the JAR is available; if not, install via npx.
_ensure-jar:
	@if [ ! -f "$(OPENAPI_GENERATOR_JAR)" ]; then \
		echo "openapi-generator JAR not found — installing v$(OPENAPI_GENERATOR_VERSION) via npx..."; \
		npx --yes @openapitools/openapi-generator-cli version-manager set $(OPENAPI_GENERATOR_VERSION); \
		NEW_JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
		if [ -z "$$NEW_JAR" ]; then \
			echo "ERROR: could not locate $(OPENAPI_GENERATOR_VERSION).jar after install."; \
			exit 1; \
		fi; \
		echo "Jar installed at: $$NEW_JAR"; \
	fi

# ---------------------------------------------------------------------------
# Go SDK generation
# ---------------------------------------------------------------------------
# The generated package lives in sdk/go/internal/generated and is gitignored
# (produced on demand, not committed — see the header note above).  Only
# model_*.go, utils.go, response.go, and types.go are kept; api_* and client.go
# are produced by the generator but immediately removed because the hand-written
# transport in sdk/go/orb/ serves that role.
# ---------------------------------------------------------------------------
sdk-go-generate: check-java _ensure-jar  ## Regenerate the Go SDK typed models from sdk/spec/openapi.json
	@echo "Regenerating Go SDK models from $(OPENAPI_SPEC)..."
	@JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
	java -jar "$$JAR" generate \
	  -g go \
	  -i $(OPENAPI_SPEC) \
	  -o $(SDK_GO_GENERATED) \
	  --additional-properties packageName=generated,withGoMod=false,generateModelTests=false,generateApiTests=false,enumClassPrefix=true,structPrefix=true \
	  2>&1 | grep -v "^\[main\] INFO" | grep -v "Thanks for using"
	@echo "Cleaning up non-model files (transport is hand-written)..."
	@rm -f $(SDK_GO_GENERATED)/api_*.go \
	       $(SDK_GO_GENERATED)/client.go \
	       $(SDK_GO_GENERATED)/configuration.go \
	       $(SDK_GO_GENERATED)/go.mod \
	       $(SDK_GO_GENERATED)/go.sum \
	       $(SDK_GO_GENERATED)/README.md \
	       $(SDK_GO_GENERATED)/git_push.sh \
	       $(SDK_GO_GENERATED)/.travis.yml \
	       $(SDK_GO_GENERATED)/.gitignore \
	       $(SDK_GO_GENERATED)/.openapi-generator-ignore
	@rm -rf $(SDK_GO_GENERATED)/test \
	        $(SDK_GO_GENERATED)/docs \
	        $(SDK_GO_GENERATED)/api \
	        $(SDK_GO_GENERATED)/.openapi-generator
	@# Restore the types.go shim for anyOf:{} fields if the generator removed it.
	@# The file is not committed (generated dir is gitignored) so we write it inline.
	@if [ ! -f $(SDK_GO_GENERATED)/types.go ]; then \
		echo "Restoring types.go shim..."; \
		printf 'package generated\n\ntype AnyOf = any\n\ntype NullableAnyOf struct {\n\tvalue *AnyOf\n\tisSet bool\n}\n\nfunc (v NullableAnyOf) Get() *AnyOf {\n\treturn v.value\n}\n\nfunc (v *NullableAnyOf) Set(val AnyOf) {\n\tv.value = &val\n\tv.isSet = true\n}\n\nfunc (v NullableAnyOf) IsSet() bool {\n\treturn v.isSet\n}\n\nfunc (v *NullableAnyOf) Unset() {\n\tv.value = nil\n\tv.isSet = false\n}\n\nfunc NewNullableAnyOf(val *AnyOf) *NullableAnyOf {\n\treturn &NullableAnyOf{value: val, isSet: true}\n}\n' \
		> $(SDK_GO_GENERATED)/types.go; \
	fi
	@echo "Verifying generated package compiles..."
	@# The Go SDK is its own module (sdk/go/go.mod); build from inside it so the
	@# generated package resolves against that module, not the repo-root module.
	@cd sdk/go && go build ./internal/generated/...
	@echo "Go SDK generation complete: $(SDK_GO_GENERATED)"

# ---------------------------------------------------------------------------
# Java SDK generation
# ---------------------------------------------------------------------------
# The generated package lives in sdk/java/generated/ and is gitignored
# (produced on demand, not committed — see the header note above).  Only models
# (src/main/java/.../model/) are kept; api/, invoker stubs, build scaffolding,
# CI files, and generator metadata are immediately removed because the
# hand-written transport in sdk/java/src/ serves that role.  This mirrors the
# cleanup pattern used for Go above.
# ---------------------------------------------------------------------------
sdk-java-generate: check-java _ensure-jar  ## Regenerate the Java SDK from sdk/spec/openapi.json
	@echo "Regenerating Java SDK from $(OPENAPI_SPEC)..."
	@JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
	java -jar "$$JAR" generate \
	  -g java \
	  -i $(OPENAPI_SPEC) \
	  -o $(SDK_JAVA_GENERATED) \
	  -c sdk/java/openapi-generator-config.yaml \
	  2>&1 | grep -v "^\[main\] INFO" | grep -v "Thanks for using"
	@echo "Cleaning up non-model files (transport is hand-written)..."
	@rm -f  $(SDK_JAVA_GENERATED)/git_push.sh \
	        $(SDK_JAVA_GENERATED)/.travis.yml \
	        $(SDK_JAVA_GENERATED)/build.gradle \
	        $(SDK_JAVA_GENERATED)/build.sbt \
	        $(SDK_JAVA_GENERATED)/pom.xml \
	        $(SDK_JAVA_GENERATED)/settings.gradle \
	        $(SDK_JAVA_GENERATED)/gradle.properties \
	        $(SDK_JAVA_GENERATED)/gradlew \
	        $(SDK_JAVA_GENERATED)/gradlew.bat \
	        $(SDK_JAVA_GENERATED)/README.md
	@rm -rf $(SDK_JAVA_GENERATED)/.github \
	        $(SDK_JAVA_GENERATED)/gradle \
	        $(SDK_JAVA_GENERATED)/api \
	        $(SDK_JAVA_GENERATED)/docs \
	        $(SDK_JAVA_GENERATED)/.openapi-generator \
	        $(SDK_JAVA_GENERATED)/.openapi-generator-ignore
	@echo "Java SDK generation complete: $(SDK_JAVA_GENERATED)"

# ---------------------------------------------------------------------------
# Kotlin SDK generation
# ---------------------------------------------------------------------------
# Only models (src/main/kotlin/.../model/) are kept; api/ stubs, build
# scaffolding, docs, and generator metadata are immediately removed because
# the hand-written transport in sdk/kotlin/src/ serves that role.
# ---------------------------------------------------------------------------
sdk-kotlin-generate: check-java _ensure-jar  ## Regenerate the Kotlin SDK from sdk/spec/openapi.json
	@echo "Regenerating Kotlin SDK from $(OPENAPI_SPEC)..."
	@JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
	java -jar "$$JAR" generate \
	  -g kotlin \
	  -i $(OPENAPI_SPEC) \
	  -o $(SDK_KOTLIN_GENERATED) \
	  -c sdk/kotlin/openapi-generator-config.yaml \
	  2>&1 | grep -v "^\[main\] INFO" | grep -v "Thanks for using"
	@echo "Cleaning up non-model files (transport is hand-written)..."
	@rm -f  $(SDK_KOTLIN_GENERATED)/build.gradle \
	        $(SDK_KOTLIN_GENERATED)/settings.gradle \
	        $(SDK_KOTLIN_GENERATED)/README.md
	@rm -rf $(SDK_KOTLIN_GENERATED)/docs \
	        $(SDK_KOTLIN_GENERATED)/src/main/kotlin/org/finos/openresourcebroker/sdk/api \
	        $(SDK_KOTLIN_GENERATED)/.openapi-generator \
	        $(SDK_KOTLIN_GENERATED)/.openapi-generator-ignore
	@echo "Kotlin SDK generation complete: $(SDK_KOTLIN_GENERATED)"

# ---------------------------------------------------------------------------
# C# (.NET) SDK generation
# ---------------------------------------------------------------------------
# The generichost generator (the only C# library that emits System.Text.Json-
# native models) produces a full Generic-Host client.  Only the Model/ types and
# their Client/ support files (Option<T>, ClientUtils, the JSON converters) are
# kept; the Api/ stubs, Extensions/, the Generic-Host bootstrap (ApiFactory,
# HostConfiguration), docs, and generator metadata are removed because the
# hand-written transport in sdk/csharp/src/ serves that role.  The kept files
# have no external package dependencies (only System.Text.Json), so the
# generated csproj is rewritten to a clean, dependency-free library that the
# hand-written client references directly — making C# genuinely hybrid.
# ---------------------------------------------------------------------------
sdk-csharp-generate: check-java _ensure-jar  ## Regenerate the C# SDK from sdk/spec/openapi.json
	@echo "Regenerating C# SDK from $(OPENAPI_SPEC)..."
	@JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
	java -jar "$$JAR" generate \
	  -g csharp \
	  -i $(OPENAPI_SPEC) \
	  -o $(SDK_CSHARP_GENERATED) \
	  -c sdk/csharp/openapi-generator-config.yaml \
	  2>&1 | grep -v "^\[main\] INFO" | grep -v "Thanks for using"
	@echo "Cleaning up non-model files (transport is hand-written)..."
	@rm -f  $(SDK_CSHARP_GENERATED)/git_push.sh \
	        $(SDK_CSHARP_GENERATED)/appveyor.yml \
	        $(SDK_CSHARP_GENERATED)/OpenResourceBroker.Sdk.sln \
	        $(SDK_CSHARP_GENERATED)/README.md
	@rm -rf $(SDK_CSHARP_GENERATED)/api \
	        $(SDK_CSHARP_GENERATED)/docs \
	        $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk/Api \
	        $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk/Extensions \
	        $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk.Test \
	        $(SDK_CSHARP_GENERATED)/.openapi-generator \
	        $(SDK_CSHARP_GENERATED)/.openapi-generator-ignore
	@# Remove the Generic-Host bootstrap files (the only kept-tree files that
	@# pull in Microsoft.Extensions.* / Polly).  The hand-written transport
	@# replaces the generated HTTP client, so the models + JSON converters + the
	@# lightweight Client support types are all that remain.
	@rm -f  $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk/Client/ApiFactory.cs \
	        $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk/Client/HostConfiguration.cs
	@# Rewrite the generated csproj to a clean, dependency-free STJ library.
	@printf '%s\n' \
	  '<Project Sdk="Microsoft.NET.Sdk">' \
	  '  <!-- Generated on demand by `make sdk-csharp-generate` — do not edit. -->' \
	  '  <PropertyGroup>' \
	  '    <TargetFramework>net8.0</TargetFramework>' \
	  '    <AssemblyName>OpenResourceBroker.Sdk</AssemblyName>' \
	  '    <PackageId>OpenResourceBroker.Sdk</PackageId>' \
	  '    <RootNamespace>OpenResourceBroker.Sdk</RootNamespace>' \
	  '    <Version>0.1.0</Version>' \
	  '    <Nullable>enable</Nullable>' \
	  '    <LangVersion>12</LangVersion>' \
	  '    <GenerateDocumentationFile>false</GenerateDocumentationFile>' \
	  '    <NoWarn>$$(NoWarn);CS1591</NoWarn>' \
	  '  </PropertyGroup>' \
	  '</Project>' \
	  > $(SDK_CSHARP_GENERATED)/src/OpenResourceBroker.Sdk/OpenResourceBroker.Sdk.csproj
	@echo "C# SDK generation complete: $(SDK_CSHARP_GENERATED)"

# ---------------------------------------------------------------------------
# TypeScript SDK generation
# ---------------------------------------------------------------------------
# Only models (models/) are kept; api/ stubs, build config files, and
# generator metadata are immediately removed because the hand-written
# transport in sdk/typescript/src/ serves that role.
# ---------------------------------------------------------------------------
sdk-typescript-generate: check-java _ensure-jar  ## Regenerate the TypeScript SDK from sdk/spec/openapi.json
	@echo "Regenerating TypeScript SDK from $(OPENAPI_SPEC)..."
	@JAR=$$(find $(OPENAPI_GENERATOR_SEARCH_DIRS) -name "$(OPENAPI_GENERATOR_VERSION).jar" 2>/dev/null | head -1); \
	java -jar "$$JAR" generate \
	  -g typescript-axios \
	  -i $(OPENAPI_SPEC) \
	  -o $(SDK_TYPESCRIPT_GENERATED) \
	  -c sdk/typescript/openapi-generator-config.yaml \
	  2>&1 | grep -v "^\[main\] INFO" | grep -v "Thanks for using"
	@echo "Cleaning up non-model files (transport is hand-written)..."
	@rm -f  $(SDK_TYPESCRIPT_GENERATED)/git_push.sh \
	        $(SDK_TYPESCRIPT_GENERATED)/package.json \
	        $(SDK_TYPESCRIPT_GENERATED)/tsconfig.json \
	        $(SDK_TYPESCRIPT_GENERATED)/tsconfig.esm.json \
	        $(SDK_TYPESCRIPT_GENERATED)/api.ts \
	        $(SDK_TYPESCRIPT_GENERATED)/README.md
	@rm -rf $(SDK_TYPESCRIPT_GENERATED)/api \
	        $(SDK_TYPESCRIPT_GENERATED)/docs \
	        $(SDK_TYPESCRIPT_GENERATED)/.openapi-generator \
	        $(SDK_TYPESCRIPT_GENERATED)/.openapi-generator-ignore
	@# withSeparateModelsAndApi makes the generator emit `export * from "./api"`
	@# in index.ts, but api/ is removed above (the hand-written transport serves
	@# that role).  Strip the dangling re-export so `tsc` does not fail with
	@# TS2307 "Cannot find module './api'".  Keep this in sync with the api/ rm.
	@if [ -f $(SDK_TYPESCRIPT_GENERATED)/index.ts ]; then \
		sed -i.bak '/export \* from "\.\/api"/d' $(SDK_TYPESCRIPT_GENERATED)/index.ts && \
		rm -f $(SDK_TYPESCRIPT_GENERATED)/index.ts.bak; \
	fi
	@echo "TypeScript SDK generation complete: $(SDK_TYPESCRIPT_GENERATED)"

# ---------------------------------------------------------------------------
# Aggregate target: regenerate ALL SDKs
# ---------------------------------------------------------------------------
sdk-generate: sdk-go-generate sdk-java-generate sdk-kotlin-generate sdk-csharp-generate sdk-typescript-generate  ## Regenerate all language SDKs from sdk/spec/openapi.json
	@echo "All SDK generation complete."

# ---------------------------------------------------------------------------
# Static spec-conformance check (no live server required)
# ---------------------------------------------------------------------------
# Wires sdk/parity/scenario.json into CI: asserts every parity step's
# (method, path, operationId) exists in sdk/spec/openapi.json.  Catches a
# scenario/spec drift (wrong verb or stale path) statically, independent of the
# real-orb contract legs.
# ---------------------------------------------------------------------------
sdk-spec-conformance:  ## Verify parity scenario matches the OpenAPI spec (static, no server)
	@echo "Checking SDK parity scenario against the OpenAPI spec..."
	@$(PYTHON) dev-tools/quality/validate_sdk_spec_conformance.py

# ---------------------------------------------------------------------------
# Cross-language runtime parity (requires a live orb)
# ---------------------------------------------------------------------------
# Each per-language target LOADS sdk/parity/scenario.json and drives its six
# ordered steps against a REAL orb spawned over a UDS, reusing that SDK's
# existing contract-test orb-spawn harness.  Steps are dispatched via the
# fixture's sdk_methods.<lang> mapping and asserted against each step's expected
# status/shape + skip rules.  Set ORB_BINARY (or ORB_PYTHON for .NET) so the
# harness can spawn orb; see the contract-test targets for the same convention.
# ---------------------------------------------------------------------------
sdk-go-parity: sdk-go-generate  ## Run the Go parity scenario against a real orb
	@echo "Running Go SDK parity scenario against real ORB..."
	@cd sdk/go && go test -tags integration -run TestParityScenario -timeout 150s ./orb/
	@echo "Go SDK parity complete."

sdk-typescript-parity: sdk-typescript-generate  ## Run the TypeScript parity scenario against a real orb
	@echo "Running TypeScript SDK parity scenario against real ORB..."
	@cd sdk/typescript && npm ci && npm run build && npm run test:parity
	@echo "TypeScript SDK parity complete."

sdk-java-parity:  ## Run the Java parity scenario against a real orb
	@echo "Running Java SDK parity scenario against real ORB..."
	@cd sdk/java && ./gradlew --no-daemon parityTest --rerun-tasks
	@echo "Java SDK parity complete."

sdk-kotlin-parity:  ## Run the Kotlin parity scenario against a real orb
	@echo "Running Kotlin SDK parity scenario against real ORB..."
	@cd sdk/kotlin && ./gradlew --no-daemon parityTest --rerun-tasks
	@echo "Kotlin SDK parity complete."

sdk-csharp-parity:  ## Run the C# parity scenario against a real orb
	@echo "Running C# SDK parity scenario against real ORB..."
	@dotnet test sdk/csharp/tests/parity/ParityTests.csproj --configuration Release --verbosity normal
	@echo "C# SDK parity complete."

sdk-parity: sdk-go-parity sdk-typescript-parity sdk-java-parity sdk-kotlin-parity sdk-csharp-parity  ## Run every language's parity scenario against a real orb
	@echo "All SDK parity scenarios passed."

# ---------------------------------------------------------------------------
# Spec-consistency checks (generate-on-build model)
# ---------------------------------------------------------------------------
# Generated code is no longer committed — the generated dirs are gitignored.
# Proof that the spec produces valid, buildable code = generate + build + test.
# These targets run the full generate→build cycle for each language.
# ---------------------------------------------------------------------------
sdk-go-check-drift: sdk-go-generate  ## Verify Go SDK: generate from spec + compile
	@echo "Verifying Go SDK builds cleanly from the current spec..."
	@cd sdk/go && go build ./...
	@echo "Go SDK: spec → generate → build OK."

sdk-java-check-drift: sdk-java-generate  ## Verify Java SDK: generate from spec + compile
	@echo "Verifying Java SDK builds cleanly from the current spec..."
	@cd sdk/java && ./gradlew --no-daemon compileJava
	@echo "Java SDK: spec → generate → build OK."

sdk-kotlin-check-drift: sdk-kotlin-generate  ## Verify Kotlin SDK: generate from spec + compile
	@echo "Verifying Kotlin SDK builds cleanly from the current spec..."
	@cd sdk/kotlin && ./gradlew --no-daemon compileKotlin
	@echo "Kotlin SDK: spec → generate → build OK."

sdk-typescript-check-drift: sdk-typescript-generate  ## Verify TypeScript SDK: generate from spec + build
	@echo "Verifying TypeScript SDK builds cleanly from the current spec..."
	@cd sdk/typescript && npm ci && npm run build
	@echo "TypeScript SDK: spec → generate → build OK."

sdk-csharp-check-drift: sdk-csharp-generate  ## Verify C# SDK: generate from spec + compile
	@echo "Verifying C# SDK builds cleanly from the current spec..."
	@# OrbSdk.sln includes the generated OpenResourceBroker.Sdk project, and the
	@# hand-written client references it (hybrid model — see sdk/ARCHITECTURE.md).
	@# Building the sln therefore compiles the generated models AND the client
	@# against them, giving C# real spec-drift protection: a spec change that
	@# produces uncompilable generated code, or that breaks the client's use of a
	@# model, fails here instead of silently.
	@dotnet build sdk/csharp/OrbSdk.sln --configuration Release
	@echo "C# SDK: spec → generate → build (client + generated models) OK."

sdk-check-drift: sdk-go-check-drift sdk-java-check-drift sdk-kotlin-check-drift sdk-typescript-check-drift sdk-csharp-check-drift  ## Verify all SDKs: generate from spec + build (all five languages)
	@echo "All SDK spec-consistency checks passed."

# ---------------------------------------------------------------------------
# Go SDK build helpers
# ---------------------------------------------------------------------------
sdk-go-build: sdk-go-generate  ## Generate then compile the Go SDK
	@echo "Building Go SDK..."
	@cd sdk/go && go build ./...
	@echo "Go SDK build complete."

sdk-go-test: sdk-go-build  ## Generate, compile, and test the Go SDK
	@echo "Running Go SDK tests..."
	@cd sdk/go && go test ./...
	@echo "Go SDK tests complete."

# ---------------------------------------------------------------------------
# Java SDK build helpers
# ---------------------------------------------------------------------------
sdk-java-build: sdk-java-generate  ## Compile the Java SDK (generates then compiles)
	@echo "Compiling Java SDK..."
	@cd sdk/java && ./gradlew --no-daemon compileJava
	@echo "Java SDK compiled: sdk/java/build/classes"

sdk-java-test: sdk-java-build  ## Run Java SDK unit tests
	@echo "Running Java SDK unit tests..."
	@cd sdk/java && ./gradlew --no-daemon test --rerun-tasks
	@echo "Java SDK unit tests complete."

sdk-java-contract-test:  ## Run Java SDK contract tests (requires ORB binary in PATH or ORB_BINARY env var)
	@echo "Running Java SDK contract tests against real ORB..."
	@cd sdk/java && ./gradlew --no-daemon contractTest --rerun-tasks
	@echo "Java SDK contract tests complete."

.PHONY: sdk-generate sdk-go-generate sdk-java-generate sdk-kotlin-generate sdk-csharp-generate \
        sdk-typescript-generate sdk-check-drift sdk-go-check-drift sdk-java-check-drift \
        sdk-kotlin-check-drift sdk-typescript-check-drift sdk-csharp-check-drift \
        check-java _ensure-jar sdk-spec-conformance \
        sdk-go-build sdk-go-test \
        sdk-java-build sdk-java-test sdk-java-contract-test \
        sdk-parity sdk-go-parity sdk-typescript-parity sdk-java-parity \
        sdk-kotlin-parity sdk-csharp-parity
