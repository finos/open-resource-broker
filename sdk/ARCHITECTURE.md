# ORB SDK Architecture

This document describes the architecture that every ORB language SDK must
implement.  It is the authoritative contract that new language SDKs follow and
that maintainers use when reviewing contributions.

## Design Model: Hybrid

ORB SDKs use a **hybrid generation model**:

- **Generated layer** — `openapi-generator` (pinned to v7.23.0) reads
  `sdk/spec/openapi.json` and produces typed request/response models and
  method stubs.  Generated output is **not committed** — each SDK's build
  generates it on demand (generate-on-build).  CI generates fresh output on
  every run to prove the spec produces buildable code.
- **Hand-written layer** — five components (described below) that codegen
  handles poorly or cannot handle at all: subprocess management, UDS transport,
  retry, AWS SigV4 auth, and SSE streaming.

The two layers compose: the public client API delegates request/response
marshalling to the generated models while all I/O flows through the
hand-written transport.

This holds for **all five SDKs, including C#**: the .NET client references the
generated `OpenResourceBroker.Sdk` models (produced by `make sdk-csharp-generate`
with the `generichost` library, which emits System.Text.Json-native models) and
marshals through them.  The generated project is included in `OrbSdk.sln`, so
`sdk-csharp-check-drift` builds the client *against* the generated models — a
spec change that breaks either fails the build.

## Five Mandatory Hand-Written Layers

Every language SDK MUST implement the following five layers.  The Go SDK in
`sdk/go/` is the reference implementation.

---

### Layer 1: Subprocess Manager

**Responsibility**: spawn and supervise a local ORB process, wait for it to
become healthy, and stop it cleanly.

**Interface** (conceptual, language-adapted):

```
SubprocessManager
  Start(ctx) → error
  Stop()     → error
  Healthy()  → bool
```

**Requirements**:
- Accept a binary path + args + env slice.
- Poll the `/health` endpoint (via the UDS transport when a socket path is
  configured, or via TCP when a port is given) until healthy or timeout.
- Background health-check loop: mark unhealthy after N consecutive failures
  (default 3) and surface that via `Healthy()`.
- Graceful stop: SIGTERM → wait → SIGKILL fallback.
- Thread-safe; all state transitions protected by a lock or atomic.

**Reference**: `sdk/go/internal/process/manager.go`

---

### Layer 2: UDS Transport

**Responsibility**: dial a UNIX domain socket, wrap it as an HTTP transport so
the rest of the SDK can issue ordinary HTTP requests without knowing the
underlying channel.

**Interface** (conceptual):

```
UDSTransport(socketPath string) → http.RoundTripper  # Go idiom
```

In other languages, implement as a custom HTTP client adapter/interceptor that
rewrites the connection to dial the socket path instead of the TCP host.

**Requirements**:
- Accept a socket path; ignore the `Host` header / URL host component.
- Support HTTP/1.1 keep-alive over the socket for connection reuse.
- Return a transport/adapter that is a drop-in replacement for the default
  TCP transport.
- Language-specific notes:
  - **Java**: `HttpClient.Builder#usingProxy` does not work; use
    `HttpClient` with a custom `Proxy` or a raw `UnixDomainSocketChannel`.
  - **Kotlin**: OkHttp `SocketFactory`-based approach (see okhttp-unix-socket
    library pattern).
  - **C#**: `SocketsHttpHandler.ConnectCallback` with
    `new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified)`.
  - **TypeScript**: Axios custom adapter backed by `undici` `MockAgent` or
    direct `undici` dispatcher with a UNIX socket connector.

**Reference**: `sdk/go/internal/transport/uds.go`

---

### Layer 3: Retry Transport

**Responsibility**: transparently retry transient failures with exponential
back-off.

**Interface** (conceptual):

```
RetryTransport(inner transport, maxRetries int, initialDelay duration) → transport
```

