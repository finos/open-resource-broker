# Generate Your Own SDK

This guide explains how to produce a typed client for any language that
[openapi-generator](https://github.com/OpenAPITools/openapi-generator) supports
but that ORB does not officially ship.

## Officially supported vs. self-serve

| Language | Status | Package |
|----------|--------|---------|
| Python | Official | `pip install orb-py` |
| Go | Official | `go get github.com/finos/open-resource-broker/sdk/go` |
| TypeScript / Node | Official | `npm install @finos/open-resource-broker` |
| Java (17+) | Official | Maven / Gradle: `org.finos.openresourcebroker:open-resource-broker-java` |
| Kotlin | Official | Maven / Gradle: `org.finos.openresourcebroker:open-resource-broker-kotlin` |
| .NET / C# | Official | NuGet: `FINOS.OpenResourceBroker` |
| Scala, Rust, Ruby, PHP, C++, Swift, Dart, … | Self-serve | See this guide |

For the six official SDKs, `openapi-generator` output is produced on demand at
build time (generate-on-build; the generated directories are gitignored, not
committed) and the five hand-written layers (subprocess manager, UDS transport,
retry, auth, SSE) are already implemented. For every other language you must
generate the models yourself and then hand-write those five layers.

## Where to get the OpenAPI spec

### From a GitHub Release (recommended for production use)

Every ORB release publishes the spec as a release artifact at a stable URL:

```
https://github.com/finos/open-resource-broker/releases/download/v<version>/openapi-v<version>.json
```

Example — download the spec for v1.8.3:

```bash
curl -fsSL \
  https://github.com/finos/open-resource-broker/releases/download/v1.8.3/openapi-v1.8.3.json \
  -o openapi.json
```

### From the repository (for development against HEAD)

```
sdk/spec/openapi.json
```

This file is generated at release time by running a live ORB server and calling
`GET /openapi.json`. It is committed to source control and is the single source
of truth for all six official SDKs. If you are working from a clone of the
repository, use this path directly:

```bash
# From inside the repository root
INPUT_SPEC="$(pwd)/sdk/spec/openapi.json"
```

The spec covers **45 operations** across templates, machines, requests,
providers, config, admin, and observability endpoints.

## Generating typed models with openapi-generator

### Pinned version

All official SDKs pin openapi-generator to **v7.23.0**. Pin to the same
version to ensure generated model shapes match what the server expects.

### Using Docker (no local JVM required)

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g <GENERATOR_NAME> \
  -o /workspace/generated/<lang> \
  --additional-properties=<key=value,...>
```

### Using npx (Node 18+)

```bash
npx @openapitools/openapi-generator-cli@2.15.3 generate \
  -i openapi.json \
  -g <GENERATOR_NAME> \
  -o generated/<lang> \
  --additional-properties=<key=value,...>
```

The npm package `@openapitools/openapi-generator-cli@2.15.3` wraps
openapi-generator-cli v7.23.0. Verify with:

```bash
npx @openapitools/openapi-generator-cli@2.15.3 version
```

### Example — Scala (scala-akka)

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g scala-akka \
  -o /workspace/generated/scala \
  --additional-properties=groupId=org.finos,artifactId=orb-sdk-scala,artifactVersion=0.1.0,generateModelTests=false,generateApiTests=false
```

### Example — Rust

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g rust \
  -o /workspace/generated/rust \
  --additional-properties=packageName=orb-sdk,generateModelTests=false,generateApiTests=false
```

### Example — Ruby

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g ruby \
  -o /workspace/generated/ruby \
  --additional-properties=gemName=orb_sdk,gemVersion=0.1.0,generateModelTests=false,generateApiTests=false
```

### Example — PHP

```bash
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g php \
  -o /workspace/generated/php \
  --additional-properties=invokerPackage=FINOS\\OpenResourceBroker,packageName=finos/orb-sdk,generateModelTests=false,generateApiTests=false
```

### Notable supported generators

Run `docker run --rm openapitools/openapi-generator-cli:v7.23.0 list` for the
complete list. A representative subset:

| Category | Generators |
|----------|-----------|
| JVM | `java`, `kotlin`, `scala-akka`, `scala-http4s`, `groovy` |
| .NET | `csharp`, `fsharp` |
| JavaScript / TypeScript | `typescript-axios`, `typescript-fetch`, `typescript-node`, `javascript` |
| Python | `python` |
| Ruby | `ruby` |
| PHP | `php`, `php-symfony` |
| Go | `go` |
| Rust | `rust` |
| Swift | `swift5`, `swift6` |
| Dart / Flutter | `dart`, `dart-dio` |
| C++ | `cpp-restbed`, `cpp-ue4` |
| Other | `r`, `perl`, `bash`, `powershell`, `elm`, `erlang-proper` |

## What you get — and what you still need to write

### What the generator produces

- **Typed model classes** — one class/struct per OpenAPI schema component (e.g.
  `TemplateItem`, `RequestMachinesRequest`, `RequestOperationResponse`).
- **API stub methods** — one method per operation with typed parameters and
  return types. These stubs call a generated HTTP client that issues plain
  TCP/HTTPS requests.
- **Serialization helpers** — JSON marshal/unmarshal wired to the model classes.

This is sufficient if you only need to connect to a **remote ORB server over
HTTP/HTTPS** and do not use the managed-subprocess or UNIX socket modes.

### What the generator does NOT produce — the five hand-written layers

The generator cannot produce ORB's core value. For a complete, production-grade
SDK you must hand-write the five layers documented in
[`sdk/ARCHITECTURE.md`](https://github.com/finos/open-resource-broker/blob/main/sdk/ARCHITECTURE.md):

| Layer | What it does | Reference implementation |
|-------|-------------|--------------------------|
| 1. Subprocess Manager | Spawns and supervises a local ORB process; polls `/health` until healthy; SIGTERM/SIGKILL on shutdown | `sdk/go/internal/process/manager.go` |
| 2. UDS Transport | Dials a UNIX domain socket and wraps it as a standard HTTP transport | `sdk/go/internal/transport/uds.go` |
| 3. Retry Transport | Transparent retry on network errors and HTTP 5xx with exponential back-off and jitter | `sdk/go/internal/transport/retry.go` |
| 4. AWS SigV4 Auth | Signs outgoing requests using the standard AWS credential chain; supports static credentials and dynamic providers | `sdk/go/internal/transport/sigv4.go` |
| 5. SSE Reader + Reconnect | Parses `data:` frames, honours `retry:` directives, reconnects with back-off, detects the terminal sentinel event | `sdk/go/internal/sse/reader.go` |

The TypeScript SDK (`sdk/typescript/`) is the most fully commented reference
implementation and is a good starting point for languages in that family. The
Go SDK (`sdk/go/`) is the canonical reference for all five layers.

**The generated HTTP client talks to a remote ORB server over TCP.** It will
not start ORB as a subprocess, will not dial a UNIX socket, will not reconnect
an SSE stream, and will not sign requests with AWS SigV4. If your use-case
requires those capabilities, you must port the five layers from the reference
implementations.

## Authentication headers

All requests to a remote ORB server must include an `Authorization` header.

### Bearer token

```
Authorization: Bearer <token>
```

### AWS SigV4

Use the standard AWS Signature Version 4 signing procedure with:

- Service name: `execute-api` (AWS API Gateway-fronted deployments) or as
  configured by the operator.
- Region: the AWS region of the ORB deployment.
- Credentials: read from the standard AWS credential chain (environment
  variables, `~/.aws/credentials`, or instance/task metadata).

Prefer the language-native AWS SDK for signing rather than a hand-rolled
implementation. See `sdk/ARCHITECTURE.md` Layer 4 for per-language guidance.

## The X-ORB-Scheduler header (HostFactory mode)

When connecting to a remote ORB server that is running with
`--scheduler hostfactory`, every request must carry:

```
X-ORB-Scheduler: hostfactory
```

The server reads this header to select the HostFactory scheduler backend.
Without it the default scheduler is used. The header is ignored if the server
was not started with `--scheduler hostfactory`.

When using the default scheduler, omit the header entirely.

## Regenerating after a spec update

The spec can change between ORB releases. To regenerate models after an
upgrade:

1. Download the new spec from the release artifact URL shown above.
2. Re-run the same `docker run` / `npx` command with the updated spec.
3. Commit the diff to generated files alongside your application code.
4. Review the diff for any new or renamed operations that require updates to
   your hand-written transport wiring.

To check which operations exist in a given spec version:

```bash
# List all operationIds in the spec (requires Python 3)
python3 -c "
import json, sys
spec = json.load(open('openapi.json'))
for path, methods in spec['paths'].items():
    for method, op in methods.items():
        print(op.get('operationId', f'{method.upper()} {path}'))
" | sort
```

The current spec (v1.8.x) exposes **45 operations**. The full list is in
[`sdk/spec/openapi.json`](https://github.com/finos/open-resource-broker/blob/main/sdk/spec/openapi.json).

## Checking for spec drift

The official SDKs do not commit generated models; instead each build regenerates
them from the spec, so "drift" surfaces as a build failure rather than a diff.
For a self-serve SDK where you *do* commit the generated models, you can detect
drift against a newer spec by running the generator again and checking the diff:

```bash
# Regenerate into a temp directory and diff
docker run --rm \
  -v "$(pwd):/workspace" \
  openapitools/openapi-generator-cli:v7.23.0 generate \
  -i /workspace/openapi.json \
  -g <GENERATOR_NAME> \
  -o /workspace/generated-new/<lang>

diff -rq generated/<lang> generated-new/<lang>
```

To run this check as a make target, add a target analogous to the existing
`sdk-<lang>-check-drift` targets (defined in `makefiles/sdk.mk`), which
regenerate from the spec and then build to prove the spec still produces
compilable code.

## Next steps

After generating models and implementing the five hand-written layers:

1. Implement unit tests (mock HTTP server, no real ORB process).
2. Implement contract tests against a real ORB instance, following the pattern
   in `sdk/typescript/tests/contract/contract.test.ts`.
3. Run the cross-language parity scenario in `sdk/parity/scenario.json` to
   verify behavioral equivalence with the official SDKs.
4. Open a pull request on the FINOS repository. The contribution guide is in
   `CONTRIBUTING.md`.
