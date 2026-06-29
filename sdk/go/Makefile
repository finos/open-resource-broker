.PHONY: generate test lint build

generate:
	go run github.com/ogen-go/ogen/cmd/ogen@latest --config ogenconfig.yaml openapi.json

test:
	go test ./...

test-integration:
	go test -tags integration -timeout 120s ./...

lint:
	golangci-lint run ./...

export-spec:
	@echo "Start ORB and export: orb server start --foreground --api-only --port 18999 & sleep 3 && curl -s http://localhost:18999/openapi.json > openapi.json"