**Requirements**:
- Retry on network errors and HTTP 5xx responses.
- Do NOT retry on 4xx (client errors) or POST/PUT with non-idempotent bodies
  unless the server explicitly indicates retry-ability (e.g. 503 + Retry-After).
- Exponential back-off with jitter; cap at a configurable maximum delay.
- Respect `context` cancellation — stop retrying when context is done.

**Reference**: `sdk/go/internal/transport/retry.go`

---

### Layer 4: Pluggable Authentication

**Responsibility**: sign or annotate outgoing HTTP requests with the correct
credentials for the target ORB deployment.

> **Scope note**: this layer governs API-level authentication — who the SDK
> client is when it talks to ORB.  It is entirely orthogonal to the *provider
> layer* (which cloud platform ORB itself talks to when provisioning machines).
> A client can authenticate with a Bearer token while ORB provisions to AWS,
> or use SigV4 for an API-Gateway-fronted ORB deployment while ORB provisions
> to GCP.

**Strategy pattern** (conceptual):

```
// Three required built-in implementations; callers can add more.
AuthStrategy.None    → pass-through (no headers added)
AuthStrategy.Bearer  → adds "Authorization: Bearer <token>"
AuthStrategy.SigV4   → signs request with AWS Signature Version 4

attach(strategy AuthStrategy, transport Transport) → Transport
```

All five SDKs implement this via a transport/interceptor wrapper that accepts
an `AuthOption` (or `AuthStrategy`) value; see the per-language auth source
for the concrete type hierarchy.

**Built-in implementations**

| Strategy | Auth header | When to use |
|---|---|---|
| `None` | — | UDS/spawn mode, or open ORB endpoints |
| `Bearer` | `Authorization: Bearer <token>` | JWT / Cognito / API-key tokens |
| `SigV4` | AWS Signature V4 headers | ORB deployed behind AWS API Gateway |

**SigV4 requirements** (when the SigV4 strategy is used):
- Use the **language-native AWS SDK** for signing rather than a hand-rolled
  implementation wherever possible:
  - **Go**: hand-rolled (no AWS SDK dependency by design; keep the SDK dep-free).
  - **Java**: `software.amazon.awssdk:auth` — `AwsRequestSigner`.
  - **Kotlin**: same as Java.
  - **C#**: `Amazon.Runtime.AWSCredentials` + `AWS4Signer`.
  - **TypeScript**: `@aws-sdk/signature-v4` + `@aws-sdk/protocol-http`.
- Read credentials from the standard AWS credential chain
  (env vars → `~/.aws/credentials` → instance metadata) when no explicit
  credentials are supplied.
- Support static credentials AND dynamic credential providers (e.g.
  `AssumeRole`, instance metadata).
- The service name defaults to `"execute-api"` for AWS API Gateway-fronted
  deployments; allow override.
- Thread-safe credential refresh.

**Reference**: `sdk/go/internal/transport/sigv4.go`

**Custom auth extensibility**

All five SDKs expose an escape hatch so callers can implement custom auth
strategies (e.g. Azure Workload Identity, GCP service-account tokens, OIDC)
without modifying SDK source:

| SDK | Escape hatch |
|-----|-------------|
| **Java** | `AuthStrategy` is a `public interface` — implement it directly. This is the reference extensibility model. |
| **Go** | `WithCustomAuth(fn func(*http.Request) error) AuthOption` |
| **TypeScript** | `{ type: "custom"; provider: AuthProvider }` where `AuthProvider` exposes `apply(config)` |
| **Kotlin** | `AuthOption.Custom(interceptor: Interceptor)` variant of the sealed class |
| **C#** | `AuthOption.Custom(Func<HttpRequestMessage, CancellationToken, Task> signer)` |

---

### Layer 5: SSE Reader + Reconnect

**Responsibility**: consume a Server-Sent Events stream, parse `data:` frames,
and automatically reconnect with back-off when the connection is dropped.

**Interface** (conceptual):

