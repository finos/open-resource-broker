# orb-go

Go client SDK for the [Open Resource Broker](https://github.com/finos/open-resource-broker) (ORB) — a Python service for provisioning cloud compute resources.

## Prerequisites

ORB is a Python service. Install it before using managed-process mode:

```bash
# Recommended: uv tool install (permanent, single binary in PATH)
uv tool install 'orb-py>=1.6.2,<2.0.0'

# Or with pip
pip install 'orb-py>=1.6.2,<2.0.0'
```

Verify: `orb --version`

Then run one-time setup:

```bash
orb init
```

This creates `~/.config/orb/config.json` with your AWS credentials and infrastructure defaults (subnets, security groups, IAM roles). Skip this step if connecting to an existing ORB server.

## Installation

```bash
go get github.com/finos/open-resource-broker/sdk/go@v1.6.2
```

## IPC Model

ORB communicates over a **Unix domain socket** (UDS). The pattern is similar to [hashicorp/go-plugin](https://github.com/hashicorp/go-plugin): the SDK spawns the ORB subprocess, negotiates a socket path, and routes all HTTP traffic through the socket — no TCP port is allocated.

The subprocess is started with:

```
orb system serve --socket-path /tmp/orb-<pid>.sock
```

The SDK polls `/health` on the socket until ORB reports healthy (up to `StartTimeout`, default 30 s), then begins serving requests. On `Close()` the SDK sends SIGTERM and waits up to `StopTimeout` before SIGKILL.

## Deployment Models

### Model A — Managed subprocess (recommended)

The Go SDK starts ORB automatically via Unix domain socket. No port management, no separate process to run.

```go
import "github.com/finos/open-resource-broker/sdk/go/orb"

c, err := orb.NewClient(
    orb.WithManagedProcess(orb.ProcessConfig{
        Binary: "orb", // must be in PATH after pip/uv install
    }),
    orb.WithAuth(orb.WithNoAuth()),
)
if err != nil {
    log.Fatal(err)
}
defer c.Close() // stops the ORB subprocess
```

The SDK automatically:
- Generates a temp socket path (`/tmp/orb-<pid>.sock`)
- Starts `orb system serve --socket-path /tmp/orb-<pid>.sock`
- Waits up to 30 s for ORB to become healthy
- Routes all API calls through the socket
- Kills the process on `Close()`

### Model B — Connect to existing ORB server

```go
c, err := orb.NewClient(
    orb.WithBaseURL("http://localhost:8000"),
    orb.WithAuth(orb.WithBearerToken("my-token")),
)
```

### Model C — Connect via explicit Unix socket

Use this when you start ORB yourself but want UDS transport:

```go
c, err := orb.NewClient(
    orb.WithUnixSocket("/run/orb/orb.sock"),
    orb.WithAuth(orb.WithNoAuth()),
)
```

## Templates

Templates define what kind of machine to provision (instance type, AMI, region, etc.).

### Option 1: Generate via CLI (one-time)

```bash
orb templates generate        # generates example templates from your AWS config
orb templates create --file my_template.json  # registers with ORB
```

### Option 2: Create via Go SDK at runtime

```go
err := c.CreateTemplate(ctx, orb.CreateTemplateRequest{
    Name:     "gpu-worker",
    Provider: "aws",
    Config: map[string]any{
        "provider_api":        "EC2Fleet",
        "image_id":            "ami-12345678",
        "instance_type":       "g4dn.xlarge",
        "subnet_ids":          []string{"subnet-abc123"},
        "security_group_ids":  []string{"sg-xyz789"},
    },
})
```

## Usage

```go
// Request machines
mr, err := c.RequestMachines(ctx, orb.RequestMachinesRequest{
    TemplateID: "gpu-worker",
    Count:      4,
})

// Wait for completion (blocks until terminal status)
final, err := c.WaitForCompletion(ctx, mr.RequestID)
fmt.Printf("status=%s machines=%d\n", final.Status, len(final.Machines))

// Or stream events manually
stream, err := c.StreamRequest(ctx, mr.RequestID)
defer stream.Close()
for {
    event, ok := stream.Next()
    if !ok {
        break
    }
    fmt.Printf("status=%s\n", event.Status)
}
if err := stream.Err(); err != nil {
    log.Fatal(err)
}
```

## Scheduler

ORB supports multiple scheduler backends. The default uses snake_case JSON. The HostFactory scheduler uses camelCase JSON — set `WithScheduler` to match the server:

```go
c, err := orb.NewClient(
    orb.WithManagedProcess(orb.ProcessConfig{Binary: "orb"}),
    orb.WithScheduler(orb.SchedulerHostFactory),
    orb.WithAuth(orb.WithNoAuth()),
)
```

When `WithManagedProcess` and `WithScheduler(SchedulerHostFactory)` are both set, the SDK automatically appends `--scheduler hostfactory` to the subprocess arguments.

| Constant | Server flag | JSON style |
|---|---|---|
| `SchedulerDefault` | _(none)_ | snake_case |
| `SchedulerHostFactory` | `--scheduler hostfactory` | camelCase |

## Authentication

| Method | Option |
|---|---|
| None | `orb.WithNoAuth()` |
| Bearer token (static) | `orb.WithBearerToken("token")` |
| Bearer token (dynamic/refresh) | `orb.WithBearerTokenFunc(fn)` |
| AWS SigV4 | `orb.WithAWSSigV4(creds, region, service)` |

## Examples

Runnable examples are in `examples/`:

| Directory | Description |
|---|---|
| `examples/basic/` | Connect to a running ORB server, list templates, request machines, wait |
| `examples/managed_process/` | Managed subprocess via Unix socket |
| `examples/scheduler/` | HostFactory scheduler with manual SSE streaming |

```bash
# Run against a live server
go run ./examples/basic/ --url http://localhost:8000 --template <id>

# Managed process (orb must be in PATH)
go run ./examples/managed_process/ --template <id>

# HostFactory scheduler
go run ./examples/scheduler/ --template <id>
```

## Testing

Use `mock.NewServer()` — no real ORB process needed:

```go
import "github.com/finos/open-resource-broker/sdk/go/mock"

srv := mock.NewServer()
defer srv.Close()

srv.AddTemplate(orb.Template{TemplateID: "tmpl-1", Name: "test"})
srv.SetRequestStatus("req-1", "complete")

c, _ := srv.Client()
final, _ := c.WaitForCompletion(ctx, "req-1")
```

## Integration Tests

Requires `orb` binary in PATH and `orb init` completed:

```bash
go test -tags integration -timeout 120s ./orb/...
```

## Troubleshooting

**`orb: starting managed process: exec: "orb": executable file not found in $PATH`**
ORB is not installed or not in PATH. Run `pip install orb-py` or `uv tool install orb-py`, then verify with `orb --version`.

**`orb: starting managed process: timed out waiting for healthy`**
ORB started but did not become healthy within `StartTimeout` (default 30 s). Check that `orb init` has been run and AWS credentials are valid. Increase `StartTimeout` in `ProcessConfig` if your environment is slow.

**`connect: permission denied` on the Unix socket**
The socket path is not writable by the current user. Either use the default auto-generated path in `/tmp`, or set `ProcessConfig.SocketPath` to a path your process owns.

**Scheduler mismatch: empty machines or wrong field names**
The `WithScheduler` option must match the `--scheduler` flag the ORB server was started with. If ORB is running with `--scheduler hostfactory`, set `orb.WithScheduler(orb.SchedulerHostFactory)` on the client.

## Version Compatibility

See [COMPATIBILITY.md](COMPATIBILITY.md).

| Go SDK | Requires Python service |
|---|---|
| v1.6.2 | >= 1.6.2 |
| v2.x | >= 2.0.0 |
