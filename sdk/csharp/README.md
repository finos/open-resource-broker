# orb-csharp

.NET / C# client SDK for the
[Open Resource Broker](https://github.com/finos/open-resource-broker) (ORB).

## Prerequisites

ORB is a Python service. Install it before using managed-process mode:

```bash
uv tool install 'orb-py>=1.6.2,<2.0.0'
# or: pip install 'orb-py>=1.6.2,<2.0.0'
```

Verify: `orb --version`

Then run one-time setup (skip if connecting to an existing server):

```bash
orb init
```

## Requirements

- .NET 8 or later
- Linux or macOS (Unix domain socket support is built-in on .NET 8)

## Installation

```bash
dotnet add package FINOS.OpenResourceBroker
```

Or add to your `.csproj`:

```xml
<PackageReference Include="FINOS.OpenResourceBroker" Version="0.1.0" />
```

## IPC Model

The SDK communicates with ORB over a **Unix domain socket** (UDS) via
`SocketsHttpHandler.ConnectCallback`. When configured for managed-process mode
the SDK spawns ORB as a child process and routes all HTTP traffic through the
socket — no TCP port is allocated.

## Operating Modes

### Spawn mode (recommended)

```csharp
using FINOS.OpenResourceBroker;

await using var client = await OrbClient.CreateAsync(new ClientConfig
{
    Process = new ProcessConfig { Binary = "orb" },
});
// use client ...
// IAsyncDisposable: DisposeAsync sends SIGTERM, waits, then SIGKILL
```

### Remote mode

```csharp
await using var client = await OrbClient.CreateAsync(new ClientConfig
{
    BaseUrl = "https://orb.example.com",
    Auth = AuthOption.Bearer("my-token"),
});
```

### UDS mode (explicit socket)

```csharp
await using var client = await OrbClient.CreateAsync(new ClientConfig
{
    SocketPath = "/run/orb/orb.sock",
});
```

## Usage

```csharp
using FINOS.OpenResourceBroker;
using FINOS.OpenResourceBroker.Models;

await using var client = await OrbClient.CreateAsync(new ClientConfig
{
    Process = new ProcessConfig { Binary = "orb" },
});

// List templates
var templates = await client.ListTemplatesAsync();
if (templates.Templates.Count == 0)
{
    Console.WriteLine("No templates registered");
    return;
}

// Request machines
var op = await client.RequestMachinesAsync(new RequestMachinesRequest
{
    TemplateId = templates.Templates[0].TemplateId,
    Count = 2,
});
Console.WriteLine($"Request submitted: {op.RequestId}");

// Wait for completion (streams SSE until terminal status)
var final = await client.WaitForCompletionAsync(op.RequestId);
Console.WriteLine($"Status: {final.Status}, machines: {final.Machines.Count}");

// Return machines
var machineIds = final.Machines.Select(m => m.MachineId).ToList();
if (machineIds.Count > 0)
{
    await client.ReturnMachinesAsync(new ReturnMachinesRequest
    {
        MachineIds = machineIds,
    });
}
```

## Authentication

| Mode | `AuthOption` |
|------|-------------|
| None | `AuthOption.None` |
| Bearer token | `AuthOption.Bearer("token")` |
| AWS SigV4 | `AuthOption.SigV4(region: "us-east-1", service: "execute-api")` |

```csharp
var client = await OrbClient.CreateAsync(new ClientConfig
{
    BaseUrl = "https://orb.example.com",
    Auth = AuthOption.Bearer("my-token"),
});
```

## Scheduler (HostFactory mode)

```csharp
var client = await OrbClient.CreateAsync(new ClientConfig
{
    Process = new ProcessConfig { Binary = "orb" },
    Scheduler = "hostfactory",
});
```

When `Scheduler = "hostfactory"` is set, the SDK appends
`X-ORB-Scheduler: hostfactory` to every request header.

## Streaming

```csharp
// IAsyncEnumerable-based SSE stream with reconnect
await foreach (var ev in client.StreamRequestStatusAsync(requestId))
{
    Console.WriteLine(ev.Status);
}

// Convenience — blocks until terminal status
var final = await client.WaitForCompletionAsync(requestId, timeoutSeconds: 600);
```

## Error Handling

```csharp
using FINOS.OpenResourceBroker;

try
{
    await client.GetTemplateAsync("unknown");
}
catch (OrbApiException ex) when (ex.StatusCode == 404)
{
    Console.WriteLine("Template not found");
}
catch (OrbApiException ex)
{
    Console.WriteLine($"HTTP {ex.StatusCode}: {ex.Message}");
}
```

## Building

```bash
cd sdk/csharp
dotnet build
```

## Tests

```bash
dotnet test
```

Integration tests (require `orb` in PATH):

```bash
dotnet test --filter Category=Integration
```

## Version Compatibility

| .NET SDK | Requires Python service |
|----------|------------------------|
| 0.1.x | >= 1.6.2 |
