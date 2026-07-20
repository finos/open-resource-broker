# orb-typescript

TypeScript / Node.js client SDK for the
[Open Resource Broker](https://github.com/finos/open-resource-broker) (ORB).

## Prerequisites

ORB is a Python service. Install it before using managed-process mode:

```bash
# Recommended
uv tool install 'orb-py>=1.8.3,<2.0.0'

# Or with pip
pip install 'orb-py>=1.8.3,<2.0.0'
```

Verify: `orb --version`

Then run one-time setup (skip if connecting to an existing server):

```bash
orb init
```

## Installation

```bash
npm install @finos/open-resource-broker
```

Requires Node 18 or later.

## IPC Model

The SDK communicates with ORB over a **Unix domain socket** (UDS). When
configured for managed-process mode the SDK spawns ORB as a child process,
negotiates a socket path, and routes all HTTP traffic through the socket — no
TCP port is allocated.

## Operating Modes

### Spawn mode (recommended)

The SDK starts ORB automatically and manages its lifecycle:

```typescript
import { OrbClient } from "@finos/open-resource-broker";

const client = await OrbClient.create({
    process: { binary: "orb" },
    auth: { type: "none" },
});
// ... use client ...
await client.close(); // sends SIGTERM, waits, then SIGKILL
```

### Remote mode

Connect to a running ORB server over HTTP/HTTPS:

```typescript
const client = await OrbClient.create({
    baseUrl: "https://orb.example.com",
    auth: { type: "bearer", token: "my-token" },
});
```

### UDS mode (explicit socket)

Connect to an existing ORB process via a UNIX socket you started yourself:

```typescript
const client = await OrbClient.create({
    socketPath: "/run/orb/orb.sock",
    auth: { type: "none" },
});
```

## Usage

```typescript
import { OrbClient } from "@finos/open-resource-broker";

const client = await OrbClient.create({
    process: { binary: "orb" },
    auth: { type: "none" },
});

try {
    // List templates
    const { templates } = await client.listTemplates();
    if (!templates.length) {
        console.log("No templates registered");
        return;
    }

    // Request machines
    const op = await client.requestMachines({
        templateId: templates[0].templateId,
        count: 2,
    });
    console.log(`Request submitted: ${op.requestId}`);

    // Wait for completion (blocks until terminal status)
    const final = await client.waitForCompletion(op.requestId);
    console.log(`Status: ${final.status}, machines: ${final.machines.length}`);

    // Return machines
    await client.returnMachines({
        machineIds: final.machines.map(m => m.machineId),
    });
} finally {
    await client.close();
}
```

## Authentication

| Mode | Config |
|------|--------|
| None | `{ type: "none" }` |
| Bearer token | `{ type: "bearer", token: "my-token" }` |
| AWS SigV4 | `{ type: "sigv4", region: "us-east-1", service: "execute-api" }` |

## Scheduler (HostFactory mode)

```typescript
const client = await OrbClient.create({
    process: { binary: "orb" },
    scheduler: "hostfactory",
    auth: { type: "none" },
});
```

When `scheduler: "hostfactory"` is set, the SDK appends
`X-ORB-Scheduler: hostfactory` to every request header.

## Streaming

```typescript
// Iterate events as they arrive
for await (const event of client.streamRequestStatus(requestId)) {
    console.log(`${event.status} — ${event.machines.length} machines`);
}

// Or collect the terminal event
const final = await client.waitForCompletion(requestId, {
    timeoutSeconds: 600,
});
```

## Error Handling

```typescript
import { OrbApiError, OrbNotFoundError } from "@finos/open-resource-broker";

try {
    const template = await client.getTemplate("unknown");
} catch (err) {
    if (err instanceof OrbNotFoundError) {
        console.log("Template not found");
    } else if (err instanceof OrbApiError) {
        console.log(`HTTP ${err.statusCode}: ${err.message}`);
    }
}
```

## Testing

Use the built-in mock server for unit tests (no real ORB required):

```typescript
import { OrbClient } from "@finos/open-resource-broker";
// See sdk/typescript/tests/unit/ for mock-server patterns
```

## Integration Tests

Requires `orb` in PATH and `orb init` completed:

```bash
npm run test:contract
```

## Version Compatibility

| TypeScript SDK | Requires Python service |
|----------------|------------------------|
| 0.1.x | >= 1.8.3 |
