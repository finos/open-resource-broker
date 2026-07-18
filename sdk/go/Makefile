.PHONY: generate test lint build

generate:
	@# The Go SDK models are produced by openapi-generator (v7.23.0, -g go),
	@# same as every other language SDK. Generation is driven from the repo
	@# root so the pinned JAR and shared config are resolved consistently.
	@# See sdk/go/openapi-generator-config.yaml and makefiles/sdk.mk.
	$(MAKE) -C ../.. sdk-go-generate

test:
	go test ./...

test-integration:
	go test -tags integration -timeout 120s ./...

lint:
	golangci-lint run ./...

export-spec:
	@echo "Start ORB and export: orb server start --foreground --api-only --port 18999 & sleep 3 && curl -s http://localhost:18999/openapi.json > ../spec/openapi.json"
