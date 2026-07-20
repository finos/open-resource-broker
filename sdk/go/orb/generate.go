package orb

// Code generation for the Go SDK uses openapi-generator (Java-based) to produce
// typed models into internal/generated from sdk/spec/openapi.json.
//
// Prerequisites:
//   - Java runtime (JDK 17+ recommended; JDK 11 minimum)
//   - openapi-generator JAR v7.23.0 (see sdk/openapi-generator-config.yaml for version)
//
// Invocation (handled by `make sdk-go-generate` or `make sdk-generate`):
//
//	java -jar <path-to-7.23.0.jar> generate \
//	  -g go \
//	  -i ../spec/openapi.json \
//	  -o ../internal/generated \
//	  --additional-properties "packageName=generated,withGoMod=false,generateModelTests=false,generateApiTests=false,enumClassPrefix=true,structPrefix=true"
//
// Only model_*.go, utils.go, response.go, and types.go are kept from the
// generator output. The api_*.go, client.go, and configuration.go files are NOT
// used — the hand-written transport layer in this package provides those.
//
// The generated directory is gitignored; run `make sdk-go-generate` before
// building.  CI generates fresh output on every run.
