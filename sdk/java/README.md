# orb-java

Java client SDK for the
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

- Java 17 or later
- JVM with Unix domain socket support (standard since Java 16)

## Installation

Gradle:

```groovy
implementation 'org.finos.openresourcebroker:open-resource-broker-java:0.1.0'
```

Maven:

```xml
<dependency>
  <groupId>org.finos.openresourcebroker</groupId>
  <artifactId>open-resource-broker-java</artifactId>
  <version>0.1.0</version>
</dependency>
```

## IPC Model

The SDK communicates with ORB over a **Unix domain socket** (UDS). When
configured for managed-process mode the SDK spawns ORB as a child process,
negotiates a socket path, and routes all HTTP traffic through the socket — no
TCP port is allocated.

## Operating Modes

### Spawn mode (recommended)

```java
import org.finos.openresourcebroker.sdk.client.OrbClient;
import org.finos.openresourcebroker.sdk.process.ProcessConfig;

var proc = ProcessConfig.builder()
    .binary("orb")
    .startTimeout(java.time.Duration.ofSeconds(30))
    .build();

try (var client = OrbClient.builder().process(proc).build()) {
    // use client
}
```

### Remote mode

```java
var client = OrbClient.builder()
    .baseUrl("https://orb.example.com")
    .auth(new BearerTokenAuth("my-token"))
    .build();
```

## Usage

```java
import org.finos.openresourcebroker.sdk.client.OrbClient;
import org.finos.openresourcebroker.sdk.model.*;
import org.finos.openresourcebroker.sdk.process.ProcessConfig;
import java.util.List;

var proc = ProcessConfig.builder().binary("orb").build();

try (var client = OrbClient.builder().process(proc).build()) {
    // List templates
    TemplateListResponse templates = client.listTemplates();
    if (templates.getTemplates().isEmpty()) {
        System.out.println("No templates registered");
        return;
    }

    // Request machines
    String templateId = templates.getTemplates().get(0).getTemplateId();
    RequestOperationResponse op = client.requestMachines(
        new RequestMachinesRequest().templateId(templateId).count(2)
    );
    System.out.println("Request submitted: " + op.getRequestId());

    // Wait for completion using the SSE stream
    List<String> machineIds = new java.util.ArrayList<>();
    client.streamRequestStatus(op.getRequestId(), null, null, event -> {
        System.out.println("Status: " + event.getStatus());
        if (event.getMachines() != null) {
            event.getMachines().forEach(m -> machineIds.add(m.getMachineId()));
        }
    });

    // Return machines
    if (!machineIds.isEmpty()) {
        client.returnMachines(new ReturnMachinesRequest().machineIds(machineIds));
    }
}
```

## Authentication

| Mode | Class |
|------|-------|
| None | `AuthStrategy.NONE` |
| Bearer token (static) | `new BearerTokenAuth("token")` |
| Bearer token (refreshing) | `new BearerTokenAuth(() -> currentToken())` |
| AWS SigV4 | `new AwsSigV4Auth(region, service)` |

Bearer and SigV4 credentials are resolved on **every** request, so a refreshing
token supplier or a rotating credential chain takes effect immediately (no stale
token is frozen at build time). SigV4 signs all requests, including SSE streams.

Remote mode over `https://` connects with TLS (certificate + hostname
verification, default port 443); `http://` is plaintext for local development
only. Bearer tokens and SigV4 headers are never sent over a plaintext link when
the base URL is `https://`.

Configure via the builder:

```java
OrbClient.builder()
    .baseUrl("https://orb.example.com")
    .auth(new BearerTokenAuth("my-token"))
    .build();
```

## Scheduler (HostFactory mode)

```java
OrbClient.builder()
    .process(proc)
    .scheduler("hostfactory")
    .build();
```

When `scheduler("hostfactory")` is set, the SDK appends
`X-ORB-Scheduler: hostfactory` to every request header.

## Streaming

```java
// Callback-based SSE stream with reconnect
client.streamRequestStatus(
    requestId,
    2.0,   // intervalSecs
    300.0, // timeoutSecs
    event -> System.out.println(event.getStatus())
);
```

## Error Handling

```java
import org.finos.openresourcebroker.sdk.client.OrbApiException;

try {
    client.getTemplate("unknown");
} catch (OrbApiException e) {
    System.out.println("HTTP " + e.getStatusCode() + ": " + e.getMessage());
}
```

## Building

```bash
cd sdk/java
./gradlew build
```

## Integration Tests

Requires `orb` in PATH:

```bash
./gradlew integrationTest
```

## Version Compatibility

| Java SDK | Requires Python service |
|----------|------------------------|
| 0.1.x | >= 1.6.2 |