```
SSEStream
  Next()  → (Event | nil, error)   // nil = stream closed normally
  Close() → void
  Err()   → error                  // set if stream closed with error
```

**Requirements**:
- Parse the SSE wire format (`data:`, `event:`, `id:`, `retry:` lines).
- Honour `retry:` directives from the server.
- Exponential back-off for reconnects; reset back-off after a successful
  event delivery.
- Send `Last-Event-ID` header on reconnect when an `id:` was received.
- Detect terminal sentinel events (ORB sends `data: {"sentinel": true}`) and
  close the stream without reconnecting.
- Respect `context` cancellation.
- Applicable endpoints:
  - `GET /api/v1/requests/{request_id}/stream` — request status stream.
  - `GET /api/v1/events/` — global event bus (text/event-stream).

**Reference**: `sdk/go/internal/sse/reader.go`, `sdk/go/orb/client.go`
(`runSSEProducer`, `consumeSSE`)

---

## Server-Side Auth Strategy vs. SDK Auth Option

The server's `iam` auth strategy and the SDK's `WithAWSSigV4` / `SigV4` auth
option solve different problems and operate at different layers.

**Server-side `iam` strategy** (`src/orb/providers/aws/auth/iam_strategy.py`):
authorizes requests by verifying the *server's own* ambient AWS identity (the
EC2 instance role, ECS task role, or environment credentials on the machine
running ORB).  It calls `sts:GetCallerIdentity` using the server's own
pre-initialized STS client.  The client's `Authorization` header is *not*
inspected.

**SDK `WithAWSSigV4` / `SigV4`**: signs the *outgoing* HTTP request with the
*client's* AWS credentials.  This is only validated when ORB is deployed
behind **AWS API Gateway** (which verifies the SigV4 signature before
forwarding the request to ORB).

Consequently:
- Direct client → ORB with server `iam` strategy: use `WithNoAuth` or
  `WithBearerToken` in the SDK; SigV4 headers are irrelevant.
- Client → API Gateway → ORB: use `WithAWSSigV4` / `SigV4` in the SDK;
  the server strategy can be `none` or `bearer_token` (API Gateway enforces
  the IAM boundary before the request reaches ORB).

---

## Scheduler / Remote Mode

The ORB client must support two operating modes:

| Mode | Transport | Auth | Notes |
|------|-----------|------|-------|
| **Spawn** | UDS socket | optional | Client starts ORB as a child process |
| **Remote** | TCP/HTTPS | Bearer / SigV4 | Client connects to an existing ORB instance |

For remote mode with HostFactory scheduler, the scheduler type is sent via the
`X-ORB-Scheduler` HTTP header on every request.  The server-side
`dependencies.py:get_request_scheduler` reads this header to select the
scheduler backend.

```
X-ORB-Scheduler: hostfactory
```

The legacy approach of embedding scheduler type in request body structs
(`SchedulerHostFactory` camelCase fields) is supported for backward
compatibility but new code should use the header mechanism.

---

## Code Generation

Generated code lives under `sdk/<lang>/generated/` (or
`sdk/go/internal/generated/` for Go, to keep generated models unexported from
the public API package).

### Generator invocation

```
make sdk-generate          # regenerate all languages
make sdk-go-generate       # regenerate Go only
make sdk-<lang>-generate   # regenerate one language
```

See `makefiles/sdk.mk` for the full invocation including the pinned
openapi-generator version (7.23.0).

### What is generated

- **Models** (`model_*.go`, `*Model.java`, etc.) — typed request/response
  structs matching the OpenAPI schema definitions exactly.
- **API stubs** — method signatures for each operation; most SDKs replace the
  stub bodies with calls to the hand-written transport.

### What is NOT generated

- Subprocess manager
- UDS transport
- Retry transport
- Auth transport (except boilerplate wiring)
- SSE reader
- Public `Client` struct and its constructor

### Exclusions

The following operations are excluded from codegen because they use
`text/event-stream` responses that generators cannot model correctly:

| Operation | Path | Notes |
|-----------|------|-------|
| `stream_events_api_v1_events__get` | `GET /api/v1/events/` | Global SSE event bus |

`GET /api/v1/requests/{request_id}/stream` returns `application/json`-framed
SSE data; it is included in the generated stubs but the method body is
replaced by the hand-written `StreamRequestStatus` implementation.

---

## Mandatory Test Contract

Every SDK must include two test suites:

### Unit tests

- Test each operation against a fake/mock HTTP server (in-process, no real ORB).
- Cover: happy path, 4xx error propagation (`ErrNotFound`, etc.),
  retry behaviour (mock 503 → mock 200), auth header presence, UDS dialling.
- Coverage target: ≥ 80% of client code.

### Contract tests (`integration` tag / `// +build integration`)

- Test against a real running ORB instance (spawned by the subprocess manager
  or pointed at an existing one via `ORB_TEST_URL` env var).
- Cover: ListTemplates, RequestMachines → WaitForCompletion, ReturnMachines,
  Health check.
- These tests are skipped in regular CI and run only when the `integration`
  build tag / test flag is supplied.

---

## Cross-SDK Naming Notes

### Subprocess Manager

The canonical class name is `SubprocessManager` (Java, Kotlin, TypeScript, C#).
Go's equivalent is `Manager` in `internal/process/` — it is intentionally
package-private and not exposed in the public API, so no renaming is required
for user-facing consistency.

### Error Hierarchy

The concrete HTTP-error class is named differently across SDKs due to language
conventions:

| SDK | Concrete HTTP-error class | Base class |
|-----|--------------------------|------------|
| Go | `APIError` (sentinel-var pattern) | — |
| TypeScript | `OrbApiError` | `OrbError` |
| Java | `OrbApiException` | `OrbError` |
| Kotlin | `OrbApiError` | `OrbError` |
| C# | `OrbApiException` | `OrbException` |

Every SDK exposes a single base type (`OrbError`, or `OrbException` in the .NET
idiom) that catches all SDK-originated failures, plus a concrete HTTP-error
class carrying the canonical field set (statusCode, code, message, requestId)
and typed sentinel subclasses for 401/403/404/409/503/408.  The C# name keeps
the `Exception` suffix per .NET convention; its unavailable sentinel is
`OrbUnavailableException` (extends `OrbApiException`, status 503).  Both Java and
Kotlin share the same Maven group (`org.finos.openresourcebroker`).

### Test Support Packages

Go provides a first-class `mock/` package (`mock/server.go`) that implements the
full ORB REST API as an in-process fake server.  This is intentional: Go's
`httptest` package is lower-level and the mock package offers richer test
scenario control (SSE disconnect simulation, per-request state).  Other SDKs
use language-native HTTP mocking instead:

| SDK | Mock approach |
|-----|--------------|
| Go | `mock.NewServer()` (see `sdk/go/mock/server.go`) |
| Java | `MockWebServer` (OkHttp) |
| Kotlin | `MockWebServer` (OkHttp) |
| TypeScript | `nock` / inline `http.createServer` |
| C# | `MockHttpMessageHandler` / `WireMock.Net` |

The Go `testutil/` package (`testutil/orb.go`) provides helpers for the
integration test suite and has no equivalent in other SDKs — this is also
intentional.

---

## Adding a New Language SDK

1. Create `sdk/<lang>/openapi-generator-config.yaml` following the pattern of
   the existing configs in this directory.
2. Add a `sdk-<lang>-generate` target in `makefiles/sdk.mk`.
3. Add `sdk/<lang>-generate` to the `sdk-generate` aggregate target.
4. Implement the five hand-written layers (see above).
5. Wire the generated models into the public client.
6. Add unit + contract tests.
7. Add the language to the matrix in `.github/workflows/sdk.yml` (the single
   consolidated SDK workflow).  The matrix job generates, builds, and tests all
   five languages in parallel; no per-language workflow file is needed.
